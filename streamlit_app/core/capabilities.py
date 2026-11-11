"""Detect optional dependencies, GPU and keys without crashing the app.

Two levels:

* ``quick_status()`` – ``importlib.util.find_spec`` + installed version. Costs
  milliseconds; used on every page.
* ``deep_check()`` – actually imports each backend (and torch/CUDA) in a
  **subprocess** with a timeout, so a broken native extension can never take
  down the Streamlit server. Results are cached to ``capabilities.json``.

Set ``MASGENT_UI_HIDE_DEPS=icet,sevenn`` to simulate missing packages (tests).
"""
from __future__ import annotations

import importlib.metadata as md
import importlib.util
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from core.paths import APP_DIR


@dataclass(frozen=True)
class Backend:
    key: str
    module: str
    dist: str
    purpose: str
    install: str


BACKENDS: dict[str, Backend] = {
    b.key: b
    for b in [
        Backend("pymatgen", "pymatgen.core", "pymatgen", "Structures, VASP input sets", "uv pip install pymatgen"),
        Backend("ase", "ase", "ase", "Structure I/O, optimisers, MD", "uv pip install ase"),
        Backend("mp_api", "mp_api.client", "mp-api", "Materials Project client", "uv pip install mp-api"),
        Backend("defects", "pymatgen.analysis.defects", "pymatgen-analysis-defects", "Interstitial (Voronoi) defects", "uv pip install pymatgen-analysis-defects"),
        Backend("icet", "icet", "icet", "Special quasirandom structures (SQS)", "uv pip install icet"),
        Backend("torch", "torch", "torch", "Neural networks, MLPs, pretrained predictors", "see Setup doctor (CUDA 13.0 wheel)"),
        Backend("sevenn", "sevenn", "sevenn", "SevenNet machine-learning potential", "uv pip install sevenn"),
        Backend("chgnet", "chgnet", "chgnet", "CHGNet machine-learning potential", "uv pip install chgnet"),
        Backend("orb", "orb_models", "orb-models", "Orb-v3 machine-learning potential", "uv pip install orb-models"),
        Backend("mattersim", "mattersim", "mattersim", "MatterSim machine-learning potential", "uv pip install mattersim"),
        Backend("optuna", "optuna", "optuna", "Hyper-parameter optimisation (model design)", "uv pip install optuna"),
        Backend("sklearn", "sklearn", "scikit-learn", "PCA, scaling, metrics", "uv pip install scikit-learn"),
        Backend("pydantic_ai", "pydantic_ai", "pydantic-ai", "AI assistant agent", "uv pip install pydantic-ai"),
    ]
}

#: Deep import statement per backend (what Masgent actually imports).
_DEEP_IMPORTS = {
    "sevenn": "from sevenn.calculator import SevenNetCalculator",
    "chgnet": "from chgnet.model.dynamics import CHGNetCalculator",
    "orb": "from orb_models.forcefield import pretrained; from orb_models.forcefield.calculator import ORBCalculator; import functorch.compile",
    "mattersim": "from mattersim.forcefield import MatterSimCalculator",
    "icet": "from icet import ClusterSpace; from icet.tools.structure_generation import generate_sqs",
    "defects": "from pymatgen.analysis.defects import generators",
    "mp_api": "from mp_api.client import MPRester",
    "pydantic_ai": "from pydantic_ai import Agent",
    "optuna": "import optuna",
    "torch": "import torch",
    "sklearn": "from sklearn.decomposition import PCA",
    "pymatgen": "from pymatgen.io.vasp.sets import MPRelaxSet",
    "ase": "from ase.io import read",
}


def _hidden() -> set[str]:
    return {x.strip() for x in os.environ.get("MASGENT_UI_HIDE_DEPS", "").split(",") if x.strip()}


@dataclass
class DepStatus:
    key: str
    available: bool
    version: str | None
    reason: str = ""
    install: str = ""
    purpose: str = ""


