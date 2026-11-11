"""Run manager: queue, warm worker pool, cancellation and bookkeeping.

* Every execution is a ``Run`` with its own folder (see ``core.storage``).
* Tools execute in separate **processes** (``core/worker.py``) so the UI never
  freezes, a crash cannot kill the app, and cancellation is a process kill.
* One spare worker is kept pre-warmed (Masgent already imported).
* Concurrency is configurable; GPU jobs are serialised.
* After an app restart, runs whose worker is still alive are *adopted*; runs
  that finished while the app was down are recovered from ``result.json``.
"""
from __future__ import annotations

import importlib.metadata as md
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import weakref
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core import examples
from core.paths import APP_DIR, RUNS_DIR_PLACEHOLDER, is_within
from core.settings import SECRET_KEYS, get_secret, load_settings, redact, scrub_params
from core.storage import ACTIVE, Storage, atomic_write_json, now_iso, read_json

WORKER = APP_DIR / "core" / "worker.py"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Parameters holding file/folder paths that must be staged into the run.
PATH_KINDS = {"structure", "file", "csv", "folder"}

_VERSION_PKGS = ["masgent", "pymatgen", "ase", "numpy", "pandas", "scipy", "torch", "chgnet", "sevenn",
                 "orb-models", "mattersim", "icet", "optuna", "scikit-learn", "pydantic", "pydantic-ai", "mp-api", "streamlit"]
_versions_cache: dict[str, str] | None = None


def package_versions() -> dict[str, str]:
    global _versions_cache
    if _versions_cache is None:
        out = {}
        for p in _VERSION_PKGS:
            try:
                out[p] = md.version(p)
            except md.PackageNotFoundError:
                out[p] = "not installed"
        _versions_cache = out
    return dict(_versions_cache)


def env_snapshot(device: str, seed: int) -> dict:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "device": device,
        "seed": seed,
        "packages": package_versions(),
        "app": "masgent-streamlit 1.0",
    }


@dataclass
class Job:
    run_id: str
    run_dir: Path
    func: str
    params: dict
    session_dir: Path
    device: str
    seed: int
    hooks: list[str]
    env: dict[str, str | None]
    proc: subprocess.Popen | None = None
    started: float = 0.0
    cancelled: bool = False
    snapshot: dict[str, float] = field(default_factory=dict)
    adopted_pid: int | None = None
    done: threading.Event = field(default_factory=threading.Event)

    @property
    def gpu(self) -> bool:
        return self.device == "cuda"


def _kill_tree(pid: int) -> None:
    try:
        import psutil

        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except psutil.Error:
                pass
        parent.kill()
    except Exception:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


