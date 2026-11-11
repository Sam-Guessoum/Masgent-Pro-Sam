"""Tool-specific result viewers (interactive Plotly plots, tables, 3D views)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from core.parsers import read_csv

from core import parsers
from core.storage import RunRecord, is_licensed
from core.structures import looks_like_structure

TEXT_SUFFIXES = {".txt", ".log", ".sh", ".spec", ".csv", ".json", ".vasp", ".xyz", ".cif", ""}
LANG = {".sh": "bash", ".json": "json", ".py": "python"}


def _first(files: Path, pattern: str) -> Path | None:
    hits = sorted(files.rglob(pattern))
    return hits[0] if hits else None


def _fig(fig, key: str, alt: str) -> None:  # noqa: ANN001
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10), height=380)
    st.plotly_chart(fig, key=key, alt=alt, config={"displaylogo": False})


def outputs_dir(rec: RunRecord) -> Path:
    return rec.files_dir


# ---------------------------------------------------------------- elastic
def elastic_view(path: Path, key: str) -> None:
    import plotly.express as px

    d = parsers.parse_elastic_txt(path)
    m = d.get("moduli", {})
    cols = st.columns(4)
    for c, (k, lab) in zip(cols, [("K_H", "Bulk modulus K (Hill)"), ("G_H", "Shear modulus G (Hill)"),
                                  ("E_H", "Young's modulus E"), ("nu_H", "Poisson's ratio ν")]):
        v = m.get(k)
        c.metric(lab, "–" if v is None else (f"{v:.3f}" if k == "nu_H" else f"{v:.1f} GPa"), border=True)
    if d.get("mechanically_stable") is not None:
        st.caption(("Mechanically stable (all eigenvalues of C > 0)." if d["mechanically_stable"]
                    else ":red[Not mechanically stable: C has a non-positive eigenvalue.]")
                   + (f" Pugh ratio K/G = {m['pugh_K_over_G']:.2f} ({'ductile' if m['pugh_K_over_G'] > 1.75 else 'brittle'} by Pugh's criterion)."
                      if m.get("pugh_K_over_G") else ""))
    if d.get("C"):
        lab = ["1 (xx)", "2 (yy)", "3 (zz)", "4 (yz)", "5 (xz)", "6 (xy)"]
        fig = px.imshow(np.array(d["C"]), x=lab, y=lab, text_auto=".1f", color_continuous_scale="RdBu_r",
                        color_continuous_midpoint=0, labels=dict(color="GPa"), title="Elastic tensor Cᵢⱼ (GPa, Voigt notation)")
        _fig(fig, f"{key}_cij", "Elastic constant heatmap")
        with st.expander("Voigt / Reuss / Hill bounds"):
            st.dataframe(pd.DataFrame({"Voigt": [m.get("K_V"), m.get("G_V")], "Reuss": [m.get("K_R"), m.get("G_R")],
                                       "Hill": [m.get("K_H"), m.get("G_H")]}, index=["Bulk K (GPa)", "Shear G (GPa)"]))


# -------------------------------------------------------------------- EOS
def eos_view(csv: Path, key: str, natoms: int | None = None) -> None:
    import plotly.graph_objects as go

    d = parsers.parse_eos_csv(csv, natoms=natoms)
    v, e = np.array(d["volume_per_atom"]), np.array(d["energy_per_atom"])
    fig = go.Figure()
    fig.add_scatter(x=v, y=e, mode="markers", name="Calculated", marker=dict(size=11))
    fit = d.get("fit")
    if fit:
        vv = np.linspace(v.min() * 0.99, v.max() * 1.01, 200)
        fig.add_scatter(x=vv, y=parsers.birch_murnaghan(vv, fit["E0_eV"], fit["V0_A3"], fit["B0_GPa"] / parsers.EV_A3_TO_GPA,
                                                        fit["B0_prime"]), mode="lines", name="Birch–Murnaghan fit")
        fig.add_scatter(x=[fit["V0_A3"]], y=[fit["E0_eV"]], mode="markers", name="Equilibrium", marker=dict(symbol="star", size=16))
        c = st.columns(3)
        c[0].metric("Equilibrium volume V₀", f"{fit['V0_A3']:.4f} Å³/atom", border=True)
        c[1].metric("Bulk modulus B₀", f"{fit['B0_GPa']:.1f} GPa", border=True)
        c[2].metric("Pressure derivative B₀′", f"{fit['B0_prime']:.2f}", border=True)
        st.caption(f"Fit residual RMS {fit['rms_meV']:.3f} meV/atom · E₀ = {fit['E0_eV']:.5f} eV/atom")
    fig.update_layout(title="Energy–volume curve", xaxis_title=d["volume_label"], yaxis_title="Energy (eV/atom)")
    _fig(fig, f"{key}_eos", "Equation of state plot")


# ------------------------------------------------------------------- MD
def md_view(log: Path, key: str) -> None:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    rows = parsers.parse_md_log(log)
    if not rows:
        st.caption("No MD data yet.")
        return
    df = pd.DataFrame(rows)
    c = st.columns(3)
    c[0].metric("Simulated time", f"{df.time_ps.iloc[-1]:.3f} ps", border=True)
    c[1].metric("Mean T", f"{df['T'].mean():.0f} K", f"σ {df['T'].std():.0f} K" if len(df) > 1 else None, delta_color="off", border=True)
    c[2].metric("Energy drift (Etot)", f"{(df.etot.iloc[-1] - df.etot.iloc[0]) * 1000:.2f} meV/atom", border=True)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, subplot_titles=("Temperature (K)", "Energy per atom (eV)", "Kinetic energy per atom (eV)"))
    fig.add_scatter(x=df.time_ps, y=df["T"], name="T", row=1, col=1)
    fig.add_scatter(x=df.time_ps, y=df.etot, name="E total", row=2, col=1)
    fig.add_scatter(x=df.time_ps, y=df.epot, name="E potential", row=2, col=1)
    fig.add_scatter(x=df.time_ps, y=df.ekin, name="E kinetic", row=3, col=1)
    fig.update_xaxes(title_text="Time (ps)", row=3, col=1)
    fig.update_layout(height=560, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, key=f"{key}_md", alt="MD temperature and energy traces", config={"displaylogo": False})


def opt_view(logs: dict[str, list[dict]], key: str) -> None:
    import plotly.graph_objects as go

    fig = go.Figure()
    for name, rows in logs.items():
        if rows:
            fig.add_scatter(x=[r["step"] for r in rows], y=[r["fmax"] for r in rows], mode="lines+markers", name=name)
    if fig.data:
        fig.update_layout(title="Optimiser convergence (max force)", xaxis_title="Step", yaxis_title="fmax (eV/Å)", yaxis_type="log")
        _fig(fig, f"{key}_opt", "Optimiser convergence")


# --------------------------------------------------------------- analysis
def convergence_view(root: Path, key: str) -> None:
    import plotly.graph_objects as go

    series = parsers.convergence_series(root)
    if not series:
        return
    for kind, pts in series.items():
        x = [p[0] for p in pts]
        e = [p[1] for p in pts]
        diff = [abs(e[i] - e[i - 1]) for i in range(1, len(e))]
        fig = go.Figure()
        fig.add_scatter(x=x[1:], y=diff, mode="lines+markers", name="|ΔE| between steps")
        fig.add_hline(y=1.0, line_dash="dash", annotation_text="1 meV/atom")
        conv = next((x[i + 1] for i, d in enumerate(diff) if d < 1.0), None)
        fig.update_layout(title=f"{'ENCUT (eV)' if kind == 'encut' else 'k-points per reciprocal atom'} convergence",
                          xaxis_title="ENCUT (eV)" if kind == "encut" else "k-point density", yaxis_title="|ΔE| (meV/atom)", yaxis_type="log")
        _fig(fig, f"{key}_{kind}", f"{kind} convergence")
        st.caption(f"First setting converged below 1 meV/atom: **{conv}**" if conv else "Not converged below 1 meV/atom in the tested range.")


def aimd_view(root: Path, key: str) -> None:
    import plotly.express as px
    import plotly.graph_objects as go

    dcsv = _first(root, "aimd_diffusion_coefficients.csv")
    ea = _first(root, "aimd_activation_energy.txt")
    if ea:
        v = parsers.parse_keyvals(ea).get("Activation Energy")
        if v is not None:
            st.metric("Activation energy (Arrhenius fit)", f"{v:.1f} meV", border=True)
    if dcsv:
        df = read_csv(dcsv)
        st.dataframe(df, hide_index=True)
        tcol, dcol = df.columns[0], df.columns[1]
        d = df[df[dcol] > 0]
        if len(d) >= 2:
            fig = go.Figure()
            fig.add_scatter(x=1000 / d[tcol], y=np.log10(d[dcol]), mode="markers+lines", name="log10 D")
            fig.update_layout(title="Arrhenius plot", xaxis_title="1000 / T (1/K)", yaxis_title="log₁₀ D (cm²/s)")
            _fig(fig, f"{key}_arr", "Arrhenius plot")
    msd = sorted(root.rglob("aimd_msd.csv"))
    if msd:
        frames = []
        for p in msd:
            df = read_csv(p)
            df["T"] = p.parent.name
            frames.append(df)
        all_ = pd.concat(frames)
        fig = px.line(all_, x="Time (ps)", y="MSD_total (Å²)", color="T", title="Mean squared displacement")
        _fig(fig, f"{key}_msd", "MSD vs time")
    te = sorted(root.rglob("aimd_temperature_energy.csv"))
    if te:
        pick = st.selectbox("Temperature run", [p.parent.name for p in te], key=f"{key}_tsel")
        df = read_csv(next(p for p in te if p.parent.name == pick))
        df["step"] = range(len(df))
        fig = px.line(df, x="step", y=df.columns[:2].tolist(), facet_row="variable", title=f"Temperature and energy ({pick})")
        fig.update_yaxes(matches=None)
        _fig(fig, f"{key}_te", "AIMD temperature and energy")


def neb_view(root: Path, key: str) -> None:
    import plotly.graph_objects as go

    pts, spl, bar = _first(root, "neb_data_points.csv"), _first(root, "neb_data_spline.csv"), _first(root, "energy_barrier.txt")
    if bar:
        v = parsers.parse_keyvals(bar).get("Energy Barrier")
        if v is not None:
            st.metric("Energy barrier", f"{v:.1f} meV ({v / 1000:.3f} eV)", border=True)
    fig = go.Figure()
    if spl:
        s = read_csv(spl)
        fig.add_scatter(x=s.iloc[:, 0], y=s.iloc[:, 1], mode="lines", name="Spline")
    if pts:
        p = read_csv(pts)
        fig.add_scatter(x=p.iloc[:, 0], y=p.iloc[:, 1], mode="markers", name="Images", marker=dict(size=11))
    if fig.data:
        fig.update_layout(title="Minimum-energy path", xaxis_title="Normalised reaction coordinate", yaxis_title="Energy (meV)")
        _fig(fig, f"{key}_neb", "NEB energy profile")


# -------------------------------------------------------------------- ML
def correlation_view(csv: Path, key: str) -> None:
    import plotly.express as px

    df = read_csv(csv, index_col=0)
    fig = px.imshow(df, text_auto=".2f", color_continuous_scale="RdBu_r", zmin=-1, zmax=1, title="Pearson correlation")
    fig.update_layout(height=max(380, 28 * len(df)))
    st.plotly_chart(fig, key=f"{key}_corr", alt="Correlation heatmap", config={"displaylogo": False})


def distributions_view(csvs: list[Path], key: str) -> None:
    import plotly.express as px

    frames = [read_csv(p) for p in csvs if p.exists()]
    if not frames:
        return
    df = pd.concat(frames, axis=1)
    col = st.selectbox("Distribution of", list(df.columns), key=f"{key}_dist")
    _fig(px.histogram(df, x=col, nbins=30, title=f"Distribution of {col}"), f"{key}_hist", f"Histogram of {col}")


def pca_view(csv: Path, key: str) -> None:
    import plotly.express as px

    df = read_csv(csv)
    if df.shape[1] >= 2:
        _fig(px.scatter(df, x=df.columns[0], y=df.columns[1], title="Samples in principal-component space"), f"{key}_pca", "PCA scatter")
    st.dataframe(df.head(200), hide_index=True)


def augmentation_view(files: Path, key: str) -> None:
    import plotly.express as px

    xin = _first(files, "ml_input_data_augmented.csv")
    if not xin:
        return
    df = read_csv(xin)
    df["source"] = "augmented"
    src_csv = _first(files / "inputs", "*.csv")
    if src_csv:
        n0 = len(read_csv(src_csv))
        df.loc[: n0 - 1, "source"] = "original"
    if df.shape[1] >= 3:
        c = list(df.columns[:-1])
        cx = st.selectbox("x", c, index=0, key=f"{key}_ax")
        cy = st.selectbox("y", c, index=min(1, len(c) - 1), key=f"{key}_ay")
        _fig(px.scatter(df, x=cx, y=cy, color="source", opacity=0.7, title="Original vs synthetic samples"), f"{key}_aug", "Augmentation scatter")


def optuna_view(trials: list[dict] | None, csv: Path | None, key: str) -> None:
    import plotly.graph_objects as go

    if csv and csv.exists():
        df = read_csv(csv)
        df = df.rename(columns={"value": "value"})
        rows = [{"number": r["number"], "state": r["state"], "value": r.get("value")} for _, r in df.iterrows()]
    else:
        rows = trials or []
    if not rows:
        st.caption("No finished trials yet.")
        return
    df = pd.DataFrame(rows)
    comp = df[df["state"] == "COMPLETE"].dropna(subset=["value"])
    fig = go.Figure()
    if len(comp):
        fig.add_scatter(x=comp["number"], y=comp["value"], mode="markers", name="Completed")
        fig.add_scatter(x=comp["number"], y=comp["value"].cummin(), mode="lines", name="Best so far")
    pr = df[df["state"] == "PRUNED"]
    if len(pr):
        fig.add_scatter(x=pr["number"], y=pr["value"], mode="markers", name="Pruned", marker=dict(symbol="x", opacity=0.5))
    fig.update_layout(title="Optimisation history", xaxis_title="Trial", yaxis_title="Mean 5-fold validation loss")
    _fig(fig, f"{key}_opt", "Optuna optimisation history")
    if trials:
        flat = [{**{"trial": t["number"], "state": t["state"], "value": t["value"], "best": t.get("best")},
                 **{f"p:{k}": v for k, v in (t.get("params") or {}).items()}} for t in trials]
        st.dataframe(pd.DataFrame(flat), hide_index=True)


def loss_view(rows: list[dict], key: str) -> None:
    import plotly.graph_objects as go

    if not rows:
        return
    df = pd.DataFrame(rows)
    fig = go.Figure()
    fig.add_scatter(x=df["epoch"], y=df["train"], name="Training")
    fig.add_scatter(x=df["epoch"], y=df["valid"], name="Validation")
    best = int(df["valid"].idxmin())
    fig.add_vline(x=df["epoch"][best], line_dash="dot", annotation_text=f"best epoch {df['epoch'][best]}")
    fig.update_layout(title="Loss curves (standardised MSE)", xaxis_title="Epoch", yaxis_title="Loss", yaxis_type="log")
    _fig(fig, f"{key}_loss", "Training loss curves")


def performance_view(path: Path) -> None:
    d = parsers.parse_performance_log(path)
    if d:
        c = st.columns(4)
        c[0].metric("Train RMSE", f"{d.get('training_rmse', float('nan')):.4g}", border=True)
        c[1].metric("Train R²", f"{d.get('training_r2', float('nan')):.4f}", border=True)
        c[2].metric("Validation RMSE", f"{d.get('validation_rmse', float('nan')):.4g}", border=True)
        c[3].metric("Validation R²", f"{d.get('validation_r2', float('nan')):.4f}", border=True)


def almgsisc_view(path: Path) -> None:
    d = parsers.parse_prediction_almgsisc(path)
    c = st.columns(3)
    c[0].metric("Ultimate tensile strength", f"{d['UTS_MPa']:.1f} MPa" if d["UTS_MPa"] is not None else "–", border=True)
    c[1].metric("Yield strength", f"{d['YS_MPa']:.1f} MPa" if d["YS_MPa"] is not None else "–", border=True)
    c[2].metric("Elongation", f"{d['EL_%']:.2f} %" if d["EL_%"] is not None else "–", border=True)
    comp = d["composition_wt%"]
    st.caption("Composition (wt.%): " + ", ".join(f"{k} {v:.2f}" for k, v in comp.items() if v is not None)
               + " · CALPHAD phases (%): " + ", ".join(f"{k.replace('PH_', '')} {v:.2f}" for k, v in d["phase_fractions_%"].items() if v is not None))


def hea_view(path: Path, key: str) -> None:
    import plotly.express as px

    d = parsers.parse_prediction_hea(path)
    st.caption("Composition (at.%): " + ", ".join(f"{k} {v:.1f}" for k, v in d["composition_at%"].items()))
    cols = st.columns(2)
    for col, ph in zip(cols, ("FCC", "BCC")):
        p = d[ph]
        with col.container(border=True):
            st.markdown(f"**{ph} phase**")
            st.metric("Formation energy", f"{p['formation_energy_kJ_mol']:.2f} kJ/mol" if p["formation_energy_kJ_mol"] is not None else "–")
            st.metric("Bulk modulus K (Hill)", f"{p['K_H']:.1f} GPa" if p["K_H"] is not None else "–")
            st.metric("Shear modulus G (Hill)", f"{p['G_H']:.1f} GPa" if p["G_H"] is not None else "–")
            if p.get("C"):
                c = np.array(p["C"])
                st.caption(f"C11 = {c[0, 0]:.1f}, C12 = {c[0, 1]:.1f}, C44 = {c[3, 3]:.1f} GPa")
    fe = d["FCC"]["formation_energy_kJ_mol"], d["BCC"]["formation_energy_kJ_mol"]
    if None not in fe:
        st.info(f"Lower formation energy → more stable phase: **{'FCC' if fe[0] < fe[1] else 'BCC'}** "
                f"(ΔE = {abs(fe[0] - fe[1]):.2f} kJ/mol).", icon=":material/insights:")
    _ = px


def interface_table(csv: Path, key: str) -> None:
    df = read_csv(csv)
    keep = [c for c in df.columns if not c.startswith("T_")]
    st.dataframe(df[keep].sort_values("Area misfit (%)") if "Area misfit (%)" in df else df[keep], hide_index=True,
                 column_config={c: st.column_config.NumberColumn(format="%.3f") for c in keep if "misfit" in c or "area" in c.lower()})


# --------------------------------------------------------------- dispatch
def tool_specific(rec: RunRecord, key: str, live: Any = None) -> None:
    """Render the rich, tool-specific part of a run's outputs."""
    func = rec.meta.get("func", "")
    f = rec.files_dir
    params = rec.inputs().get("params", {})
    res = rec.result() or {}
    if func == "run_simulation_using_mlps":
        task = params.get("task_type", "single")
        if task == "single" and "total_energy (eV)" in res:
            c = st.columns(3)
            c[0].metric("Total energy", f"{res['total_energy (eV)']:.5f} eV", border=True)
            c[1].metric("Energy per atom", f"{res['energy_per_atom (eV/atom)']:.5f} eV/atom", border=True)
            c[2].metric("Potential", params.get("mlps_type", ""), border=True)
        opt = {p.stem.replace("masgent_mlps_", ""): parsers.parse_opt_log(p) for p in sorted(f.rglob("masgent_mlps_*.log")) if "md" not in p.stem}
        if task == "eos" and (p := _first(f, "eos_cal.csv")):
            eos_view(p, key, natoms=parsers._natoms(f))
        if task == "elastic" and (p := _first(f, "elastic_constants.txt")):
            elastic_view(p, key)
        if task == "md" and (p := _first(f, "masgent_mlps_md.log")):
            md_view(p, key)
            if (t := _first(f, "masgent_mlps_md.traj")):
                with st.expander("Animate trajectory", icon=":material/animation:"):
                    from ui.viewer3d import show_trajectory

                    show_trajectory(t, key=f"{key}_traj")
        if opt and task in ("single", "eos"):
            opt_view(opt, key)
    elif func == "analyze_vasp_workflow_of_elastic_constants" and (p := _first(f, "elastic_constants.txt")):
        elastic_view(p, key)
    elif func == "analyze_vasp_workflow_of_eos" and (p := _first(f, "eos_cal.csv")):
        eos_view(p, key)
    elif func == "analyze_vasp_workflow_of_convergence_tests":
        roots = [d for d in [f, *f.rglob("*")] if d.is_dir() and ((d / "encut_tests").exists() or (d / "kpoint_tests").exists())]
        if roots:
            convergence_view(roots[0], key)
    elif func == "analyze_vasp_workflow_of_aimd":
        aimd_view(f, key)
    elif func == "analyze_vasp_workflow_of_neb":
        neb_view(f, key)
    elif func == "analyze_features_for_machine_learning":
        if (p := _first(f, "correlation_matrix.csv")):
            correlation_view(p, key)
        distributions_view([f / "machine_learning" / "ml_input_data.csv", f / "machine_learning" / "ml_output_data.csv"], key)
    elif func == "reduce_dimensions_for_machine_learning" and (p := _first(f, "ml_input_data_reduced.csv")):
        pca_view(p, key)
    elif func == "augment_data_for_machine_learning":
        augmentation_view(f, key)
    elif func == "design_model_for_machine_learning":
        from core.progress import _jsonl

        optuna_view(_jsonl(rec.path / "progress" / "optuna_trials.jsonl"), _first(f, "study.csv"), key)
        if (p := _first(f, "best_model_params.log")):
            d = parsers.parse_best_params_log(p)
            st.markdown(f"**Best trial #{d.get('best_trial')}** · CV loss {d.get('best_value'):.4g}" if d.get("best_value") is not None else "")
            st.json(d.get("params", {}), expanded=False)
    elif func in ("train_model_for_machine_learning", "retrain_model_for_machine_learning"):
        if (p := _first(f, "performance.log")):
            performance_view(p)
        from core.progress import _jsonl

        loss_view(_jsonl(rec.path / "progress" / "loss.jsonl"), key)
    elif func == "model_prediction_for_AlMgSiSc" and (p := _first(f, "AlMgSiSc_prediction.txt")):
        almgsisc_view(p)
    elif func == "model_prediction_for_AlCoCrFeNi" and (p := _first(f, "AlCoCrFeNi_prediction.txt")):
        hea_view(p, key)
    elif func == "generate_interface_from_poscars" and (p := _first(f, "interface_maker.csv")):
        st.markdown("**Matched interfaces** (sorted by area misfit)")
        interface_table(p, key)
    elif func == "read_file" and res.get("content") is not None:
        st.code(res["content"], language="text")
    elif func == "list_files" and res.get("files"):
        st.dataframe(pd.DataFrame({"file": [str(x).replace("{RUN}/files/", "") for x in res["files"]]}), hide_index=True)


