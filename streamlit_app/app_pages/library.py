"""Results library: search, filter, reopen, compare, re-run, export, report, trash."""
import datetime as dt
import json
import shutil
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from core.registry import ui_tools
from core.report import build_report
from core.storage import ACTIVE
from ui.common import human_duration
from ui.results import render_run
from ui.state import services
from ui.viewer3d import show_structure

svc = services()
store = svc.storage
st.title("Results library", anchor=False)

# ------------------------------------------------------------------ filters
with st.container(border=True):
    c1, c2, c3, c4 = st.columns([3, 2, 2, 2], vertical_alignment="bottom")
    q = c1.text_input("Search", placeholder="label, tool, tag, notes, run id…", key="lib_q")
    projects = [p["slug"] for p in store.list_projects()]
    proj = c2.selectbox("Project", ["All projects"] + projects, key="lib_proj")
    statuses = c3.multiselect("Status", ["succeeded", "failed", "running", "queued", "cancelled"], key="lib_status")
    trash_view = c4.toggle("Show trash", key="lib_trash")
    c5, c6, c7, c8 = st.columns([3, 2, 2, 2], vertical_alignment="bottom")
    tools_all = sorted(ui_tools())
    tools = c5.multiselect("Tool", tools_all, format_func=lambda t: ui_tools()[t].title, key="lib_tools")
    tags = c6.multiselect("Tags", store.all_tags(), key="lib_tags")
    dates = c7.date_input("Date range", value=(), key="lib_dates", format="YYYY-MM-DD")
    starred = c8.toggle("Starred only", key="lib_star")
    c9, c10 = st.columns([3, 1], vertical_alignment="bottom")
    order = c9.segmented_control("Sort", ["Newest", "Oldest", "Longest", "Tool", "Status"], default="Newest", key="lib_order")
    page_size = c10.selectbox("Per page", [25, 50, 100], key="lib_ps")

ORDER = {"Newest": "created DESC", "Oldest": "created ASC", "Longest": "duration_s DESC", "Tool": "tool ASC", "Status": "status ASC"}
d_from = dates[0].isoformat() if isinstance(dates, (list, tuple)) and len(dates) >= 1 else None
d_to = dates[1].isoformat() if isinstance(dates, (list, tuple)) and len(dates) == 2 else None
page = st.session_state.get("lib_page", 0)
rows, total = store.query_runs(search=q, project=None if proj == "All projects" else proj, tools=tools or None,
                               statuses=statuses or None, date_from=d_from, date_to=d_to, tags=tags or None,
                               starred=True if starred else None, trashed=trash_view, order=ORDER[order or "Newest"],
                               limit=page_size, offset=page * page_size)
