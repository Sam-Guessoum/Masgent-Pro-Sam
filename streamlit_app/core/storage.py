"""Workspace storage: run folders (source of truth) + SQLite index (rebuildable).

Layout::

    workspace/
      index.db
      projects/<slug>/project.json
      projects/<slug>/runs/<YYYYMMDD-HHMMSS>_<tool>_<shortid>/
          meta.json inputs.json result.json logs.txt env.json manifest.sha256 files/
      uploads/<sha16>/<name>
      exports/
      .trash/<run folder>
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from core.paths import UnsafePathError, safe_filename, safe_join, slugify

#: Files that must never be shown or exported (VASP pseudopotentials are licensed).
LICENSED_NAMES = ("POTCAR",)


def is_licensed(path: Path | str) -> bool:
    name = Path(path).name
    return any(name == n or (name.startswith(n) and not name.endswith(".spec")) for n in LICENSED_NAMES)


# ----------------------------------------------------------------- atomic I/O
def atomic_write_bytes(path: Path, data: bytes, retries: int = 8) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    for i in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: target briefly open by a reader
            time.sleep(0.05 * (i + 1))
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------- records
TERMINAL = {"succeeded", "failed", "cancelled"}
ACTIVE = {"queued", "running"}


@dataclass
class RunRecord:
    run_id: str
    path: Path
    meta: dict

    @property
    def files_dir(self) -> Path:
        return self.path / "files"

    @property
    def status(self) -> str:
        return self.meta.get("status", "unknown")

    def inputs(self) -> dict:
        return read_json(self.path / "inputs.json", {}) or {}

    def result(self) -> dict | None:
        return read_json(self.path / "result.json", None)

    def env(self) -> dict:
        return read_json(self.path / "env.json", {}) or {}

    def log_text(self, max_bytes: int = 400_000) -> str:
        p = self.path / "logs.txt"
        if not p.exists():
            return ""
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", errors="replace")

    def output_files(self, include_inputs: bool = False) -> list[Path]:
        if not self.files_dir.exists():
            return []
        out = []
        for p in sorted(self.files_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.files_dir).as_posix()
            if not include_inputs and rel.startswith("inputs/"):
                continue
            out.append(p)
        return out


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, project TEXT, tool TEXT, tool_title TEXT, status TEXT,
  created TEXT, started TEXT, ended TEXT, duration_s REAL, label TEXT, tags TEXT,
  starred INTEGER DEFAULT 0, notes TEXT, origin TEXT, parent_run TEXT, device TEXT,
  path TEXT, summary TEXT, trashed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_project ON runs(project);
CREATE INDEX IF NOT EXISTS runs_tool ON runs(tool);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created);
CREATE TABLE IF NOT EXISTS projects (
  slug TEXT PRIMARY KEY, name TEXT, description TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS presets (
  tool TEXT, name TEXT, params TEXT, created TEXT, PRIMARY KEY (tool, name)
);
"""

_RUN_COLS = [
    "run_id", "project", "tool", "tool_title", "status", "created", "started", "ended",
    "duration_s", "label", "tags", "starred", "notes", "origin", "parent_run", "device",
    "path", "summary", "trashed",
]


