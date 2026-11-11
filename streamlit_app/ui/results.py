"""Results dock: live view while a run is active, rich final view afterwards."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from core.diagnostics import diagnostic_report
from core.progress import read_progress, tail_text
from core.registry import ui_tools
from core.settings import redact
from core.storage import ACTIVE, RunRecord
from ui import outputs
from ui.common import PAGE_FILES, human_duration, status_badge
from ui.state import handoff, services, set_tool_run

#: "Use in…" targets for a structure file: (menu label, tool id, parameter).
USE_IN: list[tuple[str, str, str]] = [
    ("Supercell", "generate_supercell_from_poscar", "poscar_path"),
    ("Vacancies", "generate_vasp_poscar_with_vacancy_defects", "poscar_path"),
    ("Substitutions", "generate_vasp_poscar_with_substitution_defects", "poscar_path"),
    ("Interstitials", "generate_vasp_poscar_with_interstitial_defects", "poscar_path"),
    ("SQS", "generate_sqs_from_poscar", "poscar_path"),
    ("Surface slab", "generate_surface_slab_from_poscar", "poscar_path"),
    ("Interface (lower)", "generate_interface_from_poscars", "lower_poscar_path"),
    ("Interface (upper)", "generate_interface_from_poscars", "upper_poscar_path"),
    ("VASP inputs", "generate_vasp_inputs_from_poscar", "poscar_path"),
    ("KPOINTS", "customize_vasp_kpoints_with_accuracy", "poscar_path"),
    ("Convergence tests", "generate_vasp_workflow_of_convergence_tests", "poscar_path"),
    ("EOS workflow", "generate_vasp_workflow_of_eos", "poscar_path"),
    ("Elastic workflow", "generate_vasp_workflow_of_elastic_constants", "poscar_path"),
    ("AIMD workflow", "generate_vasp_workflow_of_aimd", "poscar_path"),
    ("NEB initial", "generate_vasp_workflow_of_neb", "initial_poscar_path"),
    ("NEB final", "generate_vasp_workflow_of_neb", "final_poscar_path"),
    ("ML potential", "run_simulation_using_mlps", "poscar_path"),
    ("Convert format", "convert_structure_format", "input_path"),
    ("Direct ↔ Cartesian", "convert_poscar_coordinates", "poscar_path"),
]


def use_in_menu(path: Path, key: str) -> None:
    opts = ["Set as current structure"] + [u[0] for u in USE_IN]
    choice = st.menu_button("Use in…", opts, key=key, icon=":material/arrow_forward:")
    if choice:
        if choice == "Set as current structure":
            st.session_state["active_structure"] = str(path)
            st.toast(f"Current structure: {path.name}", icon=":material/check:")
            return
        label, tool_id, field = next(u for u in USE_IN if u[0] == choice)
        handoff(tool_id, {field: str(path)})
        st.session_state["active_structure"] = str(path)
        page = ui_tools()[tool_id].page
        st.switch_page(PAGE_FILES[page])


def _rerun_into_form(rec: RunRecord) -> None:
    params = rec.inputs().get("params", {})
    restored = {}
    for k, v in params.items():
        if isinstance(v, str) and v.startswith(("files/", "inputs/")) and (rec.path / v).exists():
            restored[k] = str(rec.path / v)
        else:
            restored[k] = v
    tool_id = rec.meta.get("tool")
    if tool_id not in ui_tools():
        st.error("This tool is no longer available.")
        return
    handoff(tool_id, restored)
    st.session_state["rerun_parent"] = {tool_id: rec.run_id}
    st.switch_page(PAGE_FILES[ui_tools()[tool_id].page])


def chain_actions(rec: RunRecord, key: str) -> None:
    """Next-step shortcuts that pass this run's outputs on."""
    func, f = rec.meta.get("func"), rec.files_dir
    acts: list[tuple[str, str, dict]] = []
    if func == "design_model_for_machine_learning":
        bm, bp = next(f.rglob("best_model.pkl"), None), next(f.rglob("best_model_params.log"), None)
        inp = rec.inputs().get("params", {})
        if bm and bp:
            acts.append(("Train this model", "train_model_for_machine_learning", {
                "input_data_path": str(rec.path / inp.get("input_data_path", "")), "output_data_path": str(rec.path / inp.get("output_data_path", "")),
                "best_model_path": str(bm), "best_model_params_path": str(bp)}))
    if func == "augment_data_for_machine_learning":
        xi, yo = next(f.rglob("ml_input_data_augmented.csv"), None), next(f.rglob("ml_output_data_augmented.csv"), None)
        if xi and yo:
            for lab, tid in (("Design a model on augmented data", "design_model_for_machine_learning"),
                             ("Analyse augmented features", "analyze_features_for_machine_learning")):
                acts.append((lab, tid, {"input_data_path": str(xi), "output_data_path": str(yo)}))
    if func == "reduce_dimensions_for_machine_learning" and (r := next(f.rglob("ml_input_data_reduced.csv"), None)):
        acts.append(("Analyse reduced features", "analyze_features_for_machine_learning", {"input_data_path": str(r)}))
    if func in ("train_model_for_machine_learning", "retrain_model_for_machine_learning"):
        tm = next(f.rglob("trained_model.pkl"), None)
        inp = rec.inputs().get("params", {})
        pp = inp.get("best_model_params_path") or inp.get("old_model_params_path")
        if tm and pp:
            acts.append(("Re-train with new data", "retrain_model_for_machine_learning", {
                "old_model_path": str(tm), "old_model_params_path": str(rec.path / pp)}))
    if not acts:
        return
    with st.container(horizontal=True):
        for i, (lab, tid, params) in enumerate(acts):
            if st.button(lab, key=f"{key}_chain_{i}", icon=":material/east:"):
                handoff(tid, params)
                st.switch_page(PAGE_FILES[ui_tools()[tid].page])


