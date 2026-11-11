"""Tools found in Masgent that have no hand-written UI yet (auto-generated forms)."""
import streamlit as st

from core.registry import tools_on_page
from ui.tool_page import render_tool, tool_selector

st.title("More tools", anchor=False)
auto = [t.id for t in tools_on_page("more")]
if not auto:
    st.success("Every Masgent tool currently has a dedicated page: nothing extra to show.", icon=":material/check_circle:")
    st.caption("When a future Masgent version adds a tool to `masgent/utils/tools.py`, it appears here automatically "
               "with a form generated from its signature and Pydantic schema.")
else:
    st.info(f"{len(auto)} Masgent tool(s) without hand-written UI metadata. Forms are generated automatically.", icon=":material/auto_awesome:")
    render_tool(tool_selector("more", auto))
