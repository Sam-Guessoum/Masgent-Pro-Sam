"""Help & glossary: plain-language terms and a per-tool 'what is this' index."""
import streamlit as st

from core.catalog import PAGES
from core.help_content import GLOSSARY
from core.registry import ui_tools
from ui.common import PAGE_FILES
from ui.search import search

st.title("Help & glossary", anchor=False)
q = st.text_input("Search help", placeholder="e.g. bulk modulus, NEB, POTCAR, supercell", key="help_q")

tools_tab, gloss_tab, faq_tab = st.tabs(["Every tool", "Glossary", "FAQ"])
with tools_tab:
    hits = {h[2] for h in search(q)} if q else None
    by_page: dict[str, list] = {}
    for t in ui_tools().values():
        if hits is None or t.id in hits:
            by_page.setdefault(t.page, []).append(t)
    for page, tools in by_page.items():
        st.markdown(f"#### {PAGES.get(page, (page,))[0]}")
        for t in tools:
            with st.expander(f"{t.title} · {t.summary}"):
                st.markdown(t.help)
                st.caption(f"Masgent CLI {t.cli or '–'} · function `{t.func}` · runtime: {t.runtime}")
                if st.button("Open this tool", key=f"help_open_{t.id}", icon=":material/arrow_forward:"):
                    st.session_state["focus_tool"] = t.id
                    st.switch_page(PAGE_FILES[t.page])
with gloss_tab:
    for term, text in GLOSSARY.items():
        if not q or q.lower() in (term + text).lower():
            st.markdown(f"**{term}**: {text}")
with faq_tab:
    st.markdown("""
**Where are my results?** Everything is in the workspace (Settings shows the path), one folder per run with `meta.json`, `inputs.json`,
`result.json`, `logs.txt`, `env.json`, `manifest.sha256` and `files/`. The **Results library** searches them all.

**Does the app run VASP?** No. It prepares input folders (with a Slurm script) and analyses outputs you upload as a ZIP.

**Why do I see `POTCAR.spec` instead of `POTCAR`?** POTCARs are licensed. Without a configured POTCAR library the app writes a
small text file listing exactly which POTCARs to concatenate on your cluster.

**Can I close the browser while something runs?** Yes. Runs execute in separate worker processes and keep going; reopen the app and
find them in the library. If the whole app (server) is stopped, a still-running worker is re-attached on the next start.

**How do I cancel?** Press **Cancel** in the results panel (or in the library). The worker process is terminated.

**Is my API key safe?** It lives only in `streamlit_app/.env`, is passed to worker processes through memory, and is redacted from
logs, reports and diagnostic texts.
""")
