"""Generic tool panel: form + validation + Run + results dock (used by every page)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import streamlit as st

from core import examples
from core.applog import get_logger
from core.registry import get_tool, validate
from core.settings import load_settings
from ui.common import requirement_issues, runtime_badge
from ui.forms import render_form
from ui.results import results_dock
from ui.state import form_state, load_into_form, services, set_tool_run, tool_run


@st.cache_data(ttl=30, max_entries=256, show_spinner=False)
def _validate_cached(tool_id: str, params_json: str, visible: tuple[str, ...], _mt: str) -> list[str]:
    return validate(tool_id, json.loads(params_json), set(visible))


def _mtimes(params: dict[str, Any]) -> str:
    from pathlib import Path

    parts = []
    for v in params.values():
        if isinstance(v, str) and len(v) < 400:
            p = Path(v)
            try:
                if p.exists():
                    parts.append(f"{v}:{p.stat().st_mtime}")
            except OSError:
                pass
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def tool_header(tool_id: str) -> None:
    ui, td = get_tool(tool_id)
    st.subheader(ui.title)
    st.caption(ui.summary)
    with st.container(horizontal=True):
        runtime_badge(ui.runtime)
        if ui.cli:
            st.badge(f"Masgent {ui.cli}", color="gray", icon=":material/terminal:", help=f"Function `{td.func}`")
        if ui.gpu:
            st.badge("GPU capable", color="violet", icon=":material/memory:")
        if ui.auto:
            st.badge("Auto-generated form", color="orange", help="New Masgent tool without hand-written UI metadata.")
    with st.expander("What is this and when do I use it?", icon=":material/help:"):
        st.markdown(ui.help or td.description)
        if td.docstring and td.docstring not in (ui.help or ""):
            st.caption(f"Masgent docstring: {td.docstring}")


def _presets_menu(tool_id: str) -> None:
    ui, _ = get_tool(tool_id)
    saved = services().storage.list_presets(tool_id)
    options = [f"Example: {k}" for k in ui.presets] + [f"Saved: {k}" for k in saved]
    if not options:
        return
    choice = st.menu_button("Load example", options, key=f"presets_{tool_id}", icon=":material/science:")
    if choice:
        name = choice.split(": ", 1)[1]
        params = ui.presets.get(name) if choice.startswith("Example") else saved.get(name)
        load_into_form(tool_id, dict(params or {}), replace=True)
        for k in list(st.session_state):
            if isinstance(k, str) and k.startswith(f"w:{tool_id}:"):
                del st.session_state[k]
        st.rerun()


def render_tool(tool_id: str, dock: bool = True) -> None:
    """Full tool panel. Layout: form (left) · results dock (right)."""
    ui, _ = get_tool(tool_id)
    svc = services()
    full = st.session_state.get(f"full_{tool_id}", False)
    show_dock = st.session_state.get(f"dock_{tool_id}", True) and dock
    if show_dock and not full:
        left, right = st.columns([5, 6], gap="large")
    else:
        left, right = st.container(), st.container()

    with left:
        tool_header(tool_id)
        blocking, soft = requirement_issues(ui.needs)
        for b in blocking:
            st.error(b, icon=":material/block:")
        for s in soft:
            st.info(s, icon=":material/info:")
        with st.container(horizontal=True, vertical_alignment="center"):
            _presets_menu(tool_id)
        with st.container(border=True):
            params, visible = render_form(tool_id)
        resolved = examples.resolve_params(params)
        try:
            errors = _validate_cached(tool_id, json.dumps(resolved, sort_keys=True, default=str), tuple(sorted(visible)), _mtimes(resolved))
        except Exception as e:  # noqa: BLE001
            errors = [f"Could not validate inputs: {e}"]
        if errors:
            with st.container(border=True):
                st.markdown(":orange[:material/rule: **Fix these before running**]")
                for e in errors[:8]:
                    st.caption(f"• {e}")
        else:
            st.caption(":green[:material/check: Inputs are valid.]")

        with st.expander("Run options", icon=":material/tune:"):
            label = st.text_input("Run label (optional)", key=f"label_{tool_id}", placeholder="e.g. Al 2×2×2 for vacancy study")
            tags_txt = st.text_input("Tags (comma-separated)", key=f"tags_{tool_id}")
            device = None
            if ui.gpu:
                device = st.segmented_control("Device", ["auto", "cpu", "cuda"], default=load_settings().device or "auto",
                                              key=f"device_{tool_id}", required=True,
                                              help="'auto' uses the GPU when torch can see a supported one. Orb-v3 always runs on CPU (Masgent limitation).")
            with st.container(horizontal=True):
                pname = st.text_input("Preset name", key=f"pname_{tool_id}", placeholder="Save current inputs as…", label_visibility="collapsed")
                if st.button("Save preset", key=f"psave_{tool_id}", disabled=not pname, icon=":material/bookmark_add:"):
                    svc.storage.save_preset(tool_id, pname, {k: v for k, v in params.items() if v is not None})
                    st.toast(f"Preset '{pname}' saved", icon=":material/bookmark:")

        disabled = bool(errors or blocking)
        with st.container(horizontal=True, vertical_alignment="center"):
            run = st.button("Run", type="primary", disabled=disabled, key=f"run_{tool_id}", icon=":material/play_arrow:",
                            shortcut="ctrl+enter", help="Ctrl+Enter")
            if dock:
                st.toggle("Results beside form", value=True, key=f"dock_{tool_id}")
                st.toggle("Full width", value=False, key=f"full_{tool_id}")
        if run:
            parent = (st.session_state.get("rerun_parent") or {}).get(tool_id)
            try:
                rid = svc.manager.submit(tool_id, params, project=st.session_state.get("project"), label=label,
                                         tags=[t.strip() for t in tags_txt.split(",") if t.strip()],
                                         origin="rerun" if parent else "ui", parent_run=parent, device=device)
                get_logger(svc.workspace).info(f"submitted {tool_id} as {rid}")
                set_tool_run(tool_id, rid)
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not start the run: {e}")

    if dock:
        with right:
            with st.container(border=True):
                st.markdown("##### :material/monitoring: Results")
                results_dock(tool_id, tool_run(tool_id), key=f"dock_{tool_id}")


def tool_selector(page: str, tool_ids: list[str], labels: dict[str, str] | None = None, key: str | None = None) -> str:
    """Choose one tool on a multi-tool page (honours 'Use in…' focus)."""
    key = key or f"sel:{page}"
    focus = st.session_state.pop("focus_tool", None)
    if focus in tool_ids:
        st.session_state[key] = focus
    if st.session_state.get(key) not in tool_ids:
        st.session_state[key] = tool_ids[0]
    fmt = (lambda t: (labels or {}).get(t) or get_tool(t)[0].title)
    if len(tool_ids) <= 6:
        return st.segmented_control("Tool", tool_ids, key=key, format_func=fmt, label_visibility="collapsed", required=True)
    return st.selectbox("Tool", tool_ids, key=key, format_func=fmt, label_visibility="collapsed")


def form_values(tool_id: str) -> dict:
    return form_state(tool_id)
