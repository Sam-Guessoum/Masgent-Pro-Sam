"""Custom input widgets used by the schema-to-form renderer."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from core.parsers import read_csv

from core import examples
from core.paths import UnsafePathError, safe_filename
from core.structures import looks_like_structure, summary
from ui.state import services

# ------------------------------------------------------------ periodic table
_PT = """H,1,1 He,1,18 Li,2,1 Be,2,2 B,2,13 C,2,14 N,2,15 O,2,16 F,2,17 Ne,2,18 Na,3,1 Mg,3,2 Al,3,13 Si,3,14 P,3,15
S,3,16 Cl,3,17 Ar,3,18 K,4,1 Ca,4,2 Sc,4,3 Ti,4,4 V,4,5 Cr,4,6 Mn,4,7 Fe,4,8 Co,4,9 Ni,4,10 Cu,4,11 Zn,4,12 Ga,4,13
Ge,4,14 As,4,15 Se,4,16 Br,4,17 Kr,4,18 Rb,5,1 Sr,5,2 Y,5,3 Zr,5,4 Nb,5,5 Mo,5,6 Tc,5,7 Ru,5,8 Rh,5,9 Pd,5,10 Ag,5,11
Cd,5,12 In,5,13 Sn,5,14 Sb,5,15 Te,5,16 I,5,17 Xe,5,18 Cs,6,1 Ba,6,2 La,8,3 Ce,8,4 Pr,8,5 Nd,8,6 Pm,8,7 Sm,8,8 Eu,8,9
Gd,8,10 Tb,8,11 Dy,8,12 Ho,8,13 Er,8,14 Tm,8,15 Yb,8,16 Lu,8,17 Hf,6,4 Ta,6,5 W,6,6 Re,6,7 Os,6,8 Ir,6,9 Pt,6,10 Au,6,11
Hg,6,12 Tl,6,13 Pb,6,14 Bi,6,15 Po,6,16 At,6,17 Rn,6,18 Fr,7,1 Ra,7,2 Ac,9,3 Th,9,4 Pa,9,5 U,9,6 Np,9,7 Pu,9,8 Am,9,9
Cm,9,10 Bk,9,11 Cf,9,12 Es,9,13 Fm,9,14 Md,9,15 No,9,16 Lr,9,17 Rf,7,4 Db,7,5 Sg,7,6 Bh,7,7 Hs,7,8 Mt,7,9 Ds,7,10
Rg,7,11 Cn,7,12 Nh,7,13 Fl,7,14 Mc,7,15 Lv,7,16 Ts,7,17 Og,7,18"""
PERIODIC: list[tuple[str, int, int]] = [(e.split(",")[0], int(e.split(",")[1]), int(e.split(",")[2])) for e in _PT.split()]
ELEMENTS: list[str] = [e[0] for e in PERIODIC]


def _set(key: str, value: Any) -> None:
    st.session_state[key] = value


def element_select(label: str, key: str, value: str | None = None, options: list[str] | None = None,
                   help: str | None = None) -> str | None:  # noqa: A002
    """Searchable element selector with a periodic-table popover."""
    opts = options or ELEMENTS
    if key not in st.session_state:
        st.session_state[key] = value if value in opts else (opts[0] if options else None)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        sel = st.selectbox(label, opts, key=key, help=help, placeholder="Choose an element")
        if not options:
            with st.popover("Periodic table", icon=":material/grid_on:"):
                st.caption("Click an element.")
                for row in range(1, 10):
                    cols = st.columns(18, gap=None)
                    for sym, r, c in PERIODIC:
                        if r == row:
                            cols[c - 1].button(sym, key=f"{key}_pt_{sym}", on_click=_set, args=(key, sym), width="stretch",
                                               type="primary" if sym == sel else "secondary")
    return st.session_state.get(key)


# ------------------------------------------------------------------ library
@st.cache_data(ttl=8, show_spinner=False)
def _library_files(workspace: str, kind: str, limit_runs: int = 300) -> list[tuple[str, str]]:
    """(label, path) of files in recent runs (+ uploads) of a given kind."""
    svc = services()
    rows, _ = svc.storage.query_runs(limit=limit_runs, statuses=["succeeded"])
    out: list[tuple[str, str]] = []
    for r in rows:
        files = Path(r["path"]) / "files"
        if not files.exists():
            continue
        for p in sorted(files.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(files).as_posix()
            if _matches(p, kind):
                label = r.get("label") or r.get("tool_title") or r["tool"]
                out.append((f"{label} · {r['created'][5:16].replace('T', ' ')} › {rel}", str(p)))
    for p in svc.storage.list_uploads():
        if _matches(p, kind):
            out.append((f"Upload › {p.name}", str(p)))
    return out


def _matches(p: Path, kind: str) -> bool:
    if kind == "structure":
        return looks_like_structure(p)
    if kind == "csv":
        return p.suffix.lower() == ".csv"
    if kind.startswith("ext:"):
        exts = kind[4:].split(",")
        return p.suffix.lower().lstrip(".") in exts
    return True


def _upload(label: str, key: str, types: list[str] | None) -> str | None:
    up = st.file_uploader(label, type=types or None, key=key, label_visibility="collapsed")
    if up is not None:
        cache_key = f"{key}__saved"
        sig = (up.name, up.size)
        if st.session_state.get(f"{cache_key}_sig") != sig:
            try:
                limit = int(st.session_state.get("upload_limit_mb", 200))
                path = services().storage.save_upload(safe_filename(up.name), up.getvalue(), max_mb=limit)
                st.session_state[cache_key] = str(path)
                st.session_state[f"{cache_key}_sig"] = sig
            except (ValueError, UnsafePathError) as e:
                st.error(str(e))
                return None
        return st.session_state.get(cache_key)
    return None


def file_picker(label: str, key: str, kind: str, value: str | None, examples_map: dict[str, str] | None = None,
                types: list[str] | None = None, help: str | None = None, allow_current: bool = False) -> str | None:  # noqa: A002
    """Pick a file from: examples · upload · previous runs · current structure."""
    sources = []
    if allow_current and st.session_state.get("active_structure"):
        sources.append("Current")
    if examples_map:
        sources.append("Example")
    sources += ["Upload", "From runs"]
    src_key = f"{key}__src"
    if src_key not in st.session_state:
        if value and isinstance(value, str) and value.startswith(("example:", "example_ml:")) and "Example" in sources:
            st.session_state[src_key] = "Example"
        elif value and Path(str(value)).exists():
            st.session_state[src_key] = "From runs"
        else:
            st.session_state[src_key] = sources[0]
    if st.session_state[src_key] not in sources:
        st.session_state[src_key] = sources[0]
    st.markdown(f"**{label}**", help=help)
    src = st.segmented_control(label + " source", sources, key=src_key, label_visibility="collapsed", required=True)
    result: str | None = None
    if src == "Current":
        cur = st.session_state.get("active_structure")
        st.caption(f"Current structure: `{examples.ref_label(cur)}`")
        result = cur
    elif src == "Example":
        names = list((examples_map or {}).keys())
        prefix = "example_ml:" if kind != "structure" else "example:"
        current_name = value.split(":", 1)[1] if isinstance(value, str) and value.startswith(prefix) else None
        ek = f"{key}__ex"
        if ek not in st.session_state or st.session_state[ek] not in names:
            st.session_state[ek] = current_name if current_name in names else names[0]
        name = st.selectbox("Example", names, key=ek, label_visibility="collapsed")
        result = f"{prefix}{name}"
    elif src == "Upload":
        result = _upload(label, f"{key}__up", types)
        if result is None and value and Path(str(value)).exists() and "uploads" in str(value):
            result = value
            st.caption(f"Using `{Path(value).name}`")
    else:
        items = _library_files(str(services().workspace), kind)
        if not items:
            st.caption("No matching files in the library yet: run a tool or upload a file.")
        else:
            labels = [i[0] for i in items]
            paths = [i[1] for i in items]
            lk = f"{key}__lib"
            if lk not in st.session_state or st.session_state[lk] not in labels:
                st.session_state[lk] = labels[paths.index(value)] if value in paths else labels[0]
            chosen = st.selectbox("File from library", labels, key=lk, label_visibility="collapsed")
            result = paths[labels.index(chosen)]
    return result


def structure_picker(label: str, key: str, value: str | None, help: str | None = None, preview: bool = True) -> str | None:  # noqa: A002
    path = file_picker(label, key, "structure", value, examples_map=examples.STRUCTURES, types=None, help=help, allow_current=True)
    real = examples.resolve_ref(path) if path else None
    if real and Path(real).exists():
        try:
            s = summary(real)
            st.caption(f"{s['full_formula']} · {s['natoms']} atoms · {s.get('spacegroup', '')} · V = {s['volume']:.2f} Å³")
        except Exception as e:  # noqa: BLE001
            st.warning(f"This file could not be read as a structure: {e}")
        if preview:
            with st.expander("3D preview", icon=":material/view_in_ar:"):
                from ui.viewer3d import show_structure

                show_structure(real, key=f"{key}__3d", height=300)
    return path


def csv_picker(label: str, key: str, value: str | None, help: str | None = None) -> str | None:  # noqa: A002
    ex = {k: v for k, v in examples.ML_FILES.items() if v.endswith(".csv")}
    path = file_picker(label, key, "csv", value, examples_map=ex, types=["csv"], help=help)
    real = examples.resolve_ref(path) if path else None
    if real and Path(real).exists():
        try:
            df = read_csv(real, nrows=200)
            st.caption(f"{len(read_csv(real))} rows × {df.shape[1]} columns: {', '.join(map(str, df.columns[:8]))}"
                       + (" …" if df.shape[1] > 8 else ""))
        except Exception as e:  # noqa: BLE001
            st.warning(f"Could not read CSV: {e}")
    return path


def generic_file_picker(label: str, key: str, value: str | None, extensions: list[str] | None, help: str | None = None) -> str | None:  # noqa: A002
    ex = {k: v for k, v in examples.ML_FILES.items() if not extensions or Path(v).suffix.lstrip(".") in extensions}
    kind = f"ext:{','.join(extensions)}" if extensions else "any"
    return file_picker(label, key, kind, value, examples_map=ex or None, types=extensions, help=help)


def folder_picker(label: str, key: str, value: str | None, folder_kind: str | None, help: str | None = None) -> str | None:  # noqa: A002
    """Results folder: Masgent example · ZIP upload (zip-slip-safe) · a run's output folder."""
    sources = []
    if folder_kind and examples.analysis_example(folder_kind):
        sources.append("Masgent example")
    sources += ["Upload ZIP", "From runs"]
    src_key = f"{key}__src"
    if src_key not in st.session_state or st.session_state[src_key] not in sources:
        st.session_state[src_key] = "Masgent example" if (isinstance(value, str) and value.startswith("example_folder:")) else sources[0]
    st.markdown(f"**{label}**", help=help)
    src = st.segmented_control(label + " source", sources, key=src_key, label_visibility="collapsed", required=True)
    if src == "Masgent example":
        st.caption(f"Real VASP outputs shipped in the repository's `examples/` folder ({folder_kind}). Only the needed files are copied.")
        return f"example_folder:{folder_kind}"
    if src == "Upload ZIP":
        up = st.file_uploader("ZIP of the results folder", type=["zip"], key=f"{key}__zip", label_visibility="collapsed")
        if up is not None:
            sig = (up.name, up.size)
            if st.session_state.get(f"{key}__zipsig") != sig:
                import hashlib

                data = up.getvalue()
                digest = hashlib.sha256(data).hexdigest()[:16]
                dest = services().storage.root / "uploads" / f"zip_{digest}"
                try:
                    if not dest.exists():
                        services().storage.safe_extract_zip(data, dest, max_total_mb=int(st.session_state.get("upload_limit_mb", 200)) * 5)
                    st.session_state[f"{key}__zipdir"] = str(_unwrap_single_dir(dest))
                    st.session_state[f"{key}__zipsig"] = sig
                except (ValueError, UnsafePathError, Exception) as e:  # noqa: BLE001
                    st.error(f"Rejected archive: {e}")
                    return None
            return st.session_state.get(f"{key}__zipdir")
        return value if value and not str(value).startswith("example_folder:") and Path(str(value)).is_dir() else None
    # From runs: any directory inside a run's files that matches the expected layout
    dirs = _run_dirs(str(services().workspace), folder_kind or "")
    if not dirs:
        st.caption("No matching folders found in previous runs.")
        return None
    labels = [d[0] for d in dirs]
    chosen = st.selectbox("Folder", labels, key=f"{key}__lib", label_visibility="collapsed")
    return dirs[labels.index(chosen)][1]