def _pid_is_worker(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        import psutil

        p = psutil.Process(pid)
        return p.is_running() and any("worker.py" in c for c in p.cmdline())
    except Exception:
        return False


class RunManager:
    #: every live manager (tests use this to shut them all down)
    instances: "weakref.WeakSet[RunManager]"

    def __init__(self, storage: Storage, max_concurrent: int | None = None, warm_spares: int | None = None,
                 python: str | None = None, autostart: bool = True):
        self.storage = storage
        self._max = max_concurrent
        self.warm_spares = int(os.environ.get("MASGENT_UI_WARM_SPARES", 1)) if warm_spares is None else warm_spares
        RunManager.instances.add(self)
        self.python = python or sys.executable
        self._lock = threading.RLock()
        self._queue: deque[Job] = deque()
        self._running: dict[str, Job] = {}
        self._jobs: dict[str, Job] = {}
        self._spares: list[subprocess.Popen] = []
        self._listeners: list[Callable[[str, dict], None]] = []
        self._then: dict[str, list[Callable[[Any], None]]] = {}
        self._stop = threading.Event()
        self._pool_dir = storage.root / "cache" / "pool"
        self._pool_dir.mkdir(parents=True, exist_ok=True)
        self._recover()
        self._thread = threading.Thread(target=self._loop, name="masgent-run-dispatcher", daemon=True)
        if autostart:
            self._thread.start()

    # ------------------------------------------------------------ config
    @property
    def max_concurrent(self) -> int:
        if self._max is not None:
            return max(1, int(self._max))
        now = time.time()
        cached = getattr(self, "_conc_cache", None)
        if cached is None or now - cached[1] > 3.0:  # settings.json is re-read at most every 3 s
            cached = (max(1, int(load_settings().max_concurrent or 1)), now)
            self._conc_cache = cached
        return cached[0]

    def add_listener(self, fn: Callable[[str, dict], None]) -> None:
        self._listeners.append(fn)

    # ------------------------------------------------------------ submit
    def submit(self, tool_id: str, params: dict[str, Any], *, project: str | None = None, label: str = "",
               tags: list[str] | None = None, origin: str = "ui", parent_run: str | None = None,
               session_dir: Path | str | None = None, device: str | None = None, seed: int | None = None,
               notes: str = "") -> str:
        from core.registry import form_fields, get_tool, prepare_params

        ui, td = get_tool(tool_id)
        settings = load_settings()
        params = prepare_params(tool_id, dict(params))
        if td.func in ("read_file", "rename_file"):
            # Masgent's own traversal check is bypassable with '../' (KNOWN_ISSUES K25): enforce the sandbox here.
            from core.paths import safe_join

            base = Path(session_dir) if session_dir else Path(".")
            for k in ("name", "new_name"):
                if k in params:
                    safe_join(base, str(params[k]))
        seed = int(settings.seed if seed is None else seed)
        device = self._resolve_device(device or settings.device, ui)
        rec = self.storage.create_run(tool_id, project or settings.default_project, {
            "func": td.func, "tool_title": ui.title, "label": label, "tags": tags or [], "origin": origin,
            "parent_run": parent_run, "device": device, "seed": seed, "notes": notes,
            "page": ui.page, "runtime": ui.runtime,
        })
        files = rec.files_dir
        sdir = Path(session_dir) if session_dir else files
        kinds = {f.name: (f.ui.kind or "") for f in form_fields(tool_id)}

        staged: dict[str, Any] = {}
        rel_params: dict[str, Any] = {}
        for k, v in params.items():
            staged[k], rel_params[k] = self._stage(k, v, kinds.get(k, ""), files, rec.path, staged_ai=session_dir is not None)

        hooks = []
        if td.func == "design_model_for_machine_learning":
            hooks.append("optuna_live")
        if td.func in ("train_model_for_machine_learning", "retrain_model_for_machine_learning"):
            hooks.append("training_live")
        if td.func == "augment_data_for_machine_learning":
            hooks.append("cvae_live")

        env: dict[str, str | None] = {}
        if (mp := get_secret("MP_API_KEY")):
            env["MP_API_KEY"] = mp
        if settings.psp_dir and Path(settings.psp_dir).is_dir():
            env["PMG_VASP_PSP_DIR"] = settings.psp_dir
        env["CUDA_VISIBLE_DEVICES"] = "" if device == "cpu" else None

        atomic_write_json(rec.path / "inputs.json", scrub_params({"tool": tool_id, "func": td.func, "params": rel_params,
                                                                  "device": device, "seed": seed}))
        atomic_write_json(rec.path / "env.json", env_snapshot(device, seed))
        self.storage.update_meta(rec.run_id, session_dir=str(sdir) if session_dir else None, hooks=hooks)

        job = Job(rec.run_id, rec.path, td.func, staged, sdir, device, seed, hooks, env)
        if session_dir:
            job.snapshot = self._snapshot(sdir)
        with self._lock:
            self._queue.append(job)
            self._jobs[rec.run_id] = job
        self._emit(rec.run_id)
        return rec.run_id

    def _resolve_device(self, requested: str, ui: Any) -> str:
        if not getattr(ui, "gpu", False):
            return "cpu"
        if requested == "cpu":
            return "cpu"
        from core.capabilities import gpu_info

        info = gpu_info(self.storage.root / "cache" / "capabilities.json")
        gpu_ok = bool(info.get("cuda_available")) and info.get("gpu_supported", True)
        if requested == "cuda":
            return "cuda" if gpu_ok else "cpu"
        return "cuda" if gpu_ok else "cpu"  # auto

    def _stage(self, name: str, value: Any, kind: str, files: Path, run_dir: Path, staged_ai: bool) -> tuple[Any, Any]:
        """Copy referenced inputs into ``files/inputs`` so every run is self-contained."""
        if not isinstance(value, str) or not value:
            return value, value
        src: Path | None = None
        folder_example = None
        if value.startswith("example:"):
            src = examples.structure_path(value.split(":", 1)[1])
        elif value.startswith("example_ml:"):
            src = examples.ml_path(value.split(":", 1)[1])
        elif value.startswith("example_folder:"):
            folder_example = value.split(":", 1)[1]
            src = examples.analysis_example(folder_example)
        elif kind in PATH_KINDS or name.endswith(("_path", "_dir")):
            p = Path(value)
            src = p if p.exists() else None
        if src is None:
            if value.startswith(("example:", "example_ml:", "example_folder:")):
                raise FileNotFoundError(f"Example data not found for {value!r}")
            return value, value
        if staged_ai and is_within(src, files.parent):
            return src.as_posix(), value
        if src.is_dir():
            dest = files / src.name
            if dest.exists():
                dest = files / f"{src.name}_{name}"
            copied = examples.copy_whitelisted_tree(src, dest)
            if not copied:
                raise FileNotFoundError(f"No usable VASP output files found in {src}")
        else:
            dest = files / "inputs" / name / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        # Forward slashes: Masgent's interface maker splits paths on '/' only (K23); Windows accepts both.
        return dest.as_posix(), dest.relative_to(run_dir).as_posix()

    # ------------------------------------------------------------ control
    def cancel(self, run_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(run_id)
            if job is None:
                rec = self.storage.get_run(run_id)
                if rec and rec.status in ACTIVE and _pid_is_worker(rec.meta.get("pid")):
                    _kill_tree(rec.meta["pid"])
                    self._finish_meta(run_id, "cancelled", "Cancelled by user.")
                    return True
                return False
            job.cancelled = True
            if job in self._queue:
                self._queue.remove(job)
                self._finish_meta(run_id, "cancelled", "Cancelled before it started.")
                job.done.set()
                return True
            pid = job.proc.pid if job.proc else job.adopted_pid
        if pid:
            _kill_tree(pid)
        return True

    def wait(self, run_id: str, timeout: float | None = None) -> dict | None:
        job = self._jobs.get(run_id)
        if job:
            job.done.wait(timeout)
        rec = self.storage.get_run(run_id)
        return rec.meta if rec else None

    def active_ids(self) -> set[str]:
        with self._lock:
            return {j.run_id for j in self._queue} | set(self._running)

    def queue_position(self, run_id: str) -> int | None:
        with self._lock:
            for i, j in enumerate(self._queue):
                if j.run_id == run_id:
                    return i + 1
        return None

    def shutdown(self, kill_running: bool = False) -> None:
        self._stop.set()
        with self._lock:
            for p in self._spares:
                try:
                    p.stdin.close()  # type: ignore[union-attr]
                except Exception:
                    pass
                _kill_tree(p.pid)
            self._spares.clear()
            if kill_running:
                for job in list(self._running.values()):
                    job.cancelled = True
                    if job.proc:
                        _kill_tree(job.proc.pid)
        if kill_running:
            deadline = time.time() + 10
            while self._running and time.time() < deadline:
                self._tick()
                time.sleep(0.1)

    # --------------------------------------------------------- internals
    def _spawn(self) -> subprocess.Popen:
        env = {k: v for k, v in os.environ.items() if k not in SECRET_KEYS}
        env["MASGENT_SESSION_RUNS_DIR"] = RUNS_DIR_PLACEHOLDER
        err = open(self._pool_dir / "worker_bootstrap.log", "ab")
        try:
            return subprocess.Popen(
                [self.python, "-I", "-u", str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=err, cwd=str(self._pool_dir), env=env, creationflags=_NO_WINDOW,
            )
        finally:
            err.close()

    def _take_worker(self) -> subprocess.Popen:
        while self._spares:
            p = self._spares.pop(0)
            if p.poll() is None:
                return p
        return self._spawn()

    def _start(self, job: Job) -> None:
        proc = self._take_worker()
        line = json.dumps({
            "run_id": job.run_id, "run_dir": str(job.run_dir), "session_dir": str(job.session_dir),
            "func": job.func, "params": job.params, "device": job.device, "seed": job.seed,
            "hooks": job.hooks, "env": job.env, "needs_torch_seed": job.func.startswith(("run_simulation", "augment")),
        })
        try:
            proc.stdin.write((line + "\n").encode("utf-8"))  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]
            proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            proc = self._spawn()
            proc.stdin.write((line + "\n").encode("utf-8"))  # type: ignore[union-attr]
            proc.stdin.close()  # type: ignore[union-attr]
        job.proc, job.started = proc, time.time()
        self.storage.update_meta(job.run_id, status="running", started=now_iso(), pid=proc.pid)
        self._running[job.run_id] = job
        self._emit(job.run_id)

    def _tick(self) -> None:
        with self._lock:
            # finished jobs
            for rid, job in list(self._running.items()):
                code = job.proc.poll() if job.proc else (None if _pid_is_worker(job.adopted_pid) else 0)
                if code is not None:
                    del self._running[rid]
                    self._finalize(job, code)
            # start queued jobs
            while self._queue and len(self._running) < self.max_concurrent:
                gpu_busy = any(j.gpu for j in self._running.values())
                nxt = next((j for j in self._queue if not (j.gpu and gpu_busy)), None)
                if nxt is None:
                    break
                self._queue.remove(nxt)
                try:
                    self._start(nxt)
                except Exception as e:  # pragma: no cover
                    self._finish_meta(nxt.run_id, "failed", f"Could not start worker: {e}")
                    nxt.done.set()
            # keep spares warm
            self._spares = [p for p in self._spares if p.poll() is None]
            if not self._stop.is_set():
                while len(self._spares) < self.warm_spares:
                    self._spares.append(self._spawn())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # pragma: no cover - never let the dispatcher die
                pass
            time.sleep(0.2)

    def _finalize(self, job: Job, code: int) -> None:
        result = read_json(job.run_dir / "result.json", None)
        if job.cancelled:
            status, err = "cancelled", "Cancelled by user."
        elif result is None:
            tail = "\n".join(redact(self._log_tail(job.run_dir)).splitlines()[-15:])
            status, err = "failed", f"The worker process exited unexpectedly (exit code {code}).\n{tail}"
        else:
            status = "succeeded" if result.get("status") == "success" else "failed"
            err = None if status == "succeeded" else str(result.get("message", "Unknown error"))
        if result is not None:
            atomic_write_json(job.run_dir / "result.json", self._sanitize(result, job))
        if job.session_dir != job.run_dir / "files":
            self._collect_session_outputs(job)
        self._finish_meta(job.run_id, status, err)
        job.done.set()
        for fn in self._then.pop(job.run_id, []):
            if status != "succeeded":
                continue
            try:
                fn(self.storage.get_run(job.run_id))
            except Exception:  # pragma: no cover
                pass

    def then(self, run_id: str, fn: Callable[[Any], None]) -> None:
        """Call ``fn(run_record)`` after ``run_id`` succeeds (simple chaining)."""
        with self._lock:
            self._then.setdefault(run_id, []).append(fn)

    def _finish_meta(self, run_id: str, status: str, error: str | None) -> None:
        rec = self.storage.get_run(run_id)
        if not rec:
            return
        started = rec.meta.get("started")
        ended = now_iso()
        dur = None
        if started:
            import datetime as dt

            try:
                dur = (dt.datetime.fromisoformat(ended) - dt.datetime.fromisoformat(started)).total_seconds()
            except ValueError:
                dur = None
        summary = {}
        try:
            from core.parsers import summarize

            summary = summarize(rec)
        except Exception:
            pass
        self.storage.update_meta(run_id, status=status, ended=ended, duration_s=dur, error=redact(error),
                                 summary=summary, pid=None)
        try:
            self.storage.write_manifest(rec.path)
        except Exception:
            pass
        self._emit(run_id)

    @staticmethod
    def _sanitize(result: dict, job: Job) -> dict:
        """Make stored results portable: absolute run paths → ``{RUN}`` / ``{SESSION}``."""
        reps: list[tuple[str, str]] = []
        if job.session_dir != job.run_dir / "files":
            reps.append((str(job.session_dir), "{SESSION}"))
        reps.append((str(job.run_dir), "{RUN}"))

        def fix(v: Any) -> Any:
            if isinstance(v, str):
                s = v
                for a, b in reps:
                    s = s.replace(a, b).replace(a.replace("\\", "/"), b)
                if "{RUN}" in s or "{SESSION}" in s:
                    s = s.replace("\\", "/")
                return redact(s)
            if isinstance(v, dict):
                return {k: fix(x) for k, x in v.items()}
            if isinstance(v, list):
                return [fix(x) for x in v]
            return v
        return fix(result)

    @staticmethod
    def _snapshot(d: Path) -> dict[str, float]:
        snap = {}
        if d.exists():
            for p in d.rglob("*"):
                if p.is_file():
                    try:
                        snap[p.relative_to(d).as_posix()] = p.stat().st_mtime
                    except OSError:
                        pass
        return snap

    def _collect_session_outputs(self, job: Job) -> None:
        """AI mode: copy files created/changed by this call from the chat session dir."""
        dest = job.run_dir / "files"
        for rel, mtime in self._snapshot(job.session_dir).items():
            if rel in job.snapshot and job.snapshot[rel] >= mtime:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(job.session_dir / rel, target)
            except OSError:
                pass

    @staticmethod
    def _log_tail(run_dir: Path, n: int = 4000) -> str:
        p = run_dir / "logs.txt"
        if not p.exists():
            return ""
        data = p.read_bytes()[-n:]
        return data.decode("utf-8", errors="replace")

    def _recover(self) -> None:
        """Adopt live workers / recover finished runs left over from a previous app process."""
        rows, _ = self.storage.query_runs(statuses=["queued", "running"], limit=10_000)
        live = set()
        for r in rows:
            rec = self.storage.get_run(r["run_id"])
            if not rec:
                continue
            pid = rec.meta.get("pid")
            if rec.status == "running" and _pid_is_worker(pid):
                job = Job(rec.run_id, rec.path, rec.meta.get("func", ""), {}, Path(rec.meta.get("session_dir") or rec.files_dir),
                          rec.meta.get("device", "cpu"), int(rec.meta.get("seed", 42)), [], {}, adopted_pid=pid)
                self._running[rec.run_id] = job
                self._jobs[rec.run_id] = job
                live.add(rec.run_id)
            elif (rec.path / "result.json").exists():
                job = Job(rec.run_id, rec.path, rec.meta.get("func", ""), {}, rec.files_dir, "cpu", 42, [], {})
                self._finalize(job, 0)
                live.add(rec.run_id)
        self.storage.reindex(live_run_ids=live | set(self._running))

    def _emit(self, run_id: str) -> None:
        rec = self.storage.get_run(run_id)
        for fn in list(self._listeners):
            try:
                fn(run_id, rec.meta if rec else {})
            except Exception:
                pass


RunManager.instances = weakref.WeakSet()
