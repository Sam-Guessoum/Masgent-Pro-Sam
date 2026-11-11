"""Setup doctor and diagnostic reports (never include secrets)."""
from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core import capabilities
from core.paths import ensure_masgent_importable
from core.settings import SECRET_KEYS, get_secret, load_settings, mask, redact

#: Verified on download.pytorch.org (2026-11-11): torch 2.14.0 for Windows / Python 3.12 is published for CUDA 13.0
#: (cu130, supports RTX 50-series sm_120); same versions as the CPU build installed now, so other packages stay compatible.
TORCH_CUDA_CMD = ('uv pip install --python .venv\\Scripts\\python.exe --reinstall-package torch --reinstall-package torchvision '
                  '--reinstall-package torchaudio "torch==2.14.0" "torchvision==0.29.0" "torchaudio==2.11.0" '
                  '--index-url https://download.pytorch.org/whl/cu130')


@dataclass
class Check:
    name: str
    ok: bool | None  # None = warning / optional
    detail: str
    fix: str = ""


def run_checks(workspace: Path, deep: bool = False) -> list[Check]:
    out: list[Check] = []
    v = sys.version_info
    out.append(Check("Python version", (3, 11) <= (v.major, v.minor) < (3, 14), f"{platform.python_version()} ({sys.executable})",
                     "Masgent needs Python 3.11–3.13. Recreate the venv: `uv venv --python 3.12 .venv`."))
    ensure_masgent_importable()
    try:
        import importlib.metadata as md

        import masgent

        ver = md.version("masgent") if True else "?"
        out.append(Check("Masgent importable", True, f"masgent {ver} from {Path(masgent.__file__).parent}"))
    except Exception as e:
        out.append(Check("Masgent importable", False, str(e), "From the repo root: `uv pip install --python .venv\\Scripts\\python.exe -e .`"))

    for key, st in capabilities.quick_all().items():
        core = key in ("pymatgen", "ase")
        out.append(Check(f"Package: {capabilities.BACKENDS[key].dist}", st.available if core else (True if st.available else None),
                         f"{st.version}" if st.available else st.reason, "" if st.available else st.install))

    if deep:
        res = capabilities.deep_check(workspace / "cache" / "capabilities.json")["results"]
        for key, r in res.items():
            out.append(Check(f"Import test: {key}", True if r.get("ok") else (False if key in ("pymatgen", "ase") else None),
                             f"OK in {r.get('seconds')} s" if r.get("ok") else r.get("error", "failed"),
                             "" if r.get("ok") else capabilities.BACKENDS[key].install))

    import importlib.util as _u

    if _u.find_spec("torch") and not _u.find_spec("functorch"):
        out.append(Check("torch installation integrity", None,
                         "torch is installed but its bundled 'functorch' package is missing, so torch.compile fails. "
                         "Orb-v3 needs it; other tools are unaffected.",
                         f"Reinstall torch (CUDA 13.0 build recommended for the RTX 5050): `{TORCH_CUDA_CMD}`"))

    gpu = capabilities.gpu_info(workspace / "cache" / "capabilities.json", max_age_s=3600 if not deep else 0)
    smi = capabilities.nvidia_smi()
    if gpu.get("cuda_available") and gpu.get("gpu_supported", True):
        out.append(Check("GPU (CUDA)", True, f"{gpu.get('gpu_name')} · torch {gpu.get('torch')} · CUDA {gpu.get('cuda_build')}"))
    elif gpu.get("cuda_available"):
        out.append(Check("GPU (CUDA)", None, f"{gpu.get('gpu_name')} visible but torch lacks kernels for {gpu.get('capability')}",
                         f"Install the CUDA 13.0 build: `{TORCH_CUDA_CMD}`"))
    else:
        detail = f"torch {gpu.get('torch', 'n/a')} (CUDA build: {gpu.get('cuda_build')})"
        if smi:
            detail += f"; nvidia-smi sees: {smi}"
        out.append(Check("GPU (CUDA)", None, detail + ". Everything runs on CPU.",
                         f"For RTX 50-series GPUs install the CUDA 13.0 wheel: `{TORCH_CUDA_CMD}`" if smi else ""))

    mp = get_secret("MP_API_KEY")
    out.append(Check("Materials Project key", True if mp else None, f"MP_API_KEY = {mask(mp)}",
                     "" if mp else "Settings → Materials Project, or add MP_API_KEY=... to streamlit_app/.env"))
    llm = [n for n in SECRET_KEYS if n != "MP_API_KEY" and get_secret(n)]
    out.append(Check("LLM provider keys", True if llm else None, ", ".join(llm) or "none (the hosted 'Masgent AI' provider needs no key)"))

    s = load_settings()
    if s.psp_dir:
        ok = Path(s.psp_dir).is_dir()
        out.append(Check("POTCAR library", ok, s.psp_dir, "" if ok else "Folder not found; fix it in Settings."))
    else:
        out.append(Check("POTCAR library", None, "Not configured: POTCAR.spec placeholders will be written.",
                         "Optional: point Settings → VASP → POTCAR library at your licensed potpaw folder."))
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=workspace, delete=True) as f:
            f.write(b"ok")
        out.append(Check("Workspace writable", True, str(workspace)))
    except Exception as e:
        out.append(Check("Workspace writable", False, f"{workspace}: {e}", "Choose another folder in Settings."))
    return out


def diagnostic_report(rec, max_log_lines: int = 60) -> str:  # noqa: ANN001
    """Text you can paste into an issue. Secrets are redacted."""
    env = rec.env()
    lines = [
        "### Masgent Streamlit diagnostic report",
        f"run: {rec.run_id}",
        f"tool: {rec.meta.get('tool')} (masgent function {rec.meta.get('func')})",
        f"status: {rec.meta.get('status')}",
        f"error: {rec.meta.get('error')}",
        f"python: {env.get('python')} · platform: {env.get('platform')} · device: {env.get('device')}",
        "packages: " + ", ".join(f"{k}={v}" for k, v in (env.get("packages") or {}).items()),
        "parameters: " + json.dumps(rec.inputs().get("params", {}), default=str),
        "",
        f"last {max_log_lines} log lines:",
        *rec.log_text(80_000).splitlines()[-max_log_lines:],
    ]
    return redact("\n".join(lines))
