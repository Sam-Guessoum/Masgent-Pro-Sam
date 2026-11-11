"""Parse Masgent output files into numbers for interactive plots and comparisons.

Pure functions (no Streamlit). ``summarize(run)`` returns a few headline
metrics per tool, stored in ``meta.json`` and shown in the Results library.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

EV_A3_TO_GPA = 160.21766208


def read_csv(path, **kw):  # noqa: ANN001, ANN201
    """pandas.read_csv that also accepts files Masgent wrote with the Windows code page (cp1252)."""
    import pandas as pd

    try:
        return pd.read_csv(path, **kw)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp1252", **kw)


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _first(files: Path, pattern: str) -> Path | None:
    hits = sorted(files.rglob(pattern))
    return hits[0] if hits else None


# ------------------------------------------------------------------ elastic
def parse_elastic_txt(path: Path) -> dict[str, Any]:
    text = _read(path)
    rows = []
    lines = text.splitlines()
    try:
        i = next(k for k, l in enumerate(lines) if l.strip().startswith("Elastic Constants"))
        for l in lines[i + 1:i + 7]:
            rows.append([float(x) for x in l.split()])
    except (StopIteration, ValueError):
        rows = []
    moduli = {}
    for key, label in [("K_V", "Bulk Modulus (Voigt)"), ("K_R", "Bulk Modulus (Reuss)"), ("K_H", "Bulk Modulus (Hill)"),
                       ("G_V", "Shear Modulus (Voigt)"), ("G_R", "Shear Modulus (Reuss)"), ("G_H", "Shear Modulus (Hill)")]:
        m = re.search(re.escape(label) + r":\s*([-\d.eE+]+)", text)
        if m:
            moduli[key] = float(m.group(1))
    out: dict[str, Any] = {"C": rows if len(rows) == 6 else None, "moduli": moduli}
    k, g = moduli.get("K_H"), moduli.get("G_H")
    if k and g and (3 * k + g):
        out["moduli"]["E_H"] = 9 * k * g / (3 * k + g)
        out["moduli"]["nu_H"] = (3 * k - 2 * g) / (2 * (3 * k + g))
        out["moduli"]["pugh_K_over_G"] = k / g if g else None
    if out["C"]:
        c = np.array(out["C"])
        try:
            eig = np.linalg.eigvalsh((c + c.T) / 2)
            out["mechanically_stable"] = bool(np.all(eig > 0))
            out["eigenvalues"] = eig.tolist()
        except np.linalg.LinAlgError:
            pass
    return out


# ---------------------------------------------------------------------- EOS
def birch_murnaghan(v: np.ndarray, e0: float, v0: float, b0: float, bp: float) -> np.ndarray:
    eta = (v0 / v) ** (2.0 / 3.0)
    return e0 + 9.0 * v0 * b0 / 16.0 * ((eta - 1) ** 3 * bp + (eta - 1) ** 2 * (6 - 4 * eta))


def fit_birch_murnaghan(volumes: list[float], energies: list[float]) -> dict[str, float] | None:
    """Fit 3rd-order Birch–Murnaghan; B0 returned in GPa. Volumes/energies per atom."""
    from scipy.optimize import curve_fit

    v, e = np.asarray(volumes, float), np.asarray(energies, float)
    if len(v) < 4:
        return None
    a, b, c = np.polyfit(v, e, 2)
    v0 = -b / (2 * a) if a else float(np.mean(v))
    b0 = 2 * a * v0 if a else 0.5
    try:
        popt, _ = curve_fit(birch_murnaghan, v, e, p0=[float(np.min(e)), v0, max(b0, 1e-3), 4.0], maxfev=20000)
    except Exception:
        return None
    e0, v0, b0, bp = popt
    resid = e - birch_murnaghan(v, *popt)
    return {"E0_eV": float(e0), "V0_A3": float(v0), "B0_GPa": float(b0 * EV_A3_TO_GPA), "B0_prime": float(bp),
            "rms_meV": float(np.sqrt(np.mean(resid ** 2)) * 1000)}


def parse_eos_csv(path: Path, natoms: int | None = None) -> dict[str, Any]:
    import pandas as pd

    df = read_csv(path)
    vcol = next(c for c in df.columns if c.lower().startswith("volume"))
    ecol = next(c for c in df.columns if c.lower().startswith("energy"))
    per_atom_volume = "atom" in vcol
    vols = df[vcol].to_numpy(float)
    if not per_atom_volume and natoms:
        vols = vols / natoms
    out = {"volume_per_atom": vols.tolist(), "energy_per_atom": df[ecol].to_numpy(float).tolist(),
           "volume_label": "Volume (Å³/atom)" if (per_atom_volume or natoms) else "Volume (Å³)"}
    out["fit"] = fit_birch_murnaghan(out["volume_per_atom"], out["energy_per_atom"])
    return out


# --------------------------------------------------------------------- logs
def parse_md_log(path: Path) -> list[dict[str, float]]:
    """ASE MDLogger (peratom=True): Time[ps] Etot/N Epot/N Ekin/N T[K] [stress…]."""
    rows = []
    for line in _read(path).splitlines():
        if re.match(r"^\s*\d+\.\d+", line):
            parts = line.split()
            try:
                rows.append({"time_ps": float(parts[0]), "etot": float(parts[1]), "epot": float(parts[2]),
                             "ekin": float(parts[3]), "T": float(parts[4])})
            except (ValueError, IndexError):
                pass
    return rows


_OPT = re.compile(r"^\s*(\w+):\s+(\d+)\s+\S+\s+([-\d.eE+]+)\*?\s+([-\d.eE+]+)")


def parse_opt_log(path: Path) -> list[dict[str, float]]:
    """ASE optimiser log: 'LBFGS:   3 12:00:01   -14.123  0.0123'."""
    rows = []
    for line in _read(path).splitlines():
        m = _OPT.match(line)
        if m:
            rows.append({"step": int(m.group(2)), "energy": float(m.group(3)), "fmax": float(m.group(4))})
    return rows


def parse_keyvals(path: Path) -> dict[str, float]:
    """Generic 'Label: 12.3 unit' lines → {label: value}."""
    out = {}
    for line in _read(path).splitlines():
        m = re.match(r"^\s*([^:#][^:]*?):\s*([-\d.]+(?:[eE][-+]?\d+)?)\s*(\S*)\s*$", line)
        if m:
            out[m.group(1).strip()] = float(m.group(2))
    return out


def parse_prediction_almgsisc(path: Path) -> dict[str, Any]:
    kv = parse_keyvals(path)
    return {
        "composition_wt%": {k: kv.get(k) for k in ("Al", "Mg", "Si", "Sc")},
        "phase_fractions_%": {k: kv.get(k) for k in ("PH_Al", "PH_Eut", "PH_AlSc2Si2")},
        "UTS_MPa": kv.get("Ultimate Tensile Strength (UTS)"),
        "YS_MPa": kv.get("Yield Strength (YS)"),
        "EL_%": kv.get("Elongation (EL)"),
    }


def parse_prediction_hea(path: Path) -> dict[str, Any]:
    text = _read(path)
    out: dict[str, Any] = {"composition_at%": {}}
    for el in ("Al", "Co", "Cr", "Fe", "Ni"):
        m = re.search(rf"^{el}:\s*([-\d.]+)", text, re.M)
        if m:
            out["composition_at%"][el] = float(m.group(1))
    for phase in ("FCC", "BCC"):
        block = text.split(f"of {phase}:")[1] if f"of {phase}:" in text else ""
        block = block.split("-----")[0]
        d: dict[str, Any] = {}
        m = re.search(r"Formation Energy:\s*([-\d.]+)", block)
        d["formation_energy_kJ_mol"] = float(m.group(1)) if m else None
        rows = []
        lines = block.splitlines()
        for i, l in enumerate(lines):
            if l.strip().startswith("Elastic Constants"):
                for r in lines[i + 1:i + 7]:
                    try:
                        rows.append([float(x) for x in r.split()])
                    except ValueError:
                        pass
        d["C"] = rows if len(rows) == 6 else None
        for key, label in [("K_V", "Bulk Modulus (Voigt)"), ("K_R", "Bulk Modulus (Reuss)"), ("K_H", "Bulk Modulus (Hill)"),
                           ("G_V", "Shear Modulus (Voigt)"), ("G_R", "Shear Modulus (Reuss)"), ("G_H", "Shear Modulus (Hill)")]:
            m = re.search(re.escape(label) + r":\s*([-\d.]+)", block)
            d[key] = float(m.group(1)) if m else None
        out[phase] = d
    return out


def parse_performance_log(path: Path) -> dict[str, float]:
    text = _read(path)
    out = {}
    for split in ("Training", "Validation"):
        m = re.search(rf"{split} Set:\s*RMSE:\s*([-\d.eE+]+)\s*R2:\s*([-\d.eE+]+)", text)
        if m:
            out[f"{split.lower()}_rmse"], out[f"{split.lower()}_r2"] = float(m.group(1)), float(m.group(2))
    return out


def parse_best_params_log(path: Path) -> dict[str, Any]:
    text = _read(path)
    out: dict[str, Any] = {"params": {}}
    m = re.search(r"Value:\s*([-\d.eE+]+)", text)
    out["best_value"] = float(m.group(1)) if m else None
    m = re.search(r"Number:\s*(\d+)", text)
    out["best_trial"] = int(m.group(1)) if m else None
    if "Params:" in text:
        for line in text.split("Params:")[1].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip()
                try:
                    out["params"][k.strip()] = float(v) if re.match(r"^[-\d.eE+]+$", v) else v
                except ValueError:
                    out["params"][k.strip()] = v
    return out


# ------------------------------------------------------------------- vasprun
_E0 = re.compile(rb'<i name="e_0_energy">\s*([-\d.eE+]+)')
_FR = re.compile(rb'<i name="e_fr_energy">\s*([-\d.eE+]+)')
_NAT = re.compile(rb"<atoms>\s*(\d+)\s*</atoms>")


def quick_vasprun_energy(path: Path) -> tuple[float, int] | None:
    """Fast final energy (eV) and atom count from vasprun.xml without full parsing."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    nat = _NAT.search(data[:2_000_000])
    for tag, rx in ((b'name="e_0_energy"', _E0), (b'name="e_fr_energy"', _FR)):
        pos = data.rfind(tag)
        if pos >= 0:
            m = rx.search(data, max(0, pos - 4))
            if m and nat:
                return float(m.group(1)), int(nat.group(1))
    return None


