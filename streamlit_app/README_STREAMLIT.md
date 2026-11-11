# Masgent Streamlit app

A local web interface for **every** Masgent function. It covers structure building, defects/SQS, slabs and interfaces, VASP inputs, the five DFT workflows (generate + analyze), the four ML potentials, the ML toolkit with pretrained predictors, and Masgent's AI agent. Every run is saved, so you can reopen, compare, re-run and export it later.

* Masgent is used **as a library**. Nothing under `src/masgent/` is modified.
* VASP is **never executed** by the app. It prepares inputs for your cluster and analyses the outputs you bring back.
* Everything lives in `streamlit_app/`. Runs are saved in `streamlit_app/workspace/` (gitignored).

## 1. Setup (Windows, PowerShell)

Run all commands from the repository root (`D:\PROJECTS\Masgent`). The repo already has a uv-managed `.venv` (Python 3.12) with all of Masgent's dependencies. If you need to recreate it:

```powershell
# only if .venv is missing (Masgent supports Python 3.11–3.13)
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e .        # Masgent + all its dependencies
```

Install Masgent in editable mode from your local folder (does not change repo files; it only adds `src\masgent.egg-info`), plus the app's extras:

```powershell
uv pip install --python .venv\Scripts\python.exe --no-deps -e .
uv pip install --python .venv\Scripts\python.exe -r streamlit_app\requirements.txt
```

> The venv was created by **uv**, so it has no `pip` inside. Use `uv pip …` as above. With a plain `python -m venv`, replace `uv pip install --python .venv\Scripts\python.exe` by `.venv\Scripts\python.exe -m pip install`.

### Keys

```powershell
Copy-Item streamlit_app\.env.example streamlit_app\.env
notepad streamlit_app\.env      # paste MP_API_KEY=... (free at https://materialsproject.org/api)
```

You can also paste keys in the app (**Settings**). They are stored only in `streamlit_app\.env`, are shown masked, and never end up in runs, logs or exports. LLM keys are optional; the hosted "Masgent AI" provider needs none.

## 2. Run

```powershell
.venv\Scripts\python.exe -m streamlit run streamlit_app\app.py
```

The app opens at http://localhost:8501. It listens on **localhost only** (see `streamlit_app\.streamlit\config.toml`). Stop it with `Ctrl+C`; finished runs stay in the library. A run that is still working when you stop the app keeps going in its own worker process and is re-attached the next time you start.

## 3. GPU (RTX 5050, optional)

The installed `torch 2.14.0+cpu` is a CPU-only build, so the app currently runs everything on CPU (it works, just slower). The same torch version is published for **CUDA 13.0 (cu130)**, which supports RTX 50-series (Blackwell, sm_120) GPUs. On 2026-11-11 I checked that these exact wheels exist for Windows / Python 3.12 on download.pytorch.org: `torch-2.14.0+cu130`, `torchvision-0.29.0+cu130`, `torchaudio-2.11.0+cu130`. **I did not install them**, because that replaces about 3 GB of your environment.

Before running it, note there is **no torch 2.14 on the cu128 index** (cu128 stops at torch 2.11).

```powershell
uv pip install --python .venv\Scripts\python.exe --reinstall-package torch --reinstall-package torchvision --reinstall-package torchaudio "torch==2.14.0" "torchvision==0.29.0" "torchaudio==2.11.0" --index-url https://download.pytorch.org/whl/cu130
.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

The same reinstall also repairs the missing `functorch` folder that currently breaks Orb-v3 (KNOWN_ISSUES E02). Afterwards open **Setup doctor → Run deep check**. The device selector (`auto`/`cpu`/`cuda`) then offers the GPU. Orb-v3 stays on CPU because Masgent hard-codes it (K16).

**If an ML-potential package will not install natively on Windows**, use WSL2 (Ubuntu): `wsl --install`, then inside WSL `uv venv --python 3.12 .venv && uv pip install -e . && uv pip install -r streamlit_app/requirements.txt` and run the same `streamlit run` command (the browser on Windows can open `http://localhost:8501`). With the current environment SevenNet, CHGNet and MatterSim already work natively on Windows.

## 4. Tests

```powershell
cd streamlit_app
..\.venv\Scripts\python.exe -m pytest                         # everything (≈ 10 min, CPU)
..\.venv\Scripts\python.exe -m pytest -m "not slow"           # fast unit + page tests (< 2 min)
..\.venv\Scripts\python.exe -m pytest -m "not network"        # no model downloads / API calls
```

Markers: `slow` (real tool runs through worker processes), `mlp`, `network` (MatterSim/Orb weights, Materials Project), `mp` (needs `MP_API_KEY` in `streamlit_app\.env`; skipped otherwise), `gpu`. Results: `docs/TEST_REPORT.md`. A 20-minute click-through: `docs/MANUAL_TEST_CHECKLIST.md`.

## 5. Where things are

```
streamlit_app/
  app.py                  entry point (navigation, sidebar search, project selector)
  app_pages/              one script per page
  core/                   engine without Streamlit: registry, runner + worker, storage, parsers, agent …
  ui/                     shared widgets: forms, pickers, results dock, 3D viewer
  assets/                 small example structures / ML data (copied from examples/)
  docs/                   INVENTORY, PLAN, KNOWN_ISSUES, TEST_REPORT, MANUAL_TEST_CHECKLIST
  workspace/              your runs (created on first start, gitignored)
    index.db                  SQLite index (rebuildable: Library → Rebuild index)
    projects/<p>/runs/<YYYYMMDD-HHMMSS>_<tool>_<id>/
        meta.json inputs.json result.json logs.txt env.json manifest.sha256 files/
    projects/<p>/chats/<id>/  AI conversations (conversation.jsonl + session files)
    uploads/ exports/ cache/ .trash/ app.log
```

## 6. Notes and limitations

* **POTCAR**: without a licensed POTCAR library (Settings → POTCAR library) workflow generators write `POTCAR*.spec` placeholders listing the exact files to concatenate on the cluster. Real POTCARs are never displayed or zipped.
* **Masgent bugs/limits** found while building this (all worked around, none patched) are listed with file:line in `docs/KNOWN_ISSUES.md`. Masgent's own CLI and AI mode cannot start on Windows (K06). The app works around that.
* **Materials Project**: the candidate table and *Test connection* need a key; responses are cached under `workspace/cache/mp`.
* **3D viewer** loads 3Dmol.js 2.4.2 from cdnjs (internet needed for the viewer only).