_FOLDER_NAMES = {"convergence": "convergence_tests", "eos": "eos_calculations", "elastic": "elastic_constants",
                 "aimd": "aimd_simulations", "neb": "neb_calculations"}


@st.cache_data(ttl=8, show_spinner=False)
def _run_dirs(workspace: str, folder_kind: str) -> list[tuple[str, str]]:
    name = _FOLDER_NAMES.get(folder_kind, "")
    rows, _ = services().storage.query_runs(limit=300)
    out = []
    for r in rows:
        files = Path(r["path"]) / "files"
        for d in files.rglob(name) if name else []:
            if d.is_dir():
                out.append((f"{r.get('label') or r.get('tool_title')} · {r['created'][5:16].replace('T', ' ')} › {d.relative_to(files).as_posix()}", str(d)))
    up = services().storage.root / "uploads"
    for d in up.glob("zip_*"):
        out.append((f"Uploaded ZIP › {_unwrap_single_dir(d).name}", str(_unwrap_single_dir(d))))
    return out


def _unwrap_single_dir(d: Path) -> Path:
    """If a ZIP contained a single top-level folder, use it."""
    kids = [k for k in d.iterdir() if not k.name.startswith(("__MACOSX", "."))]
    return kids[0] if len(kids) == 1 and kids[0].is_dir() else d