def _error_panel(rec: RunRecord, key: str) -> None:
    res = rec.result() or {}
    msg = rec.meta.get("error") or res.get("message") or "The run failed."
    st.error(redact(str(msg)).splitlines()[0][:600], icon=":material/error:")
    with st.expander("What went wrong (details)"):
        st.code(redact(str(msg)), language="text", wrap_lines=True)
        if res.get("traceback"):
            st.code(redact(res["traceback"]), language="python")
    with st.popover("Copy diagnostic report", icon=":material/content_copy:"):
        st.caption("Versions, parameters and the last log lines. API keys are removed. Use the copy icon.")
        st.code(diagnostic_report(rec), language="text", wrap_lines=True)


def _provenance(rec: RunRecord) -> None:
    env = rec.env()
    meta = {k: v for k, v in rec.meta.items() if k not in ("summary",)}
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Run**")
        st.json(meta, expanded=False)
    with c2:
        st.markdown("**Environment**")
        st.json(env, expanded=False)
    bad = services().storage.verify_manifest(rec.path)
    if not rec.meta.get("status") in ACTIVE:
        if bad and bad != ["manifest.sha256 (missing)"]:
            st.warning("Integrity check: files changed since the run finished: " + ", ".join(bad[:10]))
        elif not bad:
            st.caption(":material/verified: All files match `manifest.sha256`.")
    st.caption(f"Folder: `{rec.path}`")


def _inputs_tab(rec: RunRecord) -> None:
    inp = rec.inputs()
    st.json(inp.get("params", {}))
    st.caption(f"Masgent function `{inp.get('func')}` · device {inp.get('device')} · seed {inp.get('seed')}")


def render_run(rec: RunRecord, key: str, compact: bool = False) -> None:
    """Final (or historic) view of one run."""
    meta = rec.meta
    with st.container(horizontal=True, vertical_alignment="center"):
        status_badge(meta.get("status", "?"))
        st.markdown(f"**{meta.get('label') or meta.get('tool_title')}**")
        st.caption(f"{meta.get('created', '')[:19].replace('T', ' ')} · {human_duration(meta.get('duration_s'))} · {meta.get('device')}")
    if meta.get("status") == "failed":
        _error_panel(rec, key)
    elif meta.get("status") == "cancelled":
        st.warning("Cancelled. Partial outputs (if any) are kept below.", icon=":material/cancel:")
    res = rec.result() or {}
    if meta.get("status") == "succeeded" and res.get("message"):
        st.success(res["message"].replace("{RUN}/files/", "").replace("{RUN}/files", "run folder"), icon=":material/check_circle:")

    tabs = st.tabs(["Outputs", "Files", "Log", "Inputs & parameters", "Provenance"], key=f"{key}_tabs", on_change="rerun")
    with tabs[0]:
        if tabs[0].open:
            try:
                outputs.tool_specific(rec, key)
            except Exception as e:  # noqa: BLE001
                st.warning(f"Could not render the interactive view ({type(e).__name__}: {e}). The files are still available.")
            chain_actions(rec, key)
            with st.container(horizontal=True):
                if st.button("Re-run with these parameters", key=f"{key}_rerun", icon=":material/replay:"):
                    _rerun_into_form(rec)
                try:
                    zpath, skipped = _zip_for(rec.run_id)
                    st.download_button("Download all (ZIP)", data=Path(zpath).read_bytes(), file_name=Path(zpath).name,
                                       key=f"{key}_zip", icon=":material/folder_zip:", on_click="ignore",
                                       help="POTCAR files are excluded." if skipped else None)
                except Exception as e:  # noqa: BLE001
                    st.caption(f"ZIP unavailable: {e}")
    with tabs[1]:
        if tabs[1].open:
            outputs.file_browser(rec, key, use_in=use_in_menu)
    with tabs[2]:
        if tabs[2].open:
            st.code(redact(tail_text(rec.path / "logs.txt", 200_000)) or "(empty)", language="text", height=420)
    with tabs[3]:
        if tabs[3].open:
            _inputs_tab(rec)
    with tabs[4]:
        if tabs[4].open:
            _provenance(rec)