class Storage:
    """All persistence for the app. Thread-safe; safe across Streamlit sessions."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        for d in ("projects", "uploads", "exports", ".trash", "cache"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "index.db"
        self._lock = threading.RLock()
        c = self._conn()  # note: sqlite3's context manager commits but does NOT close
        try:
            c.executescript(_SCHEMA)
            c.commit()
        finally:
            c.close()
        self.ensure_project("default", "Default project")

    # ------------------------------------------------------------- sqlite
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=15, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        return c

    def _exec(self, sql: str, args: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            c = self._conn()
            try:
                cur = c.execute(sql, tuple(args))
                rows = cur.fetchall()
                c.commit()
                return rows
            finally:
                c.close()

    # ------------------------------------------------------------ projects
    def project_dir(self, slug: str) -> Path:
        return safe_join(self.root / "projects", slugify(slug))

    def ensure_project(self, name: str, description: str = "") -> str:
        slug = slugify(name)
        pdir = self.project_dir(slug)
        (pdir / "runs").mkdir(parents=True, exist_ok=True)
        pj = pdir / "project.json"
        if not pj.exists():
            atomic_write_json(pj, {"slug": slug, "name": name, "description": description, "created": now_iso()})
        info = read_json(pj, {}) or {}
        self._exec(
            "INSERT OR REPLACE INTO projects(slug,name,description,created) VALUES (?,?,?,?)",
            (slug, info.get("name", name), info.get("description", ""), info.get("created", now_iso())),
        )
        return slug

    def update_project(self, slug: str, name: str | None = None, description: str | None = None) -> None:
        pj = self.project_dir(slug) / "project.json"
        info = read_json(pj, {}) or {"slug": slug, "created": now_iso()}
        if name is not None:
            info["name"] = name
        if description is not None:
            info["description"] = description
        atomic_write_json(pj, info)
        self.ensure_project(slug)

    def list_projects(self) -> list[dict]:
        rows = self._exec(
            "SELECT p.slug, p.name, p.description, p.created, "
            "(SELECT COUNT(*) FROM runs r WHERE r.project=p.slug AND r.trashed=0) AS n_runs "
            "FROM projects p ORDER BY p.created"
        )
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- runs
    def create_run(self, tool: str, project: str, meta: dict) -> RunRecord:
        slug = self.ensure_project(project) if project else "default"
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        short = uuid.uuid4().hex[:6]
        run_id = f"{ts}_{safe_filename(tool)[:40]}_{short}"
        path = self.project_dir(slug) / "runs" / run_id
        (path / "files").mkdir(parents=True, exist_ok=False)
        base = {
            "run_id": run_id, "tool": tool, "project": slug, "status": "queued",
            "created": now_iso(), "started": None, "ended": None, "duration_s": None,
            "label": "", "tags": [], "starred": False, "notes": "", "origin": "ui",
            "parent_run": None, "device": "cpu", "error": None,
        }
        base.update(meta or {})
        base["run_id"], base["project"] = run_id, slug
        atomic_write_json(path / "meta.json", base)
        self.index_meta(base, path)
        return RunRecord(run_id, path, base)

    def index_meta(self, meta: dict, path: Path, trashed: bool = False) -> None:
        row = {c: meta.get(c) for c in _RUN_COLS}
        row["tags"] = json.dumps(meta.get("tags") or [])
        row["starred"] = 1 if meta.get("starred") else 0
        row["path"] = str(path)
        row["summary"] = json.dumps(meta.get("summary") or {}, default=str)[:4000]
        row["trashed"] = 1 if trashed else 0
        cols = ",".join(_RUN_COLS)
        qs = ",".join("?" for _ in _RUN_COLS)
        self._exec(f"INSERT OR REPLACE INTO runs({cols}) VALUES ({qs})", [row[c] for c in _RUN_COLS])

    def _locate(self, run_id: str) -> Path | None:
        rows = self._exec("SELECT path FROM runs WHERE run_id=?", (run_id,))
        if rows and rows[0]["path"] and Path(rows[0]["path"]).exists():
            return Path(rows[0]["path"])
        safe_filename(run_id)  # validate
        for p in (self.root / "projects").glob(f"*/runs/{run_id}"):
            return p
        t = self.root / ".trash" / run_id
        return t if t.exists() else None

    def get_run(self, run_id: str) -> RunRecord | None:
        path = self._locate(run_id)
        if not path:
            return None
        meta = read_json(path / "meta.json", None)
        return RunRecord(run_id, path, meta) if meta else None

    def update_meta(self, run_id: str, **fields: Any) -> dict:
        with self._lock:
            rec = self.get_run(run_id)
            if rec is None:
                raise KeyError(run_id)
            meta = read_json(rec.path / "meta.json", {}) or {}
            meta.update(fields)
            atomic_write_json(rec.path / "meta.json", meta)
            self.index_meta(meta, rec.path, trashed=rec.path.parent.name == ".trash")
            return meta

    def query_runs(
        self, search: str = "", project: str | None = None, tools: list[str] | None = None,
        statuses: list[str] | None = None, date_from: str | None = None, date_to: str | None = None,
        tags: list[str] | None = None, starred: bool | None = None, trashed: bool = False,
        order: str = "created DESC", limit: int = 50, offset: int = 0,
    ) -> tuple[list[dict], int]:
        where, args = ["trashed=?"], [1 if trashed else 0]
        if search:
            where.append("(run_id LIKE ? OR tool LIKE ? OR tool_title LIKE ? OR label LIKE ? OR notes LIKE ? OR tags LIKE ? OR summary LIKE ?)")
            args += [f"%{search}%"] * 7
        if project:
            where.append("project=?"); args.append(project)
        if tools:
            where.append(f"tool IN ({','.join('?' * len(tools))})"); args += tools
        if statuses:
            where.append(f"status IN ({','.join('?' * len(statuses))})"); args += statuses
        if date_from:
            where.append("created>=?"); args.append(date_from)
        if date_to:
            where.append("created<=?"); args.append(date_to + "T23:59:59" if len(date_to) == 10 else date_to)
        if starred is not None:
            where.append("starred=?"); args.append(1 if starred else 0)
        for t in tags or []:
            where.append("tags LIKE ?"); args.append(f'%"{t}"%')
        allowed = {"created DESC", "created ASC", "duration_s DESC", "tool ASC", "status ASC", "label ASC"}
        order = order if order in allowed else "created DESC"
        w = " AND ".join(where)
        total = self._exec(f"SELECT COUNT(*) AS n FROM runs WHERE {w}", args)[0]["n"]
        rows = self._exec(f"SELECT * FROM runs WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?", args + [int(limit), int(offset)])
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            d["starred"] = bool(d.get("starred"))
            out.append(d)
        return out, int(total)

    def all_tags(self) -> list[str]:
        tags: set[str] = set()
        for r in self._exec("SELECT tags FROM runs WHERE trashed=0"):
            tags.update(json.loads(r["tags"] or "[]"))
        return sorted(tags)

    def distinct_tools(self) -> list[str]:
        return [r["tool"] for r in self._exec("SELECT DISTINCT tool FROM runs ORDER BY tool")]

    # ------------------------------------------------------------- reindex
    def reindex(self, live_run_ids: set[str] | None = None) -> dict:
        """Rebuild index.db from the folders. Stale active runs → failed."""
        live = live_run_ids or set()
        with self._lock:
            self._exec("DELETE FROM runs")
            n, fixed = 0, 0
            for pj in sorted((self.root / "projects").glob("*/project.json")):
                self.ensure_project(pj.parent.name)
            for meta_path in sorted(self.root.glob("projects/*/runs/*/meta.json")) + sorted(self.root.glob(".trash/*/meta.json")):
                meta = read_json(meta_path, None)
                if not meta or "run_id" not in meta:
                    continue
                if meta.get("status") in ACTIVE and meta["run_id"] not in live:
                    meta["status"] = "failed"
                    meta["error"] = meta.get("error") or "Interrupted: the app or worker stopped before this run finished."
                    meta["ended"] = meta.get("ended") or now_iso()
                    atomic_write_json(meta_path, meta)
                    fixed += 1
                self.index_meta(meta, meta_path.parent, trashed=meta_path.parent.parent.name == ".trash")
                n += 1
            return {"runs": n, "interrupted_fixed": fixed}

    # --------------------------------------------------------------- trash
    def trash_run(self, run_id: str) -> None:
        rec = self.get_run(run_id)
        if not rec or rec.path.parent.name == ".trash":
            return
        if rec.status in ACTIVE:
            raise RuntimeError("Cancel the run before deleting it.")
        dest = self.root / ".trash" / rec.path.name
        meta = dict(rec.meta, trashed_from=str(rec.path.relative_to(self.root)), trashed_at=now_iso())
        atomic_write_json(rec.path / "meta.json", meta)
        shutil.move(str(rec.path), str(dest))
        self.index_meta(meta, dest, trashed=True)

    def restore_run(self, run_id: str) -> None:
        rec = self.get_run(run_id)
        if not rec or rec.path.parent.name != ".trash":
            return
        origin = rec.meta.get("trashed_from") or f"projects/{rec.meta.get('project', 'default')}/runs/{rec.path.name}"
        dest = safe_join(self.root, origin)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(rec.path), str(dest))
        meta = {k: v for k, v in rec.meta.items() if k not in ("trashed_from", "trashed_at")}
        atomic_write_json(dest / "meta.json", meta)
        self.index_meta(meta, dest, trashed=False)

    def purge_trash(self, run_ids: list[str] | None = None) -> int:
        n = 0
        for p in list((self.root / ".trash").iterdir()):
            if run_ids is not None and p.name not in run_ids:
                continue
            shutil.rmtree(p, ignore_errors=True)
            self._exec("DELETE FROM runs WHERE run_id=?", (p.name,))
            n += 1
        return n

    # ---------------------------------------------------------- manifests
    def write_manifest(self, run_dir: Path) -> int:
        lines = []
        for p in sorted(run_dir.rglob("*")):
            if p.is_file() and p.name != "manifest.sha256" and not p.name.endswith(".tmp"):
                lines.append(f"{sha256_file(p)}  {p.relative_to(run_dir).as_posix()}")
        atomic_write_text(run_dir / "manifest.sha256", "\n".join(lines) + "\n")
        return len(lines)

    @staticmethod
    def verify_manifest(run_dir: Path) -> list[str]:
        """Return relative paths whose hash no longer matches (empty = intact)."""
        bad = []
        mf = run_dir / "manifest.sha256"
        if not mf.exists():
            return ["manifest.sha256 (missing)"]
        for line in mf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, rel = line.split("  ", 1)
            if rel in ("meta.json",):  # meta is edited by the user (labels, tags)
                continue
            p = run_dir / rel
            if not p.exists() or sha256_file(p) != digest:
                bad.append(rel)
        return bad

    # ------------------------------------------------------------- uploads
    def save_upload(self, name: str, data: bytes, max_mb: int = 200) -> Path:
        if len(data) > max_mb * 1024 * 1024:
            raise ValueError(f"File is larger than the {max_mb} MB limit.")
        digest = hashlib.sha256(data).hexdigest()[:16]
        dest = safe_join(self.root / "uploads", digest, safe_filename(name))
        if not dest.exists():
            atomic_write_bytes(dest, data)
        return dest

    def list_uploads(self) -> list[Path]:
        return sorted((p for p in (self.root / "uploads").rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)

    # ---------------------------------------------------------------- zips
    @staticmethod
    def safe_extract_zip(src: bytes | Path, dest: Path, max_total_mb: int = 500, max_files: int = 20000) -> list[Path]:
        """Extract a zip refusing absolute paths, traversal (zip-slip), links and bombs."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        zf = zipfile.ZipFile(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
        infos = zf.infolist()
        if len(infos) > max_files:
            raise ValueError(f"Archive has too many entries ({len(infos)} > {max_files}).")
        total = sum(i.file_size for i in infos)
        if total > max_total_mb * 1024 * 1024:
            raise ValueError(f"Archive expands to {total / 1e6:.0f} MB (limit {max_total_mb} MB).")
        out = []
        for info in infos:
            name = info.filename
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise UnsafePathError(f"Symlink in archive not allowed: {name}")
            target = safe_join(dest, name)  # raises on traversal / absolute
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as srcf, open(target, "wb") as dstf:
                shutil.copyfileobj(srcf, dstf)
            out.append(target)
        return out

    def export_run_zip(self, run_id: str) -> tuple[Path, list[str]]:
        rec = self.get_run(run_id)
        if not rec:
            raise KeyError(run_id)
        dest = self.root / "exports" / f"{run_id}.zip"
        skipped = _zip_dir(rec.path, dest, prefix=run_id)
        return dest, skipped

    def export_project_zip(self, slug: str) -> tuple[Path, list[str]]:
        pdir = self.project_dir(slug)
        dest = self.root / "exports" / f"project_{slug}_{dt.datetime.now():%Y%m%d-%H%M%S}.zip"
        skipped = _zip_dir(pdir, dest, prefix=slug)
        return dest, skipped

    # ------------------------------------------------------------- presets
    def save_preset(self, tool: str, name: str, params: dict) -> None:
        self._exec("INSERT OR REPLACE INTO presets(tool,name,params,created) VALUES (?,?,?,?)",
                   (tool, name, json.dumps(params, default=str), now_iso()))

    def list_presets(self, tool: str) -> dict[str, dict]:
        return {r["name"]: json.loads(r["params"]) for r in self._exec("SELECT name, params FROM presets WHERE tool=? ORDER BY name", (tool,))}

    def delete_preset(self, tool: str, name: str) -> None:
        self._exec("DELETE FROM presets WHERE tool=? AND name=?", (tool, name))

    # ---------------------------------------------------------------- misc
    def usage_bytes(self) -> int:
        total = 0
        for p in self.root.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return total


def _zip_dir(src: Path, dest: Path, prefix: str) -> list[str]:
    """Zip ``src`` into ``dest`` excluding licensed POTCAR files; returns skipped names."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    tmp = dest.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if not p.is_file() or p.name.endswith(".tmp"):
                continue
            rel = p.relative_to(src).as_posix()
            if is_licensed(p):
                skipped.append(rel)
                continue
            zf.write(p, f"{prefix}/{rel}")
        if skipped:
            zf.writestr(f"{prefix}/POTCAR_EXCLUDED.txt",
                        "POTCAR files are licensed by the VASP group and were excluded from this archive:\n"
                        + "\n".join(skipped) + "\n\nRe-create them on your cluster from your licensed POTCAR library.\n")
    os.replace(tmp, dest)
    return skipped
