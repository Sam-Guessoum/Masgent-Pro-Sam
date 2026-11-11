"""DFT workflows: generate inputs and analyse outputs for 5 standard workflows."""
import streamlit as st

from ui.common import vasp_banner
from ui.tool_page import render_tool

st.title("DFT workflows", anchor=False)
vasp_banner()

WF = {
    "Convergence": ("generate_vasp_workflow_of_convergence_tests", "analyze_vasp_workflow_of_convergence_tests"),
    "EOS": ("generate_vasp_workflow_of_eos", "analyze_vasp_workflow_of_eos"),
    "Elastic": ("generate_vasp_workflow_of_elastic_constants", "analyze_vasp_workflow_of_elastic_constants"),
    "AIMD": ("generate_vasp_workflow_of_aimd", "analyze_vasp_workflow_of_aimd"),
    "NEB": ("generate_vasp_workflow_of_neb", "analyze_vasp_workflow_of_neb"),
}
focus = st.session_state.pop("focus_tool", None)
for wf, (g, a) in WF.items():
    if focus in (g, a):
        st.session_state["wf_section"] = wf
        st.session_state["wf_mode"] = "Generate" if focus == g else "Analyze"

with st.container(horizontal=True, vertical_alignment="center"):
    st.session_state.setdefault("wf_section", "Convergence")
    st.session_state.setdefault("wf_mode", "Generate")
    wf = st.segmented_control("Workflow", list(WF), key="wf_section", label_visibility="collapsed", required=True)
    mode = st.segmented_control("Mode", ["Generate", "Analyze"], key="wf_mode", label_visibility="collapsed", required=True)
gen, ana = WF[wf]
render_tool(gen if mode == "Generate" else ana)
