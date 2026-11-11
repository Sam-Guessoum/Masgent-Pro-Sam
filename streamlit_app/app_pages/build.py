"""Build structure: Materials Project, conversions, supercells, 3D viewer."""
from pathlib import Path

import pandas as pd
import streamlit as st

from core import examples
from core.mp_client import MPClient, MPError
from core.settings import get_secret, load_settings
from ui.common import PAGE_FILES
from ui.state import handoff, services
from ui.tool_page import render_tool, tool_selector
from ui.viewer3d import show_structure
from ui.widgets import structure_picker

svc = services()
st.title("Build structure", anchor=False)

_SECTION_OF = {"generate_vasp_poscar": "Materials Project", "convert_structure_format": "Convert", "convert_poscar_coordinates": "Convert",
               "generate_supercell_from_poscar": "Supercell", "visualize_structure_from_poscar": "Viewer"}
focus = st.session_state.get("focus_tool")
if focus in _SECTION_OF:
    st.session_state["build_section"] = _SECTION_OF[focus]  # set before the widget is created
st.session_state.setdefault("build_section", "Materials Project")
section = st.segmented_control(
    "Section", ["Materials Project", "Convert", "Supercell", "Viewer"], key="build_section",
    label_visibility="collapsed", required=True)

if section == "Materials Project":
    want = "Masgent download (all polymorphs)" if st.session_state.pop("focus_tool", None) == "generate_vasp_poscar" else None
    browse, masgent = st.tabs(["Search & pick a polymorph", "Masgent download (all polymorphs)"], key="mp_tabs", on_change="rerun",
                              default=want)
    with browse:
        if browse.open:
            if not get_secret("MP_API_KEY"):
                st.warning("Add your Materials Project API key in **Settings** to search (free at materialsproject.org).", icon=":material/key:")
                st.page_link(PAGE_FILES["settings"], label="Open Settings", icon=":material/settings:")
            client = MPClient(svc.workspace / "cache" / "mp", ttl_days=load_settings().mp_cache_days)
            with st.form("mp_search", border=False):
                with st.container(horizontal=True, vertical_alignment="bottom"):
                    formula = st.text_input("Formula", value=st.session_state.get("mp_formula", "Al"), placeholder="e.g. Al, NaCl, LiFePO4")
                    refresh = st.checkbox("Refresh cache", help="Ignore the on-disk cache and query the API again.")
                    go = st.form_submit_button("Search", type="primary", icon=":material/search:")
            if go:
                st.session_state["mp_formula"] = formula
                st.session_state["mp_refresh"] = refresh
            f = st.session_state.get("mp_formula")
            if f and get_secret("MP_API_KEY"):
                try:
                    with st.spinner(f"Querying the Materials Project for {f}…"):
                        rows = client.search_formula(f, refresh=st.session_state.pop("mp_refresh", False))
                    ts = client.cache_info("summary", f)
                    if not rows:
                        st.info(f"No entries found for {f}.")
                    else:
                        import datetime as dt

                        st.caption(f"{len(rows)} entries · sorted by energy above hull · cached {dt.datetime.fromtimestamp(ts):%Y-%m-%d %H:%M}" if ts else "")
                        df = pd.DataFrame(rows)
                        ev = st.dataframe(df, hide_index=True, on_select="rerun", selection_mode="single-row", key="mp_table",
                                          column_config={"e_above_hull (eV/atom)": st.column_config.NumberColumn(format="%.4f"),
                                                         "volume (Å³)": st.column_config.NumberColumn(format="%.2f"),
                                                         "density (g/cm³)": st.column_config.NumberColumn(format="%.3f"),
                                                         "band_gap (eV)": st.column_config.NumberColumn(format="%.3f")})
                        sel = ev.selection.rows if ev and ev.selection else []
                        if sel:
                            mid = rows[sel[0]]["material_id"]
                            conventional = st.toggle("Conventional cell", value=True, key="mp_conv")
                            poscar = client.structure_poscar(mid, conventional=conventional)
                            path = svc.storage.save_upload(f"POSCAR_{f}_{mid}", poscar.encode())
                            c1, c2 = st.columns([3, 2])
                            with c1:
                                show_structure(path, key=f"mp3d_{mid}", height=360)
                            with c2:
                                st.markdown(f"**{mid}** · {rows[sel[0]]['space_group']} · E_hull {rows[sel[0]]['e_above_hull (eV/atom)']:.4f} eV/atom")
                                if st.button("Use this structure", type="primary", icon=":material/check:"):
                                    st.session_state["active_structure"] = str(path)
                                    st.toast(f"{mid} is now the current structure. Every structure picker offers it under 'Current'.")
                                st.download_button("Download POSCAR", poscar, file_name=f"POSCAR_{mid}", on_click="ignore", icon=":material/download:")
                                with st.expander("POSCAR"):
                                    st.code(poscar, language="text")
                        else:
                            st.caption("Select a row to preview it in 3D and use it.")
                except MPError as e:
                    st.error(str(e), icon=":material/cloud_off:")
    with masgent:
        if masgent.open:
            render_tool("generate_vasp_poscar")

elif section == "Convert":
    tid = tool_selector("build_convert", ["convert_structure_format", "convert_poscar_coordinates"])
    render_tool(tid)
elif section == "Supercell":
    st.session_state.pop("focus_tool", None)
    render_tool("generate_supercell_from_poscar")
else:
    want = "Masgent HTML export" if st.session_state.pop("focus_tool", None) == "visualize_structure_from_poscar" else None
    view, export = st.tabs(["Interactive 3D viewer", "Masgent HTML export"], key="viewer_tabs", on_change="rerun", default=want)
    with view:
        if view.open:
            p = structure_picker("Structure to view", "viewer_pick", st.session_state.get("active_structure") or "example:fcc Al (mp-134)", preview=False)
            real = examples.resolve_ref(p) if p else None
            if real and Path(real).exists():
                show_structure(real, key="viewer_main", height=520)
                if st.button("Set as current structure", icon=":material/push_pin:"):
                    st.session_state["active_structure"] = p
                    st.toast("Saved as current structure")
    with export:
        if export.open:
            render_tool("visualize_structure_from_poscar")
_ = handoff