def is_text(p: Path) -> bool:
    return (p.suffix.lower() in TEXT_SUFFIXES or p.name.isupper() or p.name.startswith(("POSCAR", "CONTCAR", "INCAR", "KPOINTS"))) \
        and p.stat().st_size < 2_000_000


def file_browser(rec: RunRecord, key: str, use_in: Any = None) -> None:
    """Browse every output: structures (3D), images, tables and text, with downloads."""
    files = [p for p in rec.output_files(include_inputs=False)]
    if not files:
        st.caption("This run produced no files.")
        return
    rels = [p.relative_to(rec.files_dir).as_posix() for p in files]
    structures = [r for r, p in zip(rels, files) if looks_like_structure(p)]
    images = [r for r, p in zip(rels, files) if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".svg")]
    tables = [r for r, p in zip(rels, files) if p.suffix.lower() == ".csv"]
    texts = [r for r, p in zip(rels, files) if r not in structures + images + tables and not is_licensed(p) and is_text(p)]
    other = [r for r in rels if r not in structures + images + tables + texts]

    groups = [("Structures", structures), ("Figures", images), ("Tables", tables), ("Text files", texts), ("Other", other)]
    groups = [(g, items) for g, items in groups if items]
    labels = [f"{g} ({len(items)})" for g, items in groups]
    choice = st.segmented_control("Show", labels, default=labels[0], key=f"{key}_grp", label_visibility="collapsed", required=True)
    g, items = groups[labels.index(choice)]
    if g == "Figures":
        for r in items[:12]:
            st.image(str(rec.files_dir / r), caption=r, alt=r, width="stretch")
            _download(rec.files_dir / r, f"{key}_dl_{r}")
        return
    sel = st.selectbox("File", items, key=f"{key}_{g}", label_visibility="collapsed")
    p = rec.files_dir / sel
    if g == "Structures":
        from ui.viewer3d import show_structure

        try:
            from core.structures import summary

            s = summary(p)
            st.caption(f"{s['full_formula']} · {s['natoms']} atoms · {s.get('spacegroup')} · a={s['a']:.3f} b={s['b']:.3f} c={s['c']:.3f} Å · "
                       f"α={s['alpha']:.1f}° β={s['beta']:.1f}° γ={s['gamma']:.1f}° · V={s['volume']:.2f} Å³")
        except Exception:
            pass
        show_structure(p, key=f"{key}_3d_{sel}")
        with st.expander("File contents"):
            st.code(p.read_text(encoding="utf-8", errors="replace")[:20000], language="text")
        with st.container(horizontal=True):
            _download(p, f"{key}_dl_{sel}")
            if use_in:
                use_in(p, f"{key}_use_{sel}")
    elif g == "Tables":
        df = read_csv(p)
        st.dataframe(df, hide_index=True)
        _download(p, f"{key}_dl_{sel}")
    elif g == "Text files":
        st.code(p.read_text(encoding="utf-8", errors="replace")[:100_000], language=LANG.get(p.suffix, "text"), line_numbers=True)
        _download(p, f"{key}_dl_{sel}")
    else:
        if is_licensed(p):
            st.warning("POTCAR files are licensed by the VASP group: they are not displayed or exported.", icon=":material/lock:")
        else:
            st.caption(f"{sel} · {p.stat().st_size / 1024:.1f} KB")
            _download(p, f"{key}_dl_{sel}")


def _download(p: Path, key: str) -> None:
    if is_licensed(p):
        return
    st.download_button("Download", data=p.read_bytes(), file_name=p.name, key=key, icon=":material/download:",
                       on_click="ignore")