n_pages = max(1, (total + page_size - 1) // page_size)
if page >= n_pages:
    st.session_state["lib_page"] = 0
    page = 0

df = pd.DataFrame([{
    "★": "★" if r["starred"] else "", "Status": r["status"], "Run": r.get("label") or r.get("tool_title") or r["tool"],
    "Tool": r.get("tool_title"), "Project": r["project"], "Created": r["created"][:16].replace("T", " "),
    "Duration": human_duration(r.get("duration_s")), "Tags": ", ".join(r["tags"]),
    "Key results": ", ".join(f"{k}: {v}" for k, v in (json.loads(r.get("summary") or "{}") or {}).items())[:120],
    "run_id": r["run_id"],
} for r in rows])

st.caption(f"{total} run(s){' in trash' if trash_view else ''} · page {page + 1} of {n_pages} · select one row to open, two or more to compare")
sel_rows: list[int] = []
if len(df):
    ev = st.dataframe(df, hide_index=True, on_select="rerun", selection_mode="multi-row", key=f"lib_table_{page}_{trash_view}",
                      column_config={"run_id": None, "★": st.column_config.TextColumn("★", width="small")})
    sel_rows = list(ev.selection.rows) if ev and ev.selection else []
else:
    st.info("No runs match these filters." if (q or tools or statuses or tags) else "No runs yet: start one from any tool page.")
with st.container(horizontal=True):
    if st.button("Previous", disabled=page == 0, icon=":material/chevron_left:"):
        st.session_state["lib_page"] = page - 1
        st.rerun()
    if st.button("Next", disabled=page >= n_pages - 1, icon=":material/chevron_right:"):
        st.session_state["lib_page"] = page + 1
        st.rerun()
    if st.button("Rebuild index", icon=":material/sync:", help="Re-scan every run folder and rebuild index.db (safe)."):
        info = store.reindex(live_run_ids=svc.manager.active_ids())
        st.toast(f"Re-indexed {info['runs']} runs ({info['interrupted_fixed']} interrupted runs marked failed).")
    if trash_view and len(df):
        if st.button("Empty trash", icon=":material/delete_forever:", type="secondary"):
            st.session_state["confirm_purge"] = True
if st.session_state.get("confirm_purge"):
    st.warning("Permanently delete everything in the trash? This cannot be undone.")
    a, b = st.columns(2)
    if a.button("Yes, delete permanently", type="primary"):
        n = store.purge_trash()
        st.session_state["confirm_purge"] = False
        st.success(f"Deleted {n} run(s).")
        st.rerun()
    if b.button("Keep them"):
        st.session_state["confirm_purge"] = False
        st.rerun()

selected = [df.iloc[i]["run_id"] for i in sel_rows]
if not selected and st.session_state.get("library_open"):
    selected = [st.session_state["library_open"]]


# ------------------------------------------------------------ run detail
def run_detail(run_id: str) -> None:
    rec = store.get_run(run_id)
    if not rec:
        st.warning("Run not found.")
        return
    in_trash = rec.path.parent.name == ".trash"
    with st.container(border=True):
        render_run(rec, key=f"lib_{run_id}")
    st.markdown("##### Organise")
    with st.form(f"edit_{run_id}", border=True):
        c1, c2, c3 = st.columns([3, 3, 1], vertical_alignment="bottom")
        label = c1.text_input("Label", value=rec.meta.get("label", ""))
        tags = c2.text_input("Tags (comma-separated)", value=", ".join(rec.meta.get("tags") or []))
        star = c3.checkbox("★ Star", value=bool(rec.meta.get("starred")))
        notes = st.text_area("Notes", value=rec.meta.get("notes", ""), height=80)
        if st.form_submit_button("Save", icon=":material/save:"):
            store.update_meta(run_id, label=label, tags=[t.strip() for t in tags.split(",") if t.strip()], starred=star, notes=notes)
            st.toast("Saved")
            st.rerun()
    with st.container(horizontal=True):
        html = build_report(rec)
        st.download_button("HTML report", html, file_name=f"{run_id}_report.html", mime="text/html", on_click="ignore",
                           icon=":material/summarize:", help="One-page report; open it and use Print → Save as PDF for a PDF.")
        if in_trash:
            if st.button("Restore", icon=":material/restore_from_trash:"):
                store.restore_run(run_id)
                st.rerun()
        elif rec.status in ACTIVE:
            if st.button("Cancel run", icon=":material/stop_circle:"):
                svc.manager.cancel(run_id)
                st.rerun()
        else:
            if st.button("Delete", icon=":material/delete:"):
                st.session_state[f"confirm_del_{run_id}"] = True
    if st.session_state.get(f"confirm_del_{run_id}"):
        st.warning("Move this run to the trash? You can restore it from *Show trash* until the trash is emptied.")
        a, b = st.columns(2)
        if a.button("Move to trash", type="primary", key=f"del_yes_{run_id}"):
            store.trash_run(run_id)
            st.session_state.pop(f"confirm_del_{run_id}", None)
            st.session_state.pop("library_open", None)
            st.rerun()
        if b.button("Cancel", key=f"del_no_{run_id}"):
            st.session_state.pop(f"confirm_del_{run_id}", None)
            st.rerun()
    with st.expander("Report preview", icon=":material/preview:"):
        st.iframe(html, height=600, alt="Run report preview")
    with st.expander("Masgent file tools (list / read / copy-rename)", icon=":material/folder_managed:"):
        st.caption("Runs Masgent's own `list_files`, `read_file` and `rename_file` tools as new runs. "
                   "Read-only tools act on this run's files; copy/rename acts on a private copy, so this run is never modified.")
        tool = st.segmented_control("Masgent tool", ["list_files", "read_file", "rename_file"], default="list_files", key=f"ft_{run_id}")
        files = [p.relative_to(rec.files_dir).as_posix() for p in rec.output_files(include_inputs=True)]
        params: dict = {}
        if tool in ("read_file", "rename_file"):
            params["name"] = st.selectbox("File", files, key=f"ft_name_{run_id}") if files else None
        if tool == "rename_file":
            params["new_name"] = st.text_input("New name (relative)", value=(params.get("name") or "file") + ".copy", key=f"ft_new_{run_id}")
        if st.button("Run file tool", key=f"ft_run_{run_id}", disabled=tool != "list_files" and not params.get("name"), icon=":material/play_arrow:"):
            session = rec.files_dir
            if tool == "rename_file":
                session = store.root / "cache" / "sessions" / uuid.uuid4().hex[:10]
                shutil.copytree(rec.files_dir, session)
            rid = svc.manager.submit(tool, params, project=rec.meta.get("project"), parent_run=run_id, session_dir=session,
                                     label=f"{tool} on {rec.meta.get('label') or rec.meta.get('tool_title')}")
            svc.manager.wait(rid, timeout=120)
            st.session_state["library_open"] = rid
            st.rerun()


def compare(run_ids: list[str]) -> None:
    import plotly.graph_objects as go

    from core import parsers

    recs = [store.get_run(r) for r in run_ids]
    recs = [r for r in recs if r]
    st.subheader(f"Comparing {len(recs)} runs", anchor=False)
    names = [f"{i + 1}. {r.meta.get('label') or r.meta.get('tool_title')}" for i, r in enumerate(recs)]
    params = [r.inputs().get("params", {}) for r in recs]
    keys = sorted(set().union(*[p.keys() for p in params]))
    pdf = pd.DataFrame({n: [json.dumps(p.get(k), default=str) if isinstance(p.get(k), (list, dict)) else p.get(k) for k in keys]
                        for n, p in zip(names, params)}, index=keys)
    differs = pdf.astype(str).nunique(axis=1) > 1
    only_diff = st.toggle("Only show parameters that differ", value=True, key="cmp_diff")
    st.markdown("**Parameters**")
    st.dataframe(pdf[differs] if only_diff else pdf, width="stretch")
    st.markdown("**Key results**")
    meta_rows = {n: {"tool": r.meta.get("tool_title"), "status": r.status, "duration": human_duration(r.meta.get("duration_s")),
                     **(r.meta.get("summary") or {})} for n, r in zip(names, recs)}
    st.dataframe(pd.DataFrame(meta_rows), width="stretch")

    # Overlaid plots where meaningful
    fig_eos, fig_md, fig_loss = go.Figure(), go.Figure(), go.Figure()
    for n, r in zip(names, recs):
        f = r.files_dir
        if (p := next(f.rglob("eos_cal.csv"), None)):
            d = parsers.parse_eos_csv(p, natoms=parsers._natoms(f))
            fig_eos.add_scatter(x=d["volume_per_atom"], y=d["energy_per_atom"], mode="lines+markers", name=n)
        if (p := next(f.rglob("masgent_mlps_md.log"), None)):
            md = parsers.parse_md_log(p)
            fig_md.add_scatter(x=[m["time_ps"] for m in md], y=[m["T"] for m in md], name=n)
        lp = r.path / "progress" / "loss.jsonl"
        if lp.exists():
            from core.progress import _jsonl

            L = _jsonl(lp)
            fig_loss.add_scatter(x=[x["epoch"] for x in L], y=[x["valid"] for x in L], name=f"{n} (valid)")
    for fig, title, xt, yt in ((fig_eos, "Energy–volume", "Volume (Å³/atom)", "Energy (eV/atom)"),
                               (fig_md, "MD temperature", "Time (ps)", "T (K)"),
                               (fig_loss, "Validation loss", "Epoch", "Loss")):
        if fig.data:
            fig.update_layout(title=title, xaxis_title=xt, yaxis_title=yt, height=380, margin=dict(l=10, r=10, t=40, b=10))
            if title == "Validation loss":
                fig.update_yaxes(type="log")
            st.plotly_chart(fig, key=f"cmp_{title}", alt=f"Overlay: {title}")

    from core.structures import looks_like_structure

    structs = []
    for n, r in zip(names, recs):
        s = next((p for p in r.output_files() if looks_like_structure(p)), None)
        if s:
            structs.append((n, s))
    if structs:
        st.markdown("**Structures side by side**")
        cols = st.columns(min(3, len(structs)))
        for i, (n, s) in enumerate(structs[:3]):
            with cols[i]:
                st.caption(f"{n} · {s.name}")
                show_structure(s, key=f"cmp3d_{i}", height=300, controls=False)


if len(selected) == 1:
    st.divider()
    run_detail(selected[0])
elif len(selected) > 1:
    st.divider()
    compare(selected)
_ = (dt, Path)
