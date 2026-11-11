# Masgent Streamlit frontend: plan

## Goals

1. Every Masgent tool (36 functions + INCAR-only variant + AI agent; see INVENTORY.md) is reachable in ≤ 2 clicks.
2. Each run streams live in a results dock and is saved to disk, where it can be reopened, compared, re-run and exported after a restart.
3. Masgent stays read-only. All code lives in `streamlit_app/` and imports `masgent` as a library.

## Environment facts that shaped the design

| Fact | Consequence |
|---|---|
| `bullet` needs `termios` → `masgent.cli*` and `masgent.ai_mode.ai_backend` cannot be imported on Windows | Rebuild the agent in `core/agent.py` from `masgent.utils.tools` and `utils.load_system_prompts()`. |
| `MASGENT_SESSION_RUNS_DIR` is frozen into default arguments at import time and read again at call time | Run each tool in its own worker process; pass explicit paths; substitute placeholder defaults. |
| Importing `masgent.utils.tools` takes 2–20 s (cold disk cache) | Keep one pre-warmed spare worker, so a run starts in under 1 s. |
| No POTCAR library on this machine | Worker hook writes `POTCAR*.spec` placeholders (K01). Optional PSP dir in Settings. |
| `torch 2.14.0+cpu` is installed and the GPU is not visible to torch | The GPU path exists and is detected, but tests skip it with a reason. README gives the verified CUDA 13.0 (cu130) wheel command; torch 2.14 is not published for cu128 on Windows. |
| Streamlit 1.66 | Use `st.navigation` + `app_pages/` (not legacy `pages/`), `st.iframe` instead of the deprecated `components.v1.html`, `st.fragment(run_every=…)` for live panels. |

## Architecture

```
streamlit_app/
  app.py                 entry point: sys.path, config, navigation, global search, first-run tour
  .streamlit/config.toml theme (light/dark) + server limits
  core/                  no Streamlit imports (unit-testable)
    paths.py             app/workspace paths, safe_join (path-traversal guard)
    settings.py          settings.json + .env read/write, key masking/redaction
    applog.py            rotating app.log with secret-redaction filter
    capabilities.py      optional-dependency + GPU + key detection (cheap find_spec; deep import in a subprocess on demand)
    catalog.py           hand-written UI metadata per tool (page, title, help, runtime class, field widgets, presets, examples)
    registry.py          discovers tools from masgent.utils.tools (AST, fast, no heavy import) + pairs Pydantic schemas (lazy import); merges catalog
    storage.py           workspace layout, atomic writes, SQLite index, reindex, trash/restore/purge, zip export, zip-slip-safe extraction, uploads (content-hashed)
    runner.py            RunManager: queue, concurrency (GPU jobs serialized), warm worker pool, cancel (process kill), crash detection
    worker.py            worker process entry: warm import, redirect fds to logs.txt, apply hooks, call tool, write result.json
    hooks.py             worker-only instrumentation: POTCAR placeholder, Optuna live trials, training loss stream, CPU-only device
    progress.py          parse live progress from run files (MD log, LBFGS logs, optuna/loss jsonl, tqdm)
    parsers.py           parse tool outputs (elastic txt, predictions, EOS CSV + Birch-Murnaghan fit, NEB, AIMD …)
    structures.py        structure loading/summary/XYZ export for the viewer
    mp_client.py         Materials Project search with disk cache, retries, friendly errors
    agent.py             pydantic-ai agent, provider selection, approval gate, tool wrappers → Runs
    report.py            one-page HTML run report
    diagnostics.py       setup doctor, diagnostic report (no secrets)
  ui/                    Streamlit components shared by pages
    state.py, forms.py (schema → widgets), widgets.py (periodic table, matrix, list, file picker),
    viewer3d.py, results.py (results dock + output viewers), tool_page.py, common.py, search.py
  app_pages/             one script per page (direct scripts, no functions wrapping)
  tests/                 pytest + AppTest
  docs/                  INVENTORY, PLAN, KNOWN_ISSUES, TEST_REPORT, MANUAL_TEST_CHECKLIST
  workspace/             (gitignored) runs, index.db, uploads, exports, caches
```

### Execution model

1. The UI validates the form with the tool's own Pydantic schema (inline errors). **Run** is disabled until the inputs are valid.
2. `RunManager.submit(tool, params, project, …)` does the following:
   - creates `runs/<YYYYMMDD-HHMMSS>_<tool>_<shortid>/`
   - copies input files into `files/inputs/` and rewrites the parameters to point at the copies, so every run is self-contained
   - writes `meta.json` (status `queued`), `inputs.json` and `env.json`, then indexes the run in SQLite