# ----------------------------------------------------------- small editors
def parse_number_list(text: str, as_int: bool) -> tuple[list | None, str | None]:
    parts = [p for p in re.split(r"[,\s;]+", text.strip()) if p]
    if not parts:
        return None, "Enter at least one value."
    try:
        vals = [int(p) if as_int else float(p) for p in parts]
    except ValueError:
        return None, f"Could not read numbers from “{text}”. Use commas, e.g. {'300, 400, 500' if as_int else '0.96, 1.0, 1.04'}."
    return vals, None


def number_list(label: str, key: str, value: list | None, as_int: bool, help: str | None = None) -> list | None:  # noqa: A002
    default = ", ".join(str(v) for v in (value or []))
    if key not in st.session_state:
        st.session_state[key] = default
    text = st.text_input(label, key=key, help=(help or "") + " Comma-separated.")
    vals, err = parse_number_list(text, as_int)
    if err:
        st.caption(f":red[{err}]")
    return vals


def miller_input(label: str, key: str, value: list[int] | None, help: str | None = None) -> list[int]:  # noqa: A002
    v = list(value or [1, 0, 0])
    st.markdown(f"**{label}**", help=help)
    with st.container(horizontal=True):
        out = [st.number_input(n, value=int(v[i]), step=1, min_value=-20, max_value=20, key=f"{key}_{n}")
               for i, n in enumerate(("h", "k", "l"))]
    if out == [0, 0, 0]:
        st.caption(":red[(0 0 0) is not a plane.]")
    return [int(x) for x in out]


