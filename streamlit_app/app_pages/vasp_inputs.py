"""VASP inputs: full input sets, INCAR templates, KPOINTS, Slurm script."""
import streamlit as st

from ui.common import vasp_banner
from ui.tool_page import render_tool, tool_selector

st.title("VASP inputs", anchor=False)
vasp_banner()
tid = tool_selector("vasp", ["generate_vasp_inputs_from_poscar", "generate_vasp_inputs_from_poscar:incar",
                             "customize_vasp_kpoints_with_accuracy", "generate_vasp_inputs_hpc_slurm_script"])
render_tool(tid)
