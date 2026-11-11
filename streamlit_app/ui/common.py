"""Small shared UI pieces: badges, banners, error UX, page links."""
from __future__ import annotations

import traceback
from typing import Any

import streamlit as st

from core.capabilities import quick_status
from core.catalog import RUNTIME_LABELS
from core.settings import get_secret, load_settings, redact

STATUS_BADGE = {
    "queued": ("Queued", "gray", ":material/schedule:"),
    "running": ("Running", "blue", ":material/progress_activity:"),
    "succeeded": ("Succeeded", "green", ":material/check_circle:"),
    "failed": ("Failed", "red", ":material/error:"),
    "cancelled": ("Cancelled", "orange", ":material/cancel:"),
}

#: page key -> (file, title) – filled by app.py so links work everywhere.
PAGE_FILES: dict[str, str] = {
    "home": "app_pages/home.py", "build": "app_pages/build.py", "defects": "app_pages/defects.py",
    "surfaces": "app_pages/surfaces.py", "vasp": "app_pages/vasp_inputs.py", "workflows": "app_pages/dft_workflows.py",
    "mlp": "app_pages/mlp.py", "mltoolkit": "app_pages/ml_toolkit.py", "assistant": "app_pages/assistant.py",
    "library": "app_pages/library.py", "projects": "app_pages/projects.py", "settings": "app_pages/settings.py",
    "doctor": "app_pages/doctor.py", "help": "app_pages/help.py", "more": "app_pages/more_tools.py",
}


def status_badge(status: str) -> None:
    label, color, icon = STATUS_BADGE.get(status, (status.title(), "gray", ":material/help:"))
    st.badge(label, color=color, icon=icon)


def runtime_badge(runtime: str) -> None:
    label, color = RUNTIME_LABELS.get(runtime, (runtime, "gray"))
    st.badge(f"Runtime: {label}", color=color, icon=":material/timer:")


def requirement_issues(needs: list[str]) -> tuple[list[str], list[str]]:
    """(blocking problems, soft warnings) for a tool's needs."""
    blocking, soft = [], []
    for n in needs:
        if n == "MP":
            if not get_secret("MP_API_KEY"):
                blocking.append("A Materials Project API key is required: add it in **Settings → Materials Project**.")
        elif n == "PSP":
            if not load_settings().psp_dir:
                soft.append("No POTCAR library configured: `POTCAR*.spec` placeholders will be written instead of licensed POTCARs.")
        elif n == "VASP_OUTPUTS":
            pass
        else:
            stt = quick_status(n)
            if not stt.available:
                blocking.append(f"**Unavailable:** {stt.purpose} needs `{stt.key}` ({stt.reason}). Install: `{stt.install}`")
    return blocking, soft


def vasp_banner() -> None:
    st.info(
        "VASP is never executed by this app. Workflows here **generate inputs** for your cluster and **analyze outputs** "
        "you bring back (ZIP upload).", icon=":material/dns:")
    with st.expander("How to run this on your cluster", icon=":material/terminal:"):
        st.markdown(
            "1. Download the generated folder as a ZIP (POTCARs are never included).\n"
            "2. Copy it to the cluster: `scp workflow.zip user@cluster:~/` then `unzip workflow.zip`.\n"
            "3. Create the POTCARs from your licensed library (each `*.spec` file shows the exact `cat …` command).\n"
            "4. Edit `submit_temp.sh` (partition, modules, cores), then run `bash RUN_ME.sh` to submit every job.\n"
            "5. When jobs finish, zip the whole folder (`zip -r results.zip eos_calculations`) and upload it in the *Analyze* tab.\n\n"
            "Only `vasprun.xml`, `OUTCAR`, `OSZICAR`, `XDATCAR`, `INCAR`, `POSCAR`, `CONTCAR` and `KPOINTS` are read; large files "
            "such as `WAVECAR`/`CHGCAR` can be left out of the ZIP.")


def friendly_error(message: str, exc: BaseException | None = None, tb: str | None = None) -> None:
    st.error(redact(message), icon=":material/error:")
    detail = tb or (traceback.format_exception(exc) if exc else None)
    if detail:
        with st.expander("Technical details"):
            st.code(redact("".join(detail) if isinstance(detail, list) else detail), language="text")


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(s: float | None) -> str:
    if s is None:
        return "–"
    s = float(s)
    if s < 60:
        return f"{s:.1f} s"
    m, sec = divmod(int(s), 60)
    if m < 60:
        return f"{m} min {sec} s"
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


def goto(page: str, **state: Any) -> None:
    for k, v in state.items():
        st.session_state[k] = v
    st.switch_page(PAGE_FILES[page])