3. A dispatcher thread hands the job to a warm worker process (spawn context). The worker then:
   - sets `MASGENT_SESSION_RUNS_DIR=<run>/files`, sets `cwd` to the same folder, injects keys from `.env` into its own environment only, and closes stdin
   - redirects fd 1/2 to `logs.txt`, applies hooks, sets the seed, calls the tool, and writes `result.json` atomically
   - exits when done
4. The dispatcher records the final status and duration, builds `manifest.sha256` and updates the index.
5. Cancel = terminate the worker process tree. The run is marked `cancelled`, and partial files are kept.
6. On app start, any run still marked `running`/`queued` with no live process is marked `failed` with the reason "interrupted (app restarted)".
7. AI tool calls use the same path with `session_dir=<chat session folder>`, so Masgent's default `POSCAR` chaining works. Files the call creates or changes are copied into the run's `files/`.

### Data model (files are the source of truth; SQLite is a rebuildable index)

* `meta.json`: run_id, tool, tool_title, project, status, created/started/ended, duration_s, label, tags, starred, notes, device, seed, origin (`ui`/`ai`/`rerun`), parent_run, error, pid
* `inputs.json`: validated parameters with secrets removed; paths relative to the run (`files/inputs/POSCAR`)
* `result.json`: the tool's return value; absolute paths rewritten as `{RUN}/files/...`
* `logs.txt`, `files/`, `manifest.sha256`, `env.json` (masgent/python/package versions, device, CUDA, OS)
* `index.db` tables: `runs` (indexed columns from meta), `projects`, `run_tags`, `presets`

### Pages (sidebar)

| Section | Page | Tools |
|---|---|---|
| — | Home | dashboard, quick start, recent runs, capability cards, examples |
| Build | Build structure | #1, #2, #3, #7, #11 + viewer |
| Build | Defects & alloys | #4, #5, #6, #8 |
| Build | Surfaces & interfaces | #9, #10 |
| DFT | VASP inputs | #12, #12b, #13, #14 |
| DFT | DFT workflows | #15–#24 (generate + analyze per workflow) |
| ML | ML potentials | #25 (4 backends × 4 tasks) |
| ML | ML toolkit | #26–#33 |
| AI | AI assistant | #37 (all 36 tools, Plan → Confirm → Execute) |
| Results | Results library | search/filter/reopen/compare/re-run/export/report/trash; #34–#36 file tools |
| Results | Projects | projects, default project, timeline |
| Settings | Settings, Setup doctor, Help & glossary | keys, workspace, device, concurrency, diagnostics, glossary |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Heavy imports slow the app | Pages import lazily; the registry parses `tools.py` with AST (milliseconds); schemas are imported on first validation (~1.6 s, cached). |
| Worker crash or hang | Runs execute in a separate process, so a crash can't kill the app; a heartbeat file and process liveness are checked; Cancel always works. |
| MLP model downloads (Orb, MatterSim) need internet and disk space | Shown on the capability cards; the first run's log streams the download. |
| No POTCAR library | Placeholder `.spec` files plus a clear banner (K01). |
| GPU torch not installed | Device "auto" falls back to CPU; the GPU option is disabled with an install hint. |
| Path traversal / zip-slip | `safe_join` everywhere; zip extraction validates every member, size limits, extension allow-lists. |
| Secrets leak | Keys live only in `.env` and are injected into the worker environment. A redaction filter covers logs and diagnostic reports. `inputs.json` is scrubbed. A test greps the workspace for key values. |

## Test plan

* Unit (pytest): registry (36 tools, schema pairing), form mapping, storage (atomic write, reindex, trash/restore, zip-slip), runner (success, failure, cancel, concurrency, crash), secrets masking, parsers, MP cache (mocked).
* AppTest: every page loads without exception. With optional deps hidden (capability override), pages show "unavailable" cards instead of crashing.
* Coverage: every tool in `tools.py` has a catalog entry and a page, and INVENTORY.md lists it.
* Smoke runs through the real worker with tiny inputs: supercell, conversions, defects, slab, interface, SQS (small), KPOINTS, INCAR, full inputs/workflows (placeholder POTCAR), DFT analysis on synthetic fixtures, CHGNet single point on CPU, pretrained predictors, ML pipeline on a tiny CSV. MP-key, GPU and model-download tests are skipped with a reason when unavailable.
* Restart persistence: create runs → new `Storage` and `RunManager` instance → reindex → runs reopen with inputs, outputs, logs and provenance.
