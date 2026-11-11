"""Surfaces & interfaces."""
import streamlit as st

from ui.tool_page import render_tool, tool_selector

st.title("Surfaces & interfaces", anchor=False)
tid = tool_selector("surfaces", ["generate_surface_slab_from_poscar", "generate_interface_from_poscars"])
render_tool(tid)
