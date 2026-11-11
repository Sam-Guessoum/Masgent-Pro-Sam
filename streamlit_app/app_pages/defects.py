"""Defects & alloys: vacancies, substitutions, interstitials, SQS."""
import streamlit as st

from ui.tool_page import render_tool, tool_selector

st.title("Defects & alloys", anchor=False)
tid = tool_selector("defects", ["generate_vasp_poscar_with_vacancy_defects", "generate_vasp_poscar_with_substitution_defects",
                                "generate_vasp_poscar_with_interstitial_defects", "generate_sqs_from_poscar"])
render_tool(tid)
