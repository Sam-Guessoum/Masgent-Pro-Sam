"""Masgent Streamlit app: entry point.

Run from the repository root:

    .venv\\Scripts\\streamlit run streamlit_app/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from core.paths import ensure_masgent_importable  # noqa: E402

ensure_masgent_importable()

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Masgent", page_icon=":material/science:", layout="wide",
                   menu_items={"About": "Streamlit frontend for Masgent (Materials Simulation Agent)."})

from ui.common import PAGE_FILES, status_badge  # noqa: E402
from ui.search import command_box  # noqa: E402
from ui.state import init_session, services  # noqa: E402

init_session()

P = lambda key, title, icon, **kw: st.Page(PAGE_FILES[key], title=title, icon=icon, **kw)  # noqa: E731
pages = {
    "": [P("home", "Home", ":material/home:", default=True)],
    "Build": [
        P("build", "Build structure", ":material/deployed_code:"),
        P("defects", "Defects & alloys", ":material/grain:"),
        P("surfaces", "Surfaces & interfaces", ":material/layers:"),
    ],
    "DFT (VASP)": [
        P("vasp", "VASP inputs", ":material/description:"),
        P("workflows", "DFT workflows", ":material/account_tree:"),
    ],
    "Machine learning": [
        P("mlp", "ML potentials", ":material/bolt:"),
        P("mltoolkit", "ML toolkit", ":material/model_training:"),
    ],
    "AI": [P("assistant", "AI assistant", ":material/smart_toy:")],
    "Results": [
        P("library", "Results library", ":material/folder_open:"),
        P("projects", "Projects", ":material/workspaces:"),
    ],
    "Settings & help": [
        P("settings", "Settings", ":material/settings:"),
        P("doctor", "Setup doctor", ":material/health_and_safety:"),
        P("help", "Help & glossary", ":material/menu_book:"),
        P("more", "More tools", ":material/extension:"),
    ],
}
nav = st.navigation(pages, position="sidebar", expanded=True)

svc = services()
with st.sidebar:
    command_box(st.sidebar)
    projects = [p["slug"] for p in svc.storage.list_projects()] or ["default"]
    if st.session_state.get("project") not in projects:
        st.session_state["project"] = projects[0]
    st.selectbox("Project", projects, key="project", help="New runs are saved into this project. Manage projects on the Projects page.")
    active = svc.manager.active_ids()
    if active:
        with st.container(border=True):
            st.caption(f":material/progress_activity: {len(active)} active run(s)")
            for rid in list(active)[:4]:
                rec = svc.storage.get_run(rid)
                if rec:
                    with st.container(horizontal=True, vertical_alignment="center"):
                        status_badge(rec.status)
                        st.caption(rec.meta.get("tool_title", rid)[:28])

nav.run()
