"""Settings: keys (masked), workspace, device, concurrency, POTCAR library, theme."""
from pathlib import Path

import streamlit as st

from core.mp_client import MPClient
from core.settings import SECRET_KEYS, get_secret, load_settings, mask, set_secret, update_settings
from ui.state import services

svc = services()
s = load_settings()
st.title("Settings", anchor=False)
st.caption("Keys are stored only in `streamlit_app/.env` on this computer. They are never written to runs, logs or exports, and are shown masked.")

st.subheader("Materials Project", anchor=False)
with st.container(border=True):
    cur = get_secret("MP_API_KEY")
    st.markdown(f"Current key: `{mask(cur)}`")
    with st.form("mp_key", border=False):
        c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
        new = c1.text_input("New MP_API_KEY", type="password", placeholder="Paste a new key (leave empty to keep the current one)")
        if c2.form_submit_button("Save", type="primary"):
            if new.strip():
                set_secret("MP_API_KEY", new.strip())
                st.toast("Saved MP_API_KEY")
                st.rerun()
    with st.container(horizontal=True):
        if st.button("Test connection", icon=":material/wifi_tethering:", disabled=not cur):
            with st.spinner("Fetching mp-134 (Al)…"):
                ok, msg = MPClient(svc.workspace / "cache" / "mp").test_connection()
            (st.success if ok else st.error)(msg)
        if cur and st.button("Remove key", icon=":material/key_off:"):
            set_secret("MP_API_KEY", None)
            st.rerun()
    st.caption("Get a free key at https://materialsproject.org/api. MP responses are cached on disk for "
               f"{s.mp_cache_days} days.")

st.subheader("AI providers", anchor=False)
with st.container(border=True):
    st.caption("Only needed for the AI assistant. The hosted 'Masgent AI' provider needs no key (it sends prompts to the Masgent team's server).")
    for key, label in SECRET_KEYS.items():
        if key == "MP_API_KEY":
            continue
        with st.form(f"form_{key}", border=False):
            c1, c2, c3 = st.columns([2, 4, 1], vertical_alignment="bottom")
            c1.markdown(f"**{label}**  \n`{key}` = `{mask(get_secret(key))}`")
            v = c2.text_input("New value", type="password", key=f"in_{key}", label_visibility="collapsed", placeholder="Paste key")
            if c3.form_submit_button("Save") and v.strip():
                set_secret(key, v.strip())
                st.rerun()

st.subheader("Computation", anchor=False)
with st.form("compute"):
    c1, c2, c3 = st.columns(3)
    device = c1.segmented_control("Default device", ["auto", "cpu", "cuda"], default=s.device, required=True,
                                  help="'auto' uses a supported GPU when torch can see one, otherwise CPU.")
    conc = c2.number_input("Concurrent runs", 1, 8, int(s.max_concurrent), help="GPU jobs always run one at a time.")
    seed = c3.number_input("Random seed", 0, 2**31 - 1, int(s.seed), help="Used for random defects, MD velocities and ML; stored with every run.")
    psp = st.text_input("POTCAR library (PMG_VASP_PSP_DIR)", value=s.psp_dir,
                        help="Optional folder with your licensed VASP pseudopotentials in pymatgen layout. Empty = write POTCAR.spec placeholders.")
    lim = st.number_input("Upload size limit (MB)", 1, 5000, int(s.upload_limit_mb))
    if st.form_submit_button("Save", type="primary"):
        if psp and not Path(psp).is_dir():
            st.error("POTCAR folder not found.")
        else:
            update_settings(device=device, max_concurrent=int(conc), seed=int(seed), psp_dir=psp.strip(), upload_limit_mb=int(lim))
            st.session_state["upload_limit_mb"] = int(lim)
            st.toast("Saved")

st.subheader("Workspace", anchor=False)
with st.form("ws"):
    st.caption(f"Current: `{svc.workspace}`. Runs, uploads, exports and the index live here (gitignored).")
    ws = st.text_input("Workspace folder", value=s.workspace or "", placeholder=str(svc.workspace))
    if st.form_submit_button("Change workspace"):
        p = Path(ws).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            update_settings(workspace=str(p.resolve()))
            st.success("Saved. The new workspace is used from the next page load.")
        except OSError as e:
            st.error(f"Cannot use that folder: {e}")

st.subheader("Appearance", anchor=False)
st.caption("Light/dark theme: use the ⋮ menu (top right) → Settings → Theme. The default follows your system.")
if st.button("Show the welcome tour again", icon=":material/tour:"):
    update_settings(onboarding_done=False)
    st.session_state.pop("_welcome_shown", None)
    st.switch_page("app_pages/home.py")
