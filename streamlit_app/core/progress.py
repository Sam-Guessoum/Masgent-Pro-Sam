"""Live progress extracted from a run folder while the worker is running.

Reads only files (logs, Masgent's own logs, hook jsonl streams), so it works for
any run, including runs adopted after an app restart.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.parsers import parse_md_log, parse_opt_log

_TQDM = re.compile(r"(?P<desc>[^\r\n|]*?):?\s*(?P<pct>\d{1,3})%\|[^|]*\|\s*(?P<n>\d+)/(?P<total>\d+)")


@dataclass
class Progress:
    fraction: float | None = None
    text: str = ""
    md: list[dict] = field(default_factory=list)
    opt: dict[str, list[dict]] = field(default_factory=dict)
    trials: list[dict] = field(default_factory=list)
    loss: list[dict] = field(default_factory=list)
    cvae: list[dict] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def tail_text(path: Path, max_bytes: int = 60_000) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        data = f.read().decode("utf-8", errors="replace")
    # Collapse carriage-return progress bars into their latest state per line.
    out = []
    for line in data.split("\n"):
        out.append(line.split("\r")[-1] if "\r" in line else line)
    return "\n".join(out)


def _jsonl(path: Path, limit: int = 20000) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def read_progress(run_dir: Path, meta: dict, params: dict | None = None) -> Progress:
    pr = Progress()
    files = run_dir / "files"
    session = Path(meta.get("session_dir")) if meta.get("session_dir") else files
    func = meta.get("func", "")
    params = params or {}
    log = tail_text(run_dir / "logs.txt")

    m = None
    for m in _TQDM.finditer(log):
        pass
    if m:
        n, total = int(m.group("n")), int(m.group("total"))
        if total:
            pr.fraction = min(1.0, n / total)
            pr.text = f"{m.group('desc').strip() or 'Progress'}: {n}/{total}"

    if func == "run_simulation_using_mlps":
        for md in session.rglob("masgent_mlps_md.log"):
            pr.md = parse_md_log(md)
            steps = params.get("md_steps")
            if pr.md and steps:
                dt_ps = float(params.get("md_timestep", 5.0)) / 1000.0
                done = pr.md[-1]["time_ps"] / (dt_ps * steps) if dt_ps else None
                if done is not None:
                    pr.fraction = min(1.0, done)
                    pr.text = f"MD: {pr.md[-1]['time_ps']:.3f} ps of {dt_ps * steps:.3f} ps"
        for lg in sorted(session.rglob("masgent_mlps_*.log")):
            if lg.name != "masgent_mlps_md.log":
                pr.opt[lg.stem.replace("masgent_mlps_", "")] = parse_opt_log(lg)
        task = params.get("task_type")
        if task in ("eos", "elastic") and pr.opt:
            total = len(params.get("scale_factors") or []) if task == "eos" else 13
            if total:
                pr.fraction = min(1.0, max(0, len(pr.opt) - 1) / total)
                pr.text = f"{task.upper()}: structure {len(pr.opt)} of {total}"
    elif func == "design_model_for_machine_learning":
        pr.trials = _jsonl(run_dir / "progress" / "optuna_trials.jsonl")
        n = params.get("n_trials")
        if n:
            pr.fraction = min(1.0, len(pr.trials) / n)
            pr.text = f"Optuna: {len(pr.trials)} of {n} trials"
    elif func in ("train_model_for_machine_learning", "retrain_model_for_machine_learning"):
        pr.loss = _jsonl(run_dir / "progress" / "loss.jsonl")
        n = params.get("max_epochs")
        if pr.loss and n:
            pr.fraction = min(1.0, len(pr.loss) / n)
            pr.text = f"Epoch {len(pr.loss)} (max {n}; early stopping may end sooner)"
    elif func == "augment_data_for_machine_learning":
        pr.cvae = _jsonl(run_dir / "progress" / "cvae_loss.jsonl")
    elif func == "generate_sqs_from_poscar":
        sqs_log = session / "sqs" / "masgent_sqs.log"
        if sqs_log.exists():
            pr.extra["sqs_log"] = tail_text(sqs_log, 8000)
    started = meta.get("started")
    if started:
        import datetime as dt

        try:
            pr.extra["elapsed_s"] = time.time() - dt.datetime.fromisoformat(started).timestamp()
        except ValueError:
            pass
    return pr
