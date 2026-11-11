"""ML toolkit: dataset profiling, features, PCA, CVAE, Optuna, training, pretrained models."""
from pathlib import Path

import pandas as pd
import streamlit as st

from core.parsers import read_csv

from core import examples
from ui.tool_page import render_tool, tool_selector
from ui.widgets import csv_picker

st.title("ML toolkit", anchor=False)

SECTIONS = {
    "Dataset": ["analyze_features_for_machine_learning"],
    "PCA": ["reduce_dimensions_for_machine_learning"],
    "Augment": ["augment_data_for_machine_learning"],
    "Design": ["design_model_for_machine_learning"],
    "Train": ["train_model_for_machine_learning", "retrain_model_for_machine_learning"],
    "Pretrained": ["model_prediction_for_AlMgSiSc", "model_prediction_for_AlCoCrFeNi"],
}
focus = st.session_state.get("focus_tool")
for sec, ids in SECTIONS.items():
    if focus in ids:
        st.session_state["ml_section"] = sec
st.session_state.setdefault("ml_section", "Dataset")
sec = st.segmented_control("Section", list(SECTIONS), key="ml_section", label_visibility="collapsed", required=True)
st.caption("Typical pipeline: **Dataset** (inspect) → optional **PCA** / **Augment** → **Design** (Optuna) → **Train** → predict.  "
           "The **Pretrained** models work immediately.")

if sec == "Dataset":
    st.session_state.pop("focus_tool", None)
    with st.expander("Preview & profile a CSV", icon=":material/table_view:", expanded=False):
        p = csv_picker("CSV file", "ml_profile_csv", "example_ml:AlCoCrFeNi inputs (compositions)")
        real = examples.resolve_ref(p) if p else None
        if real and Path(real).exists():
            df = read_csv(real)
            c = st.columns(4)
            c[0].metric("Rows", len(df), border=True)
            c[1].metric("Columns", df.shape[1], border=True)
            c[2].metric("Missing values", int(df.isna().sum().sum()), border=True)
            c[3].metric("Duplicate rows", int(df.duplicated().sum()), border=True)
            st.dataframe(df.head(100), hide_index=True)
            st.markdown("**Column statistics**")
            st.dataframe(df.describe().T, width="stretch")
            non_num = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
            if non_num:
                st.warning(f"Non-numeric columns {non_num}: Masgent's ML tools expect numeric features only.")
    render_tool("analyze_features_for_machine_learning")
elif sec in ("PCA", "Augment", "Design"):
    st.session_state.pop("focus_tool", None)
    render_tool(SECTIONS[sec][0])
else:
    tid = tool_selector(f"ml_{sec}", SECTIONS[sec])
    render_tool(tid)
