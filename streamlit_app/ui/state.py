"""Shared services (cached per server) and per-user session state."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st

from core.applog import get_logger
from core.runner import RunManager
from core.settings import load_settings
from core.storage import Storage


@dataclass
class Services:
    storage: Storage
    manager: RunManager
    workspace: Path


@st.cache_resource(show_spinner="Starting the Masgent engine…")
def _services(workspace: str) -> Services:
    ws = Path(workspace)
    storage = Storage(ws)
    manager = RunManager(storage)
    log = get_logger(ws)
    log.info(f"services started for workspace {ws}")
    return Services(storage, manager, ws)


def services() -> Services:
    return _services(str(load_settings().workspace_path))


def init_session() -> None:
    ss = st.session_state
    s = load_settings()
    ss.setdefault("project", s.default_project or "default")
    ss.setdefault("active_structure", None)  # path (str) or example ref
    ss.setdefault("tool_runs", {})  # tool_id -> last run_id shown in its dock
    ss.setdefault("handoff", {})  # tool_id -> params pushed by "Use in…" / re-run
    ss.setdefault("form_nonce", {})


def form_state(tool_id: str) -> dict[str, Any]:
    return st.session_state.setdefault(f"form:{tool_id}", {})


def bump_form(tool_id: str) -> None:
    """Force form widgets to re-create with values from ``form_state`` (preset load)."""
    n = st.session_state.setdefault("form_nonce", {})
    n[tool_id] = n.get(tool_id, 0) + 1


def nonce(tool_id: str) -> int:
    return st.session_state.setdefault("form_nonce", {}).get(tool_id, 0)


def load_into_form(tool_id: str, params: dict[str, Any], replace: bool = False) -> None:
    fs = form_state(tool_id)
    if replace:
        fs.clear()
    fs.update(params)
    bump_form(tool_id)


def set_tool_run(tool_id: str, run_id: str | None) -> None:
    st.session_state.setdefault("tool_runs", {})[tool_id] = run_id


def tool_run(tool_id: str) -> str | None:
    return st.session_state.setdefault("tool_runs", {}).get(tool_id)


def handoff(tool_id: str, params: dict[str, Any], page: str | None = None) -> None:
    """Send parameters to another tool's form (chaining)."""
    load_into_form(tool_id, params)
    st.session_state["focus_tool"] = tool_id
    if page:
        st.session_state["nav_target"] = page