_MATRIX_PRESETS = {"1×1×1": "1 0 0; 0 1 0; 0 0 1", "2×2×1": "2 0 0; 0 2 0; 0 0 1", "2×2×2": "2 0 0; 0 2 0; 0 0 2",
                   "3×3×3": "3 0 0; 0 3 0; 0 0 3", "√2×√2 (rot. 45°)": "1 -1 0; 1 1 0; 0 0 1"}


def matrix3_input(label: str, key: str, value: str | None, help: str | None = None) -> str:  # noqa: A002
    v = value or "2 0 0; 0 2 0; 0 0 2"
    try:
        rows = [[int(x) for x in r.split()] for r in v.split(";")]
        assert len(rows) == 3 and all(len(r) == 3 for r in rows)
    except Exception:
        rows = [[2, 0, 0], [0, 2, 0], [0, 0, 2]]
    st.markdown(f"**{label}**", help=help or "Integer 3×3 matrix; rows give the new lattice vectors in units of the old ones.")
    pk = f"{key}__preset"

    def _apply() -> None:
        p = st.session_state.get(pk)
        if p:
            st.session_state[f"{key}__df_v"] = st.session_state.get(f"{key}__df_v", 0) + 1
            st.session_state[f"{key}__value"] = _MATRIX_PRESETS[p]

    st.pills("Quick", list(_MATRIX_PRESETS), key=pk, on_change=_apply, label_visibility="collapsed")
    if f"{key}__value" in st.session_state:
        try:
            rows = [[int(x) for x in r.split()] for r in st.session_state[f"{key}__value"].split(";")]
        except Exception:
            pass
    df = pd.DataFrame(rows, columns=["a", "b", "c"], index=["a′", "b′", "c′"])
    edited = st.data_editor(df, key=f"{key}__df_{st.session_state.get(f'{key}__df_v', 0)}", width="stretch",
                            column_config={c: st.column_config.NumberColumn(c, step=1, format="%d") for c in df.columns})
    try:
        vals = edited.astype(int).values.tolist()
        import numpy as np

        det = abs(round(float(np.linalg.det(np.array(vals, float)))))
        if det == 0:
            st.caption(":red[Matrix is singular (determinant 0).]")
        else:
            st.caption(f"Supercell = {det}× the original cell.")
        return "; ".join(" ".join(str(x) for x in r) for r in vals)
    except Exception:
        return v