@st.cache_data(ttl=60, show_spinner=False, max_entries=32)
def _zip_for(run_id: str) -> tuple[str, list[str]]:
    p, skipped = services().storage.export_run_zip(run_id)
    return str(p), skipped


def _live_body(run_id: str, key: str) -> None:
    svc = services()
    rec = svc.storage.get_run(run_id)
    if rec is None:
        st.warning("Run not found.")
        return
    meta = rec.meta
    if meta.get("status") not in ACTIVE:
        st.rerun()
    params = rec.inputs().get("params", {})
    pr = read_progress(rec.path, meta, params)
    with st.container(horizontal=True, vertical_alignment="center"):
        status_badge(meta["status"])
        st.markdown(f"**{meta.get('label') or meta.get('tool_title')}**")
        if pr.extra.get("elapsed_s") is not None:
            st.caption(f"Elapsed {human_duration(pr.extra['elapsed_s'])}")
        pos = svc.manager.queue_position(run_id)
        if pos:
            st.caption(f"Queue position {pos}")
        if st.button("Cancel", key=f"{key}_cancel", icon=":material/stop_circle:", type="secondary"):
            svc.manager.cancel(run_id)
            st.toast("Cancelling…")
    if pr.fraction is not None:
        st.progress(pr.fraction, text=pr.text or None)
    else:
        st.progress(0.0 if meta["status"] == "queued" else 0.02, text="Waiting for a worker…" if meta["status"] == "queued" else "Working… (this tool does not report progress)")
    live_tab, metrics_tab, inputs_tab = st.tabs(["Live log", "Progress & metrics", "Inputs & parameters"], key=f"{key}_ltabs")
    with live_tab:
        st.code(redact(tail_text(rec.path / "logs.txt", 30_000))[-12_000:] or "Starting…", language="text", height=320)
    with metrics_tab:
        if pr.md:
            md_log = next(Path(meta.get("session_dir") or rec.files_dir).rglob("masgent_mlps_md.log"), None)
            if md_log:
                outputs.md_view(md_log, f"{key}_live")
        if pr.opt:
            outputs.opt_view(pr.opt, f"{key}_live")
        if pr.trials:
            outputs.optuna_view(pr.trials, None, f"{key}_live")
        if pr.loss:
            outputs.loss_view(pr.loss, f"{key}_live")
        if pr.cvae:
            import pandas as pd

            st.line_chart(pd.DataFrame(pr.cvae).set_index("step")["loss"], x_label="batch", y_label="CVAE loss")
        if pr.extra.get("sqs_log"):
            st.code(pr.extra["sqs_log"][-4000:], language="text")
        if not (pr.md or pr.opt or pr.trials or pr.loss or pr.cvae or pr.extra.get("sqs_log")):
            st.caption("Live metrics appear here for MLP relaxations/MD, Optuna, training and SQS.")
    with inputs_tab:
        st.json(params, expanded=False)


@st.fragment(run_every=1.5)
def live_dock(run_id: str, key: str) -> None:
    _live_body(run_id, key)


def results_dock(tool_id: str, run_id: str | None, key: str) -> None:
    svc = services()
    if not run_id:
        st.caption("Results appear here when you press **Run**. Every run is saved to the Results library.")
        rows, _ = svc.storage.query_runs(tools=[tool_id], limit=5)
        if rows:
            st.markdown("**Recent runs of this tool**")
            for r in rows:
                with st.container(horizontal=True, vertical_alignment="center"):
                    status_badge(r["status"])
                    st.caption(f"{r.get('label') or r['run_id']} · {r['created'][:16].replace('T', ' ')}")
                    if st.button("Open", key=f"{key}_open_{r['run_id']}", type="tertiary"):
                        set_tool_run(tool_id, r["run_id"])
                        st.rerun()
        return
    rec = svc.storage.get_run(run_id)
    if rec is None:
        st.info("That run no longer exists (deleted?).")
        set_tool_run(tool_id, None)
        return
    if rec.status in ACTIVE:
        live_dock(run_id, key)
    else:
        render_run(rec, key)


def params_preview(params: dict[str, Any]) -> str:
    from core.examples import ref_label

    return json.dumps({k: (ref_label(v) if isinstance(v, str) and (":" in v[:20] or "/" in v or "\\" in v) else v)
                       for k, v in params.items()}, default=str)
