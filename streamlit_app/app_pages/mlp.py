"""Machine-learning potentials: SevenNet, CHGNet, Orb-v3, MatterSim × single/EOS/elastic/MD."""
import streamlit as st

from core.capabilities import gpu_info, quick_status
from ui.state import services
from ui.tool_page import render_tool

st.title("ML potentials", anchor=False)
svc = services()

MODELS = [
    ("SevenNet", "sevenn", "7net-0 universal potential (graph NN). Model bundled with the package.", True),
    ("CHGNet", "chgnet", "Charge-informed graph NN trained on MPtrj. Model bundled; good default.", True),
    ("Orb-v3", "orb", "Orb v3 conservative (OMat). Downloads weights on first use; Masgent pins it to CPU.", False),
    ("MatterSim", "mattersim", "Microsoft MatterSim v1. Downloads a checkpoint on first use.", True),
]
g = gpu_info(svc.workspace / "cache" / "capabilities.json", max_age_s=86400)
gpu_ok = bool(g.get("cuda_available")) and g.get("gpu_supported", True)
cols = st.columns(4)
for col, (name, key, desc, gpu_capable) in zip(cols, MODELS):
    s = quick_status(key)
    with col.container(border=True, height="stretch"):
        st.markdown(f"**{name}**")
        if s.available:
            st.badge(f"Installed {s.version}", color="green", icon=":material/check:")
        else:
            st.badge("Unavailable", color="red", icon=":material/block:")
            st.caption(f"{s.reason}. Install: `{s.install}`")
        st.caption(desc)
        st.caption(f"Device: {'GPU' if (gpu_ok and gpu_capable) else 'CPU'}" + ("" if gpu_capable else " (fixed)"))
if not gpu_ok:
    st.caption(":material/memory: Running on CPU: torch cannot see a supported GPU. See **Setup doctor** for the CUDA 13.0 install command.")
render_tool("run_simulation_using_mlps")
