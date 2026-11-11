"""Setup doctor: checks versions, backends, GPU, keys, workspace; prints exact fixes."""
import streamlit as st

from core.diagnostics import run_checks
from ui.state import services

svc = services()
st.title("Setup doctor", anchor=False)
st.caption("Quick checks run instantly. The deep check imports every backend in a separate process (can take a minute).")
deep = st.button("Run deep check (import every backend)", icon=":material/stethoscope:")
with st.spinner("Checking…" if not deep else "Importing every backend in isolated processes…"):
    checks = run_checks(svc.workspace, deep=deep)
n_bad = sum(c.ok is False for c in checks)
n_warn = sum(c.ok is None for c in checks)
if n_bad:
    st.error(f"{n_bad} problem(s) need fixing.", icon=":material/error:")
elif n_warn:
    st.warning(f"Working, with {n_warn} optional item(s) to improve.", icon=":material/warning:")
else:
    st.success("Everything looks good.", icon=":material/check_circle:")
for c in checks:
    icon = ":green[:material/check_circle:]" if c.ok else (":red[:material/cancel:]" if c.ok is False else ":orange[:material/info:]")
    with st.container(border=True):
        st.markdown(f"{icon} **{c.name}**: {c.detail}")
        if c.fix:
            st.code(c.fix, language="powershell", wrap_lines=True)