def defect_amount_input(label: str, key: str, value: Any, help: str | None = None) -> float | int | None:  # noqa: A002
    mode_key = f"{key}__mode"
    if mode_key not in st.session_state:
        st.session_state[mode_key] = "Fraction" if isinstance(value, float) else "Count"
    st.markdown(f"**{label}**", help=help or "A count (≥ 1 atoms) or a fraction 0–1 of that element's atoms.")
    with st.container(horizontal=True, vertical_alignment="bottom"):
        mode = st.segmented_control("Mode", ["Count", "Fraction"], key=mode_key, label_visibility="collapsed", required=True)
        if mode == "Count":
            v = st.number_input("Atoms", min_value=1, max_value=100000, step=1,
                                value=int(value) if isinstance(value, int) and value >= 1 else 1, key=f"{key}__n")
            return int(v)
        v = st.number_input("Fraction", min_value=0.001, max_value=0.999, step=0.01, format="%.3f",
                            value=float(value) if isinstance(value, float) and 0 < value < 1 else 0.1, key=f"{key}__f")
        return float(v)


def sqs_input(label: str, key: str, value: dict | None, structure_path: str | None, help: str | None = None) -> dict:  # noqa: A002
    """Per-site concentration editor: {site element: {element: fraction}}."""
    st.markdown(f"**{label}**", help=help or "For each site you want to randomise, list the elements and fractions (must sum to 1).")
    real = examples.resolve_ref(structure_path) if structure_path else None
    sites = []
    if real and Path(real).exists():
        try:
            sites = summary(real)["elements"]
        except Exception:
            sites = []
    if not sites:
        st.caption("Choose a parent structure first.")
        return value or {}
    value = value or {}
    out: dict[str, dict[str, float]] = {}
    for site in sites:
        cur = value.get(site)
        on = st.toggle(f"Randomise **{site}** sites", value=bool(cur), key=f"{key}__on_{site}")
        if not on:
            continue
        rows = [{"element": k, "fraction": v} for k, v in (cur or {site: 0.5, ("Cu" if site != "Cu" else "Ni"): 0.5}).items()]
        df = st.data_editor(pd.DataFrame(rows), key=f"{key}__df_{site}", num_rows="dynamic", width="stretch",
                            column_config={"element": st.column_config.SelectboxColumn("Element", options=ELEMENTS, required=True),
                                           "fraction": st.column_config.NumberColumn("Fraction", min_value=0.0, max_value=1.0, step=0.05, format="%.4f")})
        conf = {str(r["element"]): float(r["fraction"]) for _, r in df.dropna().iterrows() if r["element"]}
        total = sum(conf.values())
        if abs(total - 1) > 1e-6:
            st.caption(f":red[Fractions on {site} sites sum to {total:.4f}; they must sum to 1.]")
        out[site] = conf
    if not out:
        st.caption(":red[Switch on at least one site to randomise.]")
    return out