def convergence_series(root: Path) -> dict[str, list[tuple[float, float]]]:
    """ENCUT / k-point series (value, meV/atom) from a convergence_tests folder."""
    out: dict[str, list[tuple[float, float]]] = {}
    for kind, sub in (("encut", "encut_tests"), ("kpoints", "kpoint_tests")):
        pts = []
        for vr in sorted((root / sub).glob("*/vasprun.xml")):
            try:
                val = float(vr.parent.name.split("_")[-1])
            except ValueError:
                continue
            q = quick_vasprun_energy(vr)
            if q:
                pts.append((val, q[0] / q[1] * 1000))
        if pts:
            out[kind] = sorted(pts)
    return out


# ------------------------------------------------------------------ summary
def summarize(rec: Any) -> dict[str, Any]:
    """Headline metrics for the library table (best-effort, never raises)."""
    func = rec.meta.get("func", "")
    res = rec.result() or {}
    files = rec.files_dir
    s: dict[str, Any] = {}
    try:
        if func == "run_simulation_using_mlps":
            if "energy_per_atom (eV/atom)" in res:
                s["E/atom (eV)"] = round(res["energy_per_atom (eV/atom)"], 5)
                s["E total (eV)"] = round(res["total_energy (eV)"], 4)
            if (p := _first(files, "elastic_constants.txt")):
                m = parse_elastic_txt(p)["moduli"]
                s.update({k: round(v, 2) for k, v in m.items() if k in ("K_H", "G_H") and v is not None})
            if (p := _first(files, "eos_cal.csv")):
                fit = parse_eos_csv(p, natoms=_natoms(files)).get("fit")
                if fit:
                    s.update({"V0 (Å³/atom)": round(fit["V0_A3"], 4), "B0 (GPa)": round(fit["B0_GPa"], 1)})
            if (p := _first(files, "masgent_mlps_md.log")):
                md = parse_md_log(p)
                if md:
                    s["<T> (K)"] = round(float(np.mean([r["T"] for r in md])), 1)
                    s["MD time (ps)"] = md[-1]["time_ps"]
        elif func == "analyze_vasp_workflow_of_elastic_constants" and (p := _first(files, "elastic_constants.txt")):
            m = parse_elastic_txt(p)["moduli"]
            s.update({k: round(v, 2) for k, v in m.items() if k in ("K_H", "G_H") and v is not None})
        elif func == "analyze_vasp_workflow_of_eos" and (p := _first(files, "eos_cal.csv")):
            fit = parse_eos_csv(p).get("fit")
            if fit:
                s.update({"V0 (Å³/atom)": round(fit["V0_A3"], 4), "B0 (GPa)": round(fit["B0_GPa"], 1)})
        elif func == "analyze_vasp_workflow_of_neb" and (p := _first(files, "energy_barrier.txt")):
            s["Barrier (meV)"] = round(parse_keyvals(p).get("Energy Barrier", float("nan")), 2)
        elif func == "analyze_vasp_workflow_of_aimd" and (p := _first(files, "aimd_activation_energy.txt")):
            s["Ea (meV)"] = parse_keyvals(p).get("Activation Energy")
        elif func == "model_prediction_for_AlMgSiSc" and (p := _first(files, "AlMgSiSc_prediction.txt")):
            d = parse_prediction_almgsisc(p)
            s.update({"UTS (MPa)": d["UTS_MPa"], "YS (MPa)": d["YS_MPa"], "EL (%)": d["EL_%"]})
        elif func == "model_prediction_for_AlCoCrFeNi" and (p := _first(files, "AlCoCrFeNi_prediction.txt")):
            d = parse_prediction_hea(p)
            s.update({"FCC Ef (kJ/mol)": d["FCC"]["formation_energy_kJ_mol"], "BCC Ef (kJ/mol)": d["BCC"]["formation_energy_kJ_mol"]})
        elif func in ("train_model_for_machine_learning", "retrain_model_for_machine_learning") and (p := _first(files, "performance.log")):
            s.update({k: round(v, 4) for k, v in parse_performance_log(p).items()})
        elif func == "design_model_for_machine_learning" and (p := _first(files, "best_model_params.log")):
            d = parse_best_params_log(p)
            s["best CV loss"] = d.get("best_value")
        else:
            poscars = [p for p in files.rglob("*") if p.is_file() and not p.relative_to(files).as_posix().startswith("inputs/")
                       and (p.name.startswith(("POSCAR", "CONTCAR")) or p.suffix == ".vasp")]
            if poscars:
                s["structures"] = len(poscars)
                n = _natoms_of(poscars[0])
                if n:
                    s["atoms"] = n
    except Exception:
        pass
    return s


def _natoms_of(path: Path) -> int | None:
    lines = _read(path).splitlines()
    try:
        for idx in (6, 5):
            vals = lines[idx].split()
            if vals and all(v.isdigit() for v in vals):
                return sum(int(v) for v in vals)
    except IndexError:
        return None
    return None


def _natoms(files: Path) -> int | None:
    p = _first(files / "inputs", "*")
    return _natoms_of(p) if p else None
