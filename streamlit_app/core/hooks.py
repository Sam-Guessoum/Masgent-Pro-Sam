"""Runtime instrumentation applied **only inside a worker process**.

These hooks never touch files under ``src/masgent``; they patch third-party
objects in the worker's memory for the duration of one run:

* ``potcar_placeholder`` – write ``POTCAR*.spec`` instead of failing when no
  licensed POTCAR library is configured (KNOWN_ISSUES K01).
* ``optuna_live`` – stream every finished Optuna trial to ``progress/optuna_trials.jsonl``.
* ``training_live`` – stream train/validation loss per epoch to ``progress/loss.jsonl``.
* ``cvae_live`` – stream CVAE loss to ``progress/cvae_loss.jsonl``.
* ``force_cpu`` – hide CUDA devices (device selector, K16).
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path


class _Jsonl:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, obj: dict) -> None:
        obj.setdefault("t", round(time.time(), 3))
        self._f.write(json.dumps(obj, default=float) + "\n")
        self._f.flush()


# ------------------------------------------------------------------ POTCAR
class PotcarPlaceholder:
    """Stand-in for ``pymatgen.io.vasp.Potcar`` used when no PSP library exists."""

    def __init__(self, symbols: list[str], functional: str | None):
        self.symbols = list(symbols)
        self.functional = functional or "PBE"

    def __iter__(self):
        return iter(())

    def write_file(self, filename: str) -> None:
        spec = Path(f"{filename}.spec")
        cat = " ".join(f"$VASP_PSP_DIR/{s}/POTCAR" for s in self.symbols)
        spec.write_text(
            "# POTCAR placeholder written by the Masgent Streamlit app.\n"
            "# VASP pseudopotentials are licensed and no POTCAR library is configured on this machine.\n"
            f"# Functional: {self.functional}\n"
            f"# Symbols (in POSCAR order): {' '.join(self.symbols)}\n"
            "#\n# On your cluster, assemble the real file with e.g.:\n"
            f"#   cat {cat} > {Path(filename).name}\n"
            "# (set VASP_PSP_DIR to your licensed potpaw_PBE / potpaw_PBE.54 folder)\n",
            encoding="utf-8",
        )


def potcar_placeholder() -> None:
    from pymatgen.io.vasp import sets

    def _placeholder(self):  # noqa: ANN001
        if self.structure is None:
            raise RuntimeError("No structure is associated with the input set!")
        return PotcarPlaceholder(self.potcar_symbols, str(self.user_potcar_functional))

    sets.VaspInputSet.potcar = property(_placeholder)


# ------------------------------------------------------------------ Optuna
def optuna_live(progress_dir: Path, n_trials_hint: int | None = None) -> None:
    import optuna

    sink = _Jsonl(progress_dir / "optuna_trials.jsonl")
    orig_create = optuna.create_study

    def create_study(*args, **kwargs):  # noqa: ANN002
        study = orig_create(*args, **kwargs)
        orig_optimize = study.optimize

        def cb(st, trial):  # noqa: ANN001
            try:
                best = st.best_value
            except Exception:
                best = None
            sink.write({"number": trial.number, "state": trial.state.name, "value": trial.value,
                        "best": best, "params": trial.params, "n_trials": n_trials_hint})

        def optimize(*a, **kw):  # noqa: ANN002
            kw["callbacks"] = list(kw.get("callbacks") or []) + [cb]
            try:
                return orig_optimize(*a, **kw)
            finally:
                # Masgent pickles the study afterwards (joblib.dump); remove our closure first.
                study.__dict__.pop("optimize", None)

        study.optimize = optimize
        return study

    optuna.create_study = create_study


# ---------------------------------------------------------------- training
def training_live(progress_dir: Path) -> None:
    """Record (train, valid) loss pairs from ``masgent.utils.ml_nn_train.train``.

    The training loop calls ``loss_func`` exactly twice per epoch (train, then
    validation) before ``backward()``; evaluation afterwards uses no loss.
    """
    import torch

    sink = _Jsonl(progress_dir / "loss.jsonl")
    base = torch.nn.MSELoss
    state = {"pending": None, "epoch": 0}

    class RecordingMSELoss(base):  # type: ignore[misc, valid-type]
        def forward(self, input, target):  # noqa: A002, ANN001
            out = super().forward(input, target)
            try:
                val = float(out.detach().cpu())
            except Exception:
                return out
            if state["pending"] is None:
                state["pending"] = val
            else:
                sink.write({"epoch": state["epoch"], "train": state["pending"], "valid": val})
                state["epoch"] += 1
                state["pending"] = None
            return out

    torch.nn.MSELoss = RecordingMSELoss


def cvae_live(progress_dir: Path) -> None:
    from masgent.utils import ml_cvae

    sink = _Jsonl(progress_dir / "cvae_loss.jsonl")
    orig = ml_cvae.cvae_loss
    counter = {"n": 0}

    def recorded(x, x_recon, mu, logvar):  # noqa: ANN001
        loss = orig(x, x_recon, mu, logvar)
        counter["n"] += 1
        if counter["n"] % 5 == 0:
            try:
                sink.write({"step": counter["n"], "loss": float(loss.detach())})
            except Exception:
                pass
        return loss

    ml_cvae.cvae_loss = recorded


# ------------------------------------------------------------------ device
def force_cpu() -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""


def seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass
    os.environ["PYTHONHASHSEED"] = str(seed)
