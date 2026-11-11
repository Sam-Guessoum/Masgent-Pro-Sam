"""Filesystem locations and path-safety helpers.

Everything the app writes lives under the *workspace* (default
``streamlit_app/workspace``). ``safe_join`` is the single guard used for every
user-influenced path (uploads, zip members, file viewers).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

APP_DIR: Path = Path(__file__).resolve().parents[1]
REPO_DIR: Path = APP_DIR.parent
SRC_DIR: Path = REPO_DIR / "src"

#: Placeholder used for ``MASGENT_SESSION_RUNS_DIR`` while a worker imports
#: Masgent; default arguments frozen at import time contain this token and are
#: substituted with the real run directory before a tool is called.
RUNS_DIR_PLACEHOLDER = "__MASGENT_RUNS_DIR__"


def ensure_masgent_importable() -> None:
    """Make ``import masgent`` work even if it was not pip-installed."""
    try:
        import masgent  # noqa: F401
    except ImportError:
        if SRC_DIR.is_dir() and str(SRC_DIR) not in sys.path:
            sys.path.insert(0, str(SRC_DIR))


def config_dir() -> Path:
    """Directory holding ``.env`` and ``settings.json`` (overridable for tests)."""
    override = os.environ.get("MASGENT_UI_CONFIG_DIR")
    return Path(override).resolve() if override else APP_DIR


def default_workspace() -> Path:
    override = os.environ.get("MASGENT_UI_WORKSPACE")
    return Path(override).resolve() if override else APP_DIR / "workspace"


class UnsafePathError(ValueError):
    """Raised when a path would escape its sandbox root."""


def safe_join(root: Path | str, *parts: str) -> Path:
    """Join ``parts`` under ``root`` and refuse anything that escapes it.

    Rejects absolute paths, drive letters, ``..`` traversal and paths that
    resolve (through symlinks) outside ``root``.
    """
    root_p = Path(root).resolve()
    for part in parts:
        p = str(part).replace("\\", "/")
        if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or "\x00" in p:
            raise UnsafePathError(f"Absolute or invalid path not allowed: {part!r}")
        if any(seg == ".." for seg in p.split("/")):
            raise UnsafePathError(f"Path traversal not allowed: {part!r}")
    candidate = root_p.joinpath(*[str(p).replace("\\", "/") for p in parts]).resolve()
    if candidate != root_p and root_p not in candidate.parents:
        raise UnsafePathError(f"Path escapes sandbox: {candidate}")
    return candidate


def is_within(path: Path | str, root: Path | str) -> bool:
    try:
        p, r = Path(path).resolve(), Path(root).resolve()
    except OSError:
        return False
    return p == r or r in p.parents


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._+\-]+")


def safe_filename(name: str, default: str = "file") -> str:
    """Reduce an arbitrary (uploaded) name to a safe single path component."""
    base = Path(str(name).replace("\\", "/")).name
    base = _SAFE_NAME.sub("_", base).strip("._") or default
    return base[:120]


def slugify(text: str, default: str = "project") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (s or default)[:60]
