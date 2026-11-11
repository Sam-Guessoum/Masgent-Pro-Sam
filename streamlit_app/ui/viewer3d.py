"""3D structure / trajectory viewer (3Dmol.js inside ``st.iframe``)."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.structures import trajectory_frames, viewer_html


def _dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


@st.cache_data(max_entries=64, show_spinner=False)
def _html(path: str, mtime: float, height: int, style: str, cell: bool, supercell: tuple[int, int, int], dark: bool, labels: bool) -> str:
    return viewer_html(path, height=height, style=style, show_cell=cell, supercell=supercell, dark=dark, labels=labels)


def show_structure(path: str | Path, key: str, height: int = 420, controls: bool = True) -> None:
    p = Path(path)
    style, cell, sc, labels = "ball-and-stick", True, (1, 1, 1), False
    if controls:
        with st.container(horizontal=True, vertical_alignment="bottom"):
            style = st.segmented_control("Style", ["ball-and-stick", "spacefill", "stick"], default="ball-and-stick",
                                         key=f"{key}_style", label_visibility="collapsed", required=True)
            cell = st.toggle("Unit cell", value=True, key=f"{key}_cell")
            labels = st.toggle("Labels", value=False, key=f"{key}_lab")
            rep = st.number_input("Repeat", min_value=1, max_value=4, value=1, key=f"{key}_rep",
                                  help="Show n×n×n periodic images (display only).")
            sc = (int(rep),) * 3
    try:
        html = _html(str(p), p.stat().st_mtime, height, style, cell, sc, _dark(), labels)
        st.iframe(html, height=height + 8, alt=f"3D view of {p.name}")
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not display {p.name}: {e}")


@st.cache_data(max_entries=8, show_spinner="Loading trajectory…")
def _traj_html(path: str, mtime: float, height: int, dark: bool) -> tuple[str, int]:
    frames, lat, n = trajectory_frames(path)
    return viewer_html(frames_xyz=frames, lattice=lat, height=height, dark=dark, animate=True), n


def show_trajectory(path: str | Path, key: str, height: int = 420) -> None:
    p = Path(path)
    try:
        html, n = _traj_html(str(p), p.stat().st_mtime, height, _dark())
        st.caption(f"{n} saved frames (animated, subsampled to ≤ 200).")
        st.iframe(html, height=height + 8, alt=f"MD trajectory {p.name}")
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not animate trajectory: {e}")
