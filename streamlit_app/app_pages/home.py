"""Home / dashboard."""
import streamlit as st

from core.capabilities import quick_all
from core.settings import get_secret, load_settings, mask, update_settings
from ui.common import PAGE_FILES, human_duration, human_size, status_badge
from ui.state import handoff, services, set_tool_run

svc = services()
settings = load_settings()

st.title("Masgent", anchor=False)
st.caption("Materials simulation agent: build structures, prepare and analyse VASP workflows, run ML potentials and ML models, or ask the AI assistant.")


def start_sample() -> str:
    """Al fcc → 2×2×2 supercell → CHGNet single point on CPU (≈ 1 minute)."""
    first = svc.manager.submit("generate_supercell_from_poscar",
                               {"poscar_path": "example:fcc Al (mp-134)", "scaling_matrix": "2 0 0; 0 2 0; 0 0 2"},
                               project="sample-project", label="Sample: Al 2×2×2 supercell", tags=["sample"])

    def second(rec):  # noqa: ANN001
        poscar = rec.files_dir / "supercell" / "POSCAR"
        svc.manager.submit("run_simulation_using_mlps", {"poscar_path": str(poscar), "mlps_type": "CHGNet", "task_type": "single"},
                           project="sample-project", label="Sample: CHGNet single point on Al supercell", tags=["sample"],
                           parent_run=rec.run_id, device="cpu")
    svc.manager.then(first, second)
    return first


@st.dialog("Welcome to Masgent", width="large")
def welcome() -> None:
    st.markdown(
        "**1 · Build or fetch a structure.** Start on *Build structure*: pick an example, upload a CIF/POSCAR, or download from the Materials Project.\n\n"
        "**2 · Run a tool.** Every tool has a form on the left and a live **Results** panel on the right. *Use in…* passes outputs to the next tool.\n\n"
        "**3 · Find everything later.** Each run is saved with its inputs, logs, outputs and provenance in the **Results library**, even after you restart the app.")
    st.info("Try the sample project: an fcc Al 2×2×2 supercell followed by a CHGNet single-point energy on CPU (about a minute).", icon=":material/rocket_launch:")
    c1, c2 = st.columns(2)
    if c1.button("Run the sample project", type="primary", width="stretch", icon=":material/play_arrow:"):
        start_sample()
        update_settings(onboarding_done=True)
        st.session_state["project"] = "sample-project"
        st.rerun()
    if c2.button("Skip the tour", width="stretch"):
        update_settings(onboarding_done=True)
        st.rerun()


if not settings.onboarding_done and not st.session_state.get("_welcome_shown"):
    st.session_state["_welcome_shown"] = True
    welcome()

# --------------------------------------------------------------- quick start
st.subheader("Quick start", anchor=False)
cards = [
    ("Get a structure", "Materials Project, examples, uploads, 3D viewer.", "build", ":material/deployed_code:"),
    ("ML potential", "Relax, EOS, elastic or MD in seconds with CHGNet & co.", "mlp", ":material/bolt:"),
    ("VASP workflow", "Convergence, EOS, elastic, AIMD, NEB: generate & analyse.", "workflows", ":material/account_tree:"),
    ("Ask the AI", "Describe the task; approve the plan; watch it run.", "assistant", ":material/smart_toy:"),
]
cols = st.columns(4)
for col, (title, desc, page, icon) in zip(cols, cards):
    with col.container(border=True, height="stretch"):
        st.markdown(f"**{icon} {title}**")
        st.caption(desc)
        st.page_link(PAGE_FILES[page], label="Open", icon=":material/arrow_forward:")

