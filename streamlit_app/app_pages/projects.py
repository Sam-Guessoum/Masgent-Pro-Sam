"""Projects: group runs, set a default project, per-project timeline, export."""
from pathlib import Path

import pandas as pd
import streamlit as st

from core.settings import load_settings, update_settings
from ui.common import human_duration
from ui.state import services

svc = services()
store = svc.storage
st.title("Projects", anchor=False)

with st.form("new_project", border=True):
    c1, c2, c3 = st.columns([2, 3, 1], vertical_alignment="bottom")
    name = c1.text_input("New project name", placeholder="e.g. LaCoO3 oxygen vacancies")
    desc = c2.text_input("Description (optional)")
    if c3.form_submit_button("Create", type="primary", icon=":material/add:") and name.strip():
        slug = store.ensure_project(name.strip(), desc)
        st.session_state["project"] = slug
        st.toast(f"Created project '{slug}' and made it active.")
        st.rerun()

default = load_settings().default_project
for p in store.list_projects():
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**{p['name']}** · `{p['slug']}`")
            st.caption(f"{p['n_runs']} run(s) · created {str(p['created'])[:10]}")
            if p["slug"] == default:
                st.badge("Default", color="green")
            if st.session_state.get("project") == p["slug"]:
                st.badge("Active", color="blue")
        if p.get("description"):
            st.caption(p["description"])
        with st.container(horizontal=True):
            if st.button("Make active", key=f"act_{p['slug']}"):
                st.session_state["project"] = p["slug"]
                st.rerun()
            if st.button("Set as default", key=f"def_{p['slug']}", disabled=p["slug"] == default):
                update_settings(default_project=p["slug"])
                st.rerun()
            if st.button("Export project ZIP", key=f"zip_{p['slug']}", icon=":material/folder_zip:"):
                path, skipped = store.export_project_zip(p["slug"])
                st.session_state[f"zip_ready_{p['slug']}"] = (str(path), len(skipped))
            ready = st.session_state.get(f"zip_ready_{p['slug']}")
            if ready:
                st.download_button(f"Download {Path(ready[0]).name}", Path(ready[0]).read_bytes(), file_name=Path(ready[0]).name,
                                   key=f"dl_{p['slug']}", on_click="ignore", icon=":material/download:",
                                   help=f"{ready[1]} POTCAR file(s) excluded." if ready[1] else None)
        with st.expander("Timeline"):
            rows, _ = store.query_runs(project=p["slug"], limit=200, order="created ASC")
            if rows:
                df = pd.DataFrame([{"Created": r["created"].replace("T", " "), "Run": r.get("label") or r.get("tool_title"),
                                    "Status": r["status"], "Duration": human_duration(r.get("duration_s"))} for r in rows])
                try:
                    import plotly.express as px

                    tl = pd.DataFrame([{"Run": (r.get("label") or r.get("tool_title"))[:40], "Start": r.get("started") or r["created"],
                                        "End": r.get("ended") or r.get("started") or r["created"], "Status": r["status"]} for r in rows])
                    tl["Start"], tl["End"] = pd.to_datetime(tl["Start"]), pd.to_datetime(tl["End"])
                    tl["End"] = tl[["Start", "End"]].max(axis=1) + pd.Timedelta(seconds=2)
                    fig = px.timeline(tl, x_start="Start", x_end="End", y="Run", color="Status")
                    fig.update_layout(height=max(220, 26 * len(tl)), margin=dict(l=10, r=10, t=10, b=10))
                    st.plotly_chart(fig, key=f"tl_{p['slug']}", alt=f"Timeline of project {p['slug']}")
                except Exception:
                    pass
                st.dataframe(df, hide_index=True)
            else:
                st.caption("No runs yet.")
