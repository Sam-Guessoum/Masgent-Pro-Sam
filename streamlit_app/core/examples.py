"""Bundled example data: small structures/CSVs in ``streamlit_app/assets`` and
(read-only) real VASP outputs from the repository's ``examples/`` folder.

Analysis examples are copied with a *whitelist* so that licensed POTCARs and
huge binaries (WAVECAR, CHGCAR …) never enter the workspace.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from core.paths import APP_DIR, REPO_DIR

ASSETS = APP_DIR / "assets"

STRUCTURES: dict[str, str] = {
    "fcc Al (mp-134)": "structures/POSCAR_Al",
    "fcc Cu": "structures/POSCAR_Cu",
    "Diamond Si": "structures/POSCAR_Si",
    "Rock-salt MgO": "structures/POSCAR_MgO",
    "Rock-salt NaCl": "structures/POSCAR_NaCl",
    "Rock-salt NiO": "structures/POSCAR_NiO",
    "Zinc-blende GaAs": "structures/POSCAR_GaAs",
    "Rutile TiO2": "structures/POSCAR_TiO2",
    "MoS2": "structures/POSCAR_MoS2",
    "Perovskite LaCoO3": "structures/POSCAR_LaCoO3",
    "La2NiO4": "structures/POSCAR_La2NiO4",
    "NEB initial image (La-Ni-O)": "neb/POSCAR_initial",
    "NEB final image (La-Ni-O)": "neb/POSCAR_final",
}

ML_FILES: dict[str, str] = {
    "AlCoCrFeNi inputs (compositions)": "ml/ml_input_data.csv",
    "AlCoCrFeNi outputs (FCC properties)": "ml/ml_output_data.csv",
    "Example designed model (best_model.pkl)": "ml/best_model.pkl",
    "Example model parameters (best_model_params.log)": "ml/best_model_params.log",
}

_WF = REPO_DIR / "examples" / "1. Density Functional Theory (DFT) Simulations" / "1.3 Standard VASP Workflow Preparation and Output Analysis"

#: Example VASP output folders for each analysis tool.
ANALYSIS_EXAMPLES: dict[str, Path] = {
    "convergence": _WF / "convergence_tests",
    "eos": _WF / "eos_calculations",
    "elastic": _WF / "elastic_constants",
    "aimd": _WF / "aimd_simulations",
    "neb": _WF / "neb_calculations",
}

#: Files the analysis tools actually read (everything else is skipped).
ANALYSIS_WHITELIST = {"vasprun.xml", "OUTCAR", "OSZICAR", "XDATCAR", "INCAR", "POSCAR", "CONTCAR", "KPOINTS"}


def structure_path(name: str) -> Path | None:
    rel = STRUCTURES.get(name)
    p = ASSETS / rel if rel else None
    return p if p and p.exists() else None


def ml_path(name: str) -> Path | None:
    rel = ML_FILES.get(name)
    p = ASSETS / rel if rel else None
    return p if p and p.exists() else None


def analysis_example(kind: str) -> Path | None:
    p = ANALYSIS_EXAMPLES.get(kind)
    return p if p and p.is_dir() else None


def copy_whitelisted_tree(src: Path, dest: Path, whitelist: set[str] = ANALYSIS_WHITELIST,
                          max_file_mb: int = 300) -> list[str]:
    """Copy only whitelisted file names from ``src`` into ``dest`` (structure preserved)."""
    copied = []
    for p in sorted(Path(src).rglob("*")):
        if not p.is_file() or p.name not in whitelist:
            continue
        if p.stat().st_size > max_file_mb * 1024 * 1024:
            continue
        rel = p.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        copied.append(rel.as_posix())
    return copied


def resolve_ref(value):  # noqa: ANN001, ANN201
    """Turn 'example:…' / 'example_ml:…' / 'example_folder:…' references into real paths."""
    if not isinstance(value, str):
        return value
    if value.startswith("example:"):
        p = structure_path(value.split(":", 1)[1])
    elif value.startswith("example_ml:"):
        p = ml_path(value.split(":", 1)[1])
    elif value.startswith("example_folder:"):
        p = analysis_example(value.split(":", 1)[1])
    else:
        return value
    return str(p) if p else value


def resolve_params(params: dict) -> dict:
    return {k: resolve_ref(v) for k, v in params.items()}


def ref_label(value) -> str:  # noqa: ANN001
    if isinstance(value, str) and ":" in value and value.split(":", 1)[0] in ("example", "example_ml", "example_folder"):
        kind, name = value.split(":", 1)
        return {"example": "Example structure", "example_ml": "Example data", "example_folder": "Masgent example outputs"}[kind] + f": {name}"
    return Path(str(value)).name if value else ""