st.markdown("**Try an example**")
with st.container(horizontal=True):
    if st.button("Sample project (≈ 1 min)", icon=":material/rocket_launch:"):
        start_sample()
        st.session_state["project"] = "sample-project"
        st.toast("Sample project started: watch it below or in the Results library.")
    examples_btn = [
        ("2×2×2 Al supercell", "generate_supercell_from_poscar", {"poscar_path": "example:fcc Al (mp-134)", "scaling_matrix": "2 0 0; 0 2 0; 0 0 2"}),
        ("CHGNet EOS of Al", "run_simulation_using_mlps", {"poscar_path": "example:fcc Al (mp-134)", "mlps_type": "CHGNet", "task_type": "eos"}),
        ("HEA prediction", "model_prediction_for_AlCoCrFeNi", {"Al": 20.0, "Co": 20.0, "Cr": 20.0, "Fe": 20.0}),
        ("Analyse example EOS", "analyze_vasp_workflow_of_eos", {"eos_dir": "example_folder:eos"}),
    ]
    from core.registry import ui_tools

    for i, (lab, tid, params) in enumerate(examples_btn):
        if st.button(lab, key=f"ex_{i}"):
            handoff(tid, params)
            st.switch_page(PAGE_FILES[ui_tools()[tid].page])

# ------------------------------------------------------------- recent runs
left, right = st.columns([3, 2], gap="large")
with left:
    st.subheader("Recent runs", anchor=False)

    @st.fragment(run_every=3)
    def recent() -> None:
        rows, total = svc.storage.query_runs(limit=10)
        if not rows:
            st.caption("No runs yet. Pick a quick-start card above.")
            return
        for r in rows:
            with st.container(horizontal=True, vertical_alignment="center"):
                status_badge(r["status"])
                st.markdown(f"{r.get('label') or r.get('tool_title') or r['tool']}")
                st.caption(f"{r['created'][5:16].replace('T', ' ')} · {human_duration(r.get('duration_s'))} · {r['project']}")
                if st.button("Open", key=f"home_open_{r['run_id']}", type="tertiary"):
                    st.session_state["library_open"] = r["run_id"]
                    st.switch_page(PAGE_FILES["library"])
        st.caption(f"{total} runs in total.")
    recent()

with right:
    st.subheader("System status", anchor=False)
    caps = quick_all()
    mp = get_secret("MP_API_KEY")
    with st.container(border=True):
        st.markdown(f"{':green[:material/check_circle:]' if mp else ':orange[:material/warning:]'} Materials Project key: `{mask(mp)}`")
        from core.capabilities import gpu_info

        g = gpu_info(svc.workspace / "cache" / "capabilities.json", max_age_s=86400)
        if g.get("cuda_available"):
            st.markdown(f":green[:material/check_circle:] GPU: {g.get('gpu_name')} (torch {g.get('torch')})")
        else:
            st.markdown(f":orange[:material/memory:] GPU not used: torch {g.get('torch', 'n/a')} is a CPU build. See *Setup doctor*.")
        mlps = {"sevenn": "SevenNet", "chgnet": "CHGNet", "orb": "Orb-v3", "mattersim": "MatterSim"}
        st.markdown("ML potentials: " + " · ".join(f"{':green' if caps[k].available else ':red'}[{v}]" for k, v in mlps.items()))
        others = {"icet": "SQS (icet)", "optuna": "Optuna", "pydantic_ai": "AI agent", "defects": "Interstitials"}
        st.markdown("Other: " + " · ".join(f"{':green' if caps[k].available else ':red'}[{v}]" for k, v in others.items()))
        llm = [k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GROK_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY") if get_secret(k)]
        st.markdown(f"LLM keys: {', '.join(k.split('_')[0].title() for k in llm) or 'none (Masgent AI needs none)'}")

        @st.cache_data(ttl=60, show_spinner=False)
        def usage(ws: str) -> int:
            return svc.storage.usage_bytes()
        st.caption(f"Workspace `{svc.workspace}` · {human_size(usage(str(svc.workspace)))} used")
        st.page_link(PAGE_FILES["doctor"], label="Run the setup doctor", icon=":material/health_and_safety:")
_ = set_tool_run
