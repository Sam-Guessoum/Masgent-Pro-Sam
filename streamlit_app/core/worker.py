"""Worker process: runs exactly one Masgent tool call, isolated from the UI.

Lifecycle (started by ``core.runner`` as ``python -I worker.py``):

1. *Warm-up*: import ``masgent.utils.tools`` with a placeholder
   ``MASGENT_SESSION_RUNS_DIR`` while waiting for work (hides the import cost).
2. Read **one** JSON job line from stdin, then replace stdin with ``/dev/null`` so
   any ``input()`` inside Masgent fails fast instead of hanging.
3. Point ``MASGENT_SESSION_RUNS_DIR`` and the CWD at the session folder,
   redirect fd 1/2 to ``logs.txt``, inject environment (MP key, PSP dir, device),
   apply hooks, seed RNGs, call the tool.
4. Write ``result.json`` (atomic) and exit. Cancellation = the parent kills us.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import time
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from core.paths import RUNS_DIR_PLACEHOLDER, ensure_masgent_importable  # noqa: E402


def _atomic_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    for i in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    os.replace(tmp, path)


def _jsonable(obj):  # noqa: ANN001
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except Exception:
        pass
    return str(obj)


def warm_up() -> None:
    os.environ["MASGENT_SESSION_RUNS_DIR"] = RUNS_DIR_PLACEHOLDER
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    import warnings

    warnings.filterwarnings("ignore")
    ensure_masgent_importable()
    try:
        from masgent.utils import tools  # noqa: F401
    except Exception:
        pass  # surfaced properly when the job runs


def redirect_output(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    sys.stdout = open(1, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False)
    sys.stderr = open(2, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    sys.stdin = open(os.devnull, "r", encoding="utf-8")


def resolve_placeholders(func, params: dict, session_dir: str) -> dict:  # noqa: ANN001
    """Fill omitted args whose import-time default embeds the placeholder."""
    out = dict(params)
    for name, p in inspect.signature(func).parameters.items():
        if name in out:
            if isinstance(out[name], str) and RUNS_DIR_PLACEHOLDER in out[name]:
                out[name] = out[name].replace(RUNS_DIR_PLACEHOLDER, session_dir)
            continue
        if isinstance(p.default, str) and RUNS_DIR_PLACEHOLDER in p.default:
            out[name] = p.default.replace(RUNS_DIR_PLACEHOLDER, session_dir)
    return out


def run_job(job: dict) -> int:
    run_dir = Path(job["run_dir"])
    session_dir = Path(job.get("session_dir") or run_dir / "files")
    session_dir.mkdir(parents=True, exist_ok=True)
    redirect_output(run_dir / "logs.txt")
    started = time.time()
    print(f"[masgent-ui] run {job['run_id']} - tool {job['func']} - pid {os.getpid()}", flush=True)

    # Environment for this run only (keys never written to disk by us).
    for k, v in (job.get("env") or {}).items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    os.environ["MASGENT_SESSION_RUNS_DIR"] = str(session_dir)
    os.chdir(session_dir)

    result: dict
    try:
        from core import hooks

        if job.get("device") == "cpu":
            hooks.force_cpu()
        hooks.seed_everything(int(job.get("seed", 42)))
        psp = (job.get("env") or {}).get("PMG_VASP_PSP_DIR")
        if psp:
            from pymatgen.core import SETTINGS

            SETTINGS["PMG_VASP_PSP_DIR"] = psp
        else:
            hooks.potcar_placeholder()
        progress_dir = run_dir / "progress"
        for h in job.get("hooks") or []:
            if h == "optuna_live":
                hooks.optuna_live(progress_dir, (job.get("params") or {}).get("n_trials"))
            elif h == "training_live":
                hooks.training_live(progress_dir)
            elif h == "cvae_live":
                hooks.cvae_live(progress_dir)
        if job.get("needs_torch_seed"):
            try:
                import torch

                torch.manual_seed(int(job.get("seed", 42)))
            except Exception:
                pass

        from masgent.utils import tools
        from masgent.utils import tools as _t  # noqa: F401  (ensure module import errors surface here)

        func = getattr(tools, job["func"])
        params = resolve_placeholders(func, job.get("params") or {}, str(session_dir))
        print(f"[masgent-ui] calling {job['func']} with {sorted(params)}", flush=True)
        raw = func(**params)
        result = _jsonable(raw) if isinstance(raw, dict) else {"status": "success", "message": str(raw)}
    except SystemExit as e:  # Masgent calls sys.exit on a bad MP key (K12)
        result = {"status": "error", "message": f"Masgent tried to exit the program (code {e.code}). "
                  "This usually means the Materials Project API key is missing or invalid: check Settings."}
    except BaseException as e:  # noqa: BLE001
        tb = traceback.format_exc()
        print(tb, file=sys.stderr, flush=True)
        result = {"status": "error", "message": f"{type(e).__name__}: {e}", "traceback": tb}
    result.setdefault("status", "success")
    result["_elapsed_s"] = round(time.time() - started, 3)
    print(f"[masgent-ui] finished with status={result.get('status')} in {result['_elapsed_s']} s", flush=True)
    _atomic_json(run_dir / "result.json", result)
    sys.stdout.flush()
    sys.stderr.flush()
    return 0


def main() -> int:
    warm_up()
    line = sys.stdin.readline()
    if not line.strip():
        return 0  # pool shutdown: parent closed our stdin
    job = json.loads(line)
    return run_job(job)


if __name__ == "__main__":
    sys.exit(main())
