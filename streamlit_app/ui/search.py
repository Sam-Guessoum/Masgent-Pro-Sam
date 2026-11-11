"""Global search / command box: jump to any tool or page in one step."""
from __future__ import annotations

import streamlit as st

from core.catalog import PAGES
from core.registry import ui_tools
from ui.common import PAGE_FILES

EXTRA_PAGES = {
    "home": "Home / dashboard", "library": "Results library", "projects": "Projects", "assistant": "AI assistant",
    "settings": "Settings", "doctor": "Setup doctor", "help": "Help & glossary",
}


@st.cache_data(show_spinner=False)
def _entries() -> list[tuple[str, str, str | None]]:
    """(label, page key, tool id or None)."""
    out = []
    for t in ui_tools().values():
        page_title = PAGES.get(t.page, (t.page,))[0]
        out.append((f"{t.title}  ·  {page_title}", t.page, t.id))
    for k, v in EXTRA_PAGES.items():
        out.append((f"{v}  ·  page", k, None))
    return out


def _keywords(tool_id: str | None) -> str:
    if not tool_id:
        return ""
    t = ui_tools()[tool_id]
    return " ".join(t.keywords + [t.summary, t.func, t.cli])


def command_box(where=st.sidebar) -> None:  # noqa: ANN001
    entries = _entries()
    labels = [e[0] for e in entries]

    def _go() -> None:
        sel = st.session_state.get("cmd_box")
        if not sel:
            return
        _, page, tool = entries[labels.index(sel)]
        st.session_state["cmd_box"] = None
        if tool:
            st.session_state["focus_tool"] = tool
        st.session_state["_cmd_jump"] = PAGE_FILES[page]

    where.selectbox("Search tools", labels, index=None, key="cmd_box", on_change=_go,
                    placeholder="Search tools… (e.g. supercell, NEB, CHGNet)", label_visibility="collapsed",
                    help="Type to filter every Masgent function and page, then press Enter.")
    jump = st.session_state.pop("_cmd_jump", None)
    if jump:
        st.switch_page(jump)


def search(query: str) -> list[tuple[str, str, str | None]]:
    """Keyword search used by tests and the help page."""
    q = query.lower().strip()
    hits = []
    for label, page, tool in _entries():
        hay = (label + " " + _keywords(tool)).lower()
        if all(w in hay for w in q.split()):
            hits.append((label, page, tool))
    return hits