def quick_status(key: str) -> DepStatus:
    b = BACKENDS[key]
    if key in _hidden():
        return DepStatus(key, False, None, "Hidden by MASGENT_UI_HIDE_DEPS (simulation)", b.install, b.purpose)
    try:
        found = importlib.util.find_spec(b.module.split(".")[0]) is not None
        if found and "." in b.module:
            found = importlib.util.find_spec(b.module) is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        return DepStatus(key, False, None, f"Python package '{b.dist}' is not installed", b.install, b.purpose)
    try:
        ver = md.version(b.dist)
    except md.PackageNotFoundError:
        ver = "unknown"
    return DepStatus(key, True, ver, "", b.install, b.purpose)


def quick_all() -> dict[str, DepStatus]:
    return {k: quick_status(k) for k in BACKENDS}


def is_available(*keys: str) -> tuple[bool, list[DepStatus]]:
    missing = [s for s in (quick_status(k) for k in keys) if not s.available]
    return (not missing, missing)


# ----------------------------------------------------------------- deep check
_DEEP_SCRIPT = r"""
import json, sys, time, warnings
warnings.filterwarnings('ignore')
key, stmt = sys.argv[1], sys.argv[2]
t = time.time(); out = {'key': key}
try:
    exec(stmt, {})
    out['ok'] = True
except BaseException as e:
    out['ok'] = False; out['error'] = f'{type(e).__name__}: {e}'[:500]
out['seconds'] = round(time.time() - t, 2)
if key == 'torch' and out['ok']:
    import torch
    out['torch'] = torch.__version__
    out['cuda_build'] = torch.version.cuda
    try:
        out['cuda_available'] = bool(torch.cuda.is_available())
        if out['cuda_available']:
            out['gpu_name'] = torch.cuda.get_device_name(0)
            out['arch_list'] = torch.cuda.get_arch_list()
            cap = torch.cuda.get_device_capability(0)
            out['capability'] = f'sm_{cap[0]}{cap[1]}'
            out['gpu_supported'] = out['capability'] in out['arch_list']
    except Exception as e:
        out['cuda_available'] = False; out['cuda_error'] = str(e)[:300]
print('@@JSON@@' + json.dumps(out))
"""


def _deep_one(key: str, timeout: float = 180.0) -> dict:
    if key in _hidden():
        return {"key": key, "ok": False, "error": "hidden (simulation)"}
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _DEEP_SCRIPT, key, _DEEP_IMPORTS[key]],
            capture_output=True, text=True, timeout=timeout, cwd=str(APP_DIR),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in proc.stdout.splitlines()[::-1]:
            if line.startswith("@@JSON@@"):
                return json.loads(line[8:])
        return {"key": key, "ok": False, "error": (proc.stderr or "no output")[-500:]}
    except subprocess.TimeoutExpired:
        return {"key": key, "ok": False, "error": f"import timed out after {timeout:.0f}s"}
    except Exception as e:  # pragma: no cover
        return {"key": key, "ok": False, "error": f"{type(e).__name__}: {e}"}


def deep_check(cache_file: Path | None = None, keys: list[str] | None = None, max_age_s: float = 0) -> dict:
    """Import each backend in a subprocess. Optionally reuse a recent cache."""
    if cache_file and max_age_s and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - data.get("ts", 0) < max_age_s:
                return data
        except Exception:
            pass
    from concurrent.futures import ThreadPoolExecutor

    keys = keys or list(_DEEP_IMPORTS)
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(_deep_one, keys))
    data = {"ts": time.time(), "results": {r["key"]: r for r in results}}
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def gpu_info(cache_file: Path | None = None, max_age_s: float = 3600) -> dict:
    """torch/CUDA facts (from the deep-check cache, refreshed if stale)."""
    if cache_file and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if "torch" in data.get("results", {}) and time.time() - data.get("ts", 0) < max_age_s:
                return data["results"]["torch"]
        except Exception:
            pass
    res = _deep_one("torch")
    if cache_file:
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {"results": {}}
        except Exception:
            data = {"results": {}}
        data.setdefault("results", {})["torch"] = res
        data["ts"] = data.get("ts", time.time())
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return res


def nvidia_smi() -> str | None:
    """GPU name from nvidia-smi (works even with a CPU-only torch build)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def as_dict(s: DepStatus) -> dict:
    return asdict(s)
