"""AI assistant: chat with Masgent's agent; Plan → Confirm → Execute as UI."""
import streamlit as st

from core.agent import PROVIDERS, ChatSession
from core.capabilities import quick_status
from core.settings import get_secret, load_settings, update_settings
from ui.common import PAGE_FILES, status_badge
from ui.state import services

svc = services()
st.title("AI assistant", anchor=False)

if not quick_status("pydantic_ai").available:
    st.error("The AI assistant needs `pydantic-ai`. Install: `uv pip install pydantic-ai`.", icon=":material/block:")
    st.stop()

with st.sidebar:
    st.markdown("**Assistant**")
    provs = list(PROVIDERS)
    saved = load_settings().ai_provider
    st.session_state.setdefault("ai_provider", saved if saved in provs else provs[0])
    prov = st.selectbox("Model provider", provs, key="ai_provider")
    spec = PROVIDERS[prov]
    if spec.get("key") and not get_secret(spec["key"]):
        st.warning(f"Needs `{spec['key']}`: add it in Settings.", icon=":material/key:")
    if spec.get("note"):
        st.caption(spec["note"])
    if st.button("New conversation", icon=":material/add_comment:"):
        st.session_state.pop("chat", None)
        st.rerun()

chat: ChatSession | None = st.session_state.get("chat")
if chat is None or chat.provider != prov or chat.project != st.session_state.get("project"):
    if chat is None or not chat.busy:
        try:
            chat = ChatSession(svc.manager, svc.storage, st.session_state.get("project", "default"), prov)
            st.session_state["chat"] = chat
            update_settings(ai_provider=prov)
        except Exception as e:  # noqa: BLE001
            st.error(str(e))
            st.stop()

st.caption("Protocol: the assistant first proposes a **plan** → you **approve**, edit or cancel → only then it executes. "
           "Every tool call becomes a normal run in the Results library.")


def tool_steps(turn) -> None:  # noqa: ANN001
    for t in turn.tool_runs:
        label = f"`{t['tool']}`"
        state = {"succeeded": "complete", "failed": "error", "cancelled": "error"}.get(t.get("status"), "running")
        with st.status(label, state=state, type="step"):
            st.json(t.get("params", {}), expanded=False)
            if t.get("run_id"):
                with st.container(horizontal=True, vertical_alignment="center"):
                    status_badge(t.get("status", "running"))
                    if st.button("Open run", key=f"open_{t['run_id']}", type="tertiary", icon=":material/open_in_new:"):
                        st.session_state["library_open"] = t["run_id"]
                        st.switch_page(PAGE_FILES["library"])


def render_history() -> None:
    for i, turn in enumerate(chat.turns):
        if turn.role == "user":
            with st.chat_message("user"):
                st.markdown(turn.text)
            continue
        with st.chat_message("assistant", avatar=":material/smart_toy:"):
            if turn.tool_runs:
                tool_steps(turn)
            if turn.text:
                st.markdown(turn.text)
            elif chat.busy and i == len(chat.turns) - 1:
                st.markdown(":shimmer[Thinking…]")
            if turn.error:
                st.error(turn.error, icon=":material/error:")


@st.fragment(run_every=1.0)
def live() -> None:
    render_history()
    if chat.busy:
        if st.button("Stop", icon=":material/stop_circle:", key="chat_stop"):
            chat.cancel()
    else:
        st.rerun()


if not chat.turns:
    st.markdown("**Try asking:**")
    SUGG = ["Generate a POSCAR file for NaCl.", "Build a 2×2×2 supercell of the example fcc Al and relax it with CHGNet.",
            "Prepare full VASP input files for LaCoO3.", "Predict the properties of an equiatomic AlCoCrFeNi alloy."]
    with st.container(horizontal=True):
        for s_ in SUGG:
            if st.button(s_, key=f"sugg_{s_[:12]}"):
                chat.submit(s_)
                st.rerun()

if chat.busy:
    live()
else:
    render_history()
    last = chat.turns[-1] if chat.turns else None
    if last and last.role == "assistant" and last.plan:
        with st.container(border=True):
            st.markdown(":material/assignment: **The assistant proposed a plan.** Review it above, then:")
            with st.container(horizontal=True):
                if st.button("Approve & execute", type="primary", icon=":material/check:"):
                    chat.submit("Approved. Please proceed with the plan exactly as stated.", approve=True)
                    st.rerun()
                edit = st.popover("Edit parameters", icon=":material/edit:")
                with edit:
                    change = st.text_area("What should change?", placeholder="e.g. use a 3×3×3 supercell and MatterSim instead")
                    if st.button("Send changes", disabled=not change):
                        chat.submit(f"Please update the plan: {change}. Show me the revised plan before executing.")
                        st.rerun()
                if st.button("Cancel plan", icon=":material/close:"):
                    chat.submit("Cancel this plan. Do not execute anything.")
                    st.rerun()

prompt = st.chat_input("Ask Masgent… (e.g. 'add 2 oxygen vacancies to LaCoO3')", disabled=chat.busy, key="chat_in")
if prompt:
    try:
        chat.submit(prompt)
    except RuntimeError as e:
        st.warning(str(e))
    st.rerun()
st.caption(f"Conversation log: `{chat.dir / 'conversation.jsonl'}` · session files: `{chat.session_dir}`")
