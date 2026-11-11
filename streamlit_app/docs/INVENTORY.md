# Masgent feature inventory (verified against local source, v1.1.19)

Source of truth: `src/masgent/utils/tools.py` (tool functions decorated with
`@with_metadata`), `src/masgent/utils/schemas.py` (Pydantic v2 validators),
`src/masgent/cli_mode/cli_entries.py` + `cli_run.py` (CLI menu), and
`src/masgent/ai_mode/ai_backend.py` (agent tool list).

## Counts

| What | Count | Notes |
|---|---|---|
| Tool functions in `tools.py` (all `@with_metadata`) | **36** | Counted by AST; enforced by `tests/test_coverage.py` |
| Tools registered on the AI agent (`ai_backend.py`) | **35** | `analyze_vasp_workflow_of_neb` is **not** registered (KNOWN_ISSUES #K07) |
| CLI leaf commands | 37 | 1.1.1–1.1.9 (1.1.4 has 3 sub-commands), 1.2.1–1.2.4, 1.3.1–1.3.5, 1.4.1–1.4.5, 2.1–2.4 (each with 4 tasks), 3.1.1–3.1.3, 3.2, 3.3, 3.4, 3.5.1–3.5.2 |
| UI features (rows below) | **38** | 36 tools + "INCAR only" variant of 1.2.x + AI assistant |

Every tool returns `{status: 'success'|'error', message, ...outputs}`. Exceptions
to "clean return" behaviour: `generate_vasp_poscar` can call `sys.exit(1)` (bad MP
key) or `input()` (missing MP key) through `utils.ask_for_mp_api_key` /
`validate_mp_api_key`; the frontend worker always sets `MP_API_KEY` and converts
`SystemExit` into a failed run.

## `MASGENT_SESSION_RUNS_DIR`

Read **both** at import time and at call time:

* Call time: every tool body does `os.environ.get('MASGENT_SESSION_RUNS_DIR')` to
  decide where to write outputs.
* Import time: default argument values such as
  `poscar_path=f'{os.environ.get("MASGENT_SESSION_RUNS_DIR")}/POSCAR'` and the
  `ToolMetadata.defaults` dicts are frozen when `masgent.utils.tools` is imported.

Isolation strategy: each run executes in its own worker **process**; the worker
imports Masgent with the placeholder `MASGENT_SESSION_RUNS_DIR=__MASGENT_RUNS_DIR__`,
then sets the real per-run directory before calling the tool, and always passes
file paths explicitly (placeholder defaults are substituted with the real run
dir if a caller, e.g. the AI agent, omits them).

## Legend

* Needs: **MP** = Materials Project key, **PSP** = VASP POTCAR library
  (`PMG_VASP_PSP_DIR`; without it the app writes `*.spec` placeholders, see
  KNOWN_ISSUES #K01), **VASP** = outputs from a real VASP run (analysis only, never
  executed locally), **GPU** = optional acceleration, **Net** = internet.
* Runtime class: instant (<2 s), seconds (<30 s), minutes, long (>10 min possible).
* Page = the Streamlit page under `streamlit_app/app_pages/`.

## Inventory

| # | Tool function | CLI | Inputs (field: type = default; validation) | Outputs (files / keys) | Optional deps | Needs | Runtime | UI page |
|---|---|---|---|---|---|---|---|---|
| 1 | `generate_vasp_poscar` | 1.1.1 | formula: str (required; regex `([A-Z][a-z]?)(\d*)`+, valid elements) | `POSCAR`, `POSCAR_<formula>` (most stable), `POSCARs/<formula>/POSCAR_<crystal>_<sg>_<mp-id>`; keys `all_poscars`, `most_stable_poscar` | mp-api | MP, Net | seconds | Build structure → Materials Project |
| 2 | `convert_poscar_coordinates` | 1.1.2 | to_cartesian: bool (required); poscar_path: file = session POSCAR | `convert/POSCAR` | – | – | instant | Build structure → Convert |
| 3 | `convert_structure_format` | 1.1.3 | input_path: file (must parse with ASE); input_format, output_format: `POSCAR\|CIF\|XYZ` (must differ) | `convert/POSCAR` or `convert/<stem>.<ext>` | – | – | instant | Build structure → Convert |
| 4 | `generate_vasp_poscar_with_vacancy_defects` | 1.1.4 → vacancy | original_element: str (must exist in POSCAR); defect_amount: float in (0,1) **or** int ≥1 (≤ count); poscar_path | `defects/vacancies/POSCAR` | – | – | instant | Defects & alloys |
| 5 | `generate_vasp_poscar_with_substitution_defects` | 1.1.4 → substitution | original_element; defect_element: valid symbol; defect_amount (as above); poscar_path | `defects/substitutions/POSCAR` | – | – | instant | Defects & alloys |
| 6 | `generate_vasp_poscar_with_interstitial_defects` | 1.1.4 → interstitial | defect_element: valid symbol; poscar_path | `defects/interstitials/POSCAR_<i>`; key `poscar_paths` | pymatgen-analysis-defects | – | seconds | Defects & alloys |
| 7 | `generate_supercell_from_poscar` | 1.1.5 | scaling_matrix: str `"a b c; d e f; g h i"` (3×3 ints); poscar_path | `supercell/POSCAR` | – | – | instant | Build structure → Supercell |
| 8 | `generate_sqs_from_poscar` | 1.1.6 | target_configurations: dict[site → {element: frac}] (each sums to 1, site must exist); cutoffs: list[float] = [8.0, 4.0] (>0); max_supercell_size: int = 8 (≥1); mc_steps: int = 10000 (≥1000); poscar_path | `sqs/POSCAR`, `sqs/masgent_sqs.log` | icet | – | minutes | Defects & alloys → SQS |
| 9 | `generate_surface_slab_from_poscar` | 1.1.7 | miller_indices: list[int]×3 (required); vacuum_thickness: float = 15.0 (>0); slab_layers: int = 4 (≥1); poscar_path | `surface_slab/POSCAR` | – | – | instant | Surfaces & interfaces |
| 10 | `generate_interface_from_poscars` | 1.1.8 | lower_poscar_path, upper_poscar_path: file; lower_hkl, upper_hkl: list[int]×3; lower/upper_slab_layers: int = 4; slab_vacuum = 15.0; min_area = 50.0; max_area = 500.0 (min<max); interface_gap = 2.0; uv_tolerance = 5.0 %; angle_tolerance = 5.0°; shape_filter: bool = False | `interface_maker/interfaces/*.vasp`, `interface_maker/slabs/*.vasp`, `interface_maker.csv`, `interface_maker.log` | – | – | seconds–minutes | Surfaces & interfaces |
| 11 | `visualize_structure_from_poscar` | 1.1.9 | poscar_path: file | `visualization/OPEN_IN_BROWSER*.html` (3Dmol.js, loads JS from 3Dmol.org) | – | Net (to view HTML) | instant | Build structure → Viewer (plus a native in-app viewer on every structure) |
| 12 | `generate_vasp_inputs_from_poscar` (full) | 1.2.1 | vasp_input_sets: `MPMetalRelaxSet\|MPRelaxSet\|MPStaticSet\|MPNonSCFBandSet\|MPNonSCFDOSSet\|MPMDSet`; poscar_path; only_incar = False | `vasp_inputs/<set>/{INCAR,KPOINTS,POSCAR,POTCAR}` | – | PSP | instant | VASP inputs |
| 12b | `generate_vasp_inputs_from_poscar` (INCAR only) | 1.2.2 | same, only_incar = True | `vasp_inputs/<set>/{INCAR,POSCAR}` | – | – | instant | VASP inputs |
| 13 | `customize_vasp_kpoints_with_accuracy` | 1.2.3 | accuracy_level: `Low(1000)\|Medium(3000)\|High(5000)\|Custom`; custom_kppa: int ≥1 (Custom only); gamma_centered: bool = True; poscar_path | `KPOINTS` | – | – | instant | VASP inputs |
| 14 | `generate_vasp_inputs_hpc_slurm_script` | 1.2.4 | partition = "normal"; nodes ≥1 = 1; ntasks ≥1 = 8; walltime `H?H:MM:SS` = "01:00:00"; jobname = "masgent_job"; command = "srun vasp_std > vasp.out" | `masgent_submit.sh` | – | – | instant | VASP inputs → Slurm |
| 15 | `generate_vasp_workflow_of_convergence_tests` | 1.3.1 | poscar_path; test_type: `kpoints\|encut\|all` (tool default `both` fails validation, K02); kpoint_levels: list[int>0] = [1000..5000]; encut_levels: list[int>0] = [300..700] | `convergence_tests/{kpoint_tests,encut_tests}/…` + `RUN_ME.sh`, `submit_temp.sh`, `*_temp` | – | PSP | seconds | DFT workflows → Convergence |
| 16 | `generate_vasp_workflow_of_eos` | 1.3.2 | poscar_path; scale_factors: list[float>0] = [0.94..1.06] (must be floats) | `eos_calculations/scale_*/POSCAR` + templates, `RUN_ME.sh` | – | PSP | seconds | DFT workflows → EOS |
| 17 | `generate_vasp_workflow_of_elastic_constants` | 1.3.3 | poscar_path | `elastic_constants/NN_strain_*/POSCAR` (13 deformations) + templates | – | PSP | seconds | DFT workflows → Elastic |
| 18 | `generate_vasp_workflow_of_aimd` | 1.3.4 | poscar_path (required in signature); temperatures: list[int>0] = [500..2500]; md_steps ≥1 = 1000; md_timestep >0 = 5.0 fs (schema says 2.0, K05) | `aimd_simulations/T_<T>K/{INCAR,POSCAR}` + templates | – | PSP | seconds | DFT workflows → AIMD |
| 19 | `generate_vasp_workflow_of_neb` | 1.3.5 | initial_poscar_path, final_poscar_path: file; num_images ≥1 = 5 | `neb_calculations/{INCAR,KPOINTS,POTCAR,submit.sh,00..NN/POSCAR}` | – | PSP | seconds | DFT workflows → NEB |
| 20 | `analyze_vasp_workflow_of_convergence_tests` | 1.4.1 | convergence_tests_dir: folder with `{encut,kpoint}_tests/*_N/vasprun.xml` | `encut_tests.png`, `kpoint_tests.png` (written **into the input folder**) | – | VASP | seconds | DFT workflows → Convergence |
| 21 | `analyze_vasp_workflow_of_eos` | 1.4.2 | eos_dir: folder with `scale_*/{POSCAR,vasprun.xml}` | `eos_cal.csv`, `eos_fit.csv`, `eos_curve.png`, `POSCAR_equilibrium` | – | VASP | seconds | DFT workflows → EOS |
| 22 | `analyze_vasp_workflow_of_elastic_constants` | 1.4.3 | elastic_constants_dir: folder with `NN_strain_*/vasprun.xml` | `elastic_constants.txt` (6×6 C_ij, K/G Voigt/Reuss/Hill) | – | VASP | seconds | DFT workflows → Elastic |
| 23 | `analyze_vasp_workflow_of_aimd` | 1.4.4 | aimd_dir: folder with `T_<T>K/{INCAR,OSZICAR,XDATCAR}`; specie: element symbol | per-T `aimd_temperature_energy.{csv,png}`, `aimd_msd.{csv,png}`; `aimd_diffusion_coefficients.csv`, `aimd_activation_energy.txt`, `aimd_arrhenius_plot.png` | – | VASP | seconds | DFT workflows → AIMD |
| 24 | `analyze_vasp_workflow_of_neb` | 1.4.5 | neb_dir: folder with `00..NN` image dirs (OUTCARs) | `neb_data_spline.csv`, `neb_data_points.csv`, `energy_barrier.txt`, `neb_energy_profile.png` | – | VASP | seconds | DFT workflows → NEB |
| 25 | `run_simulation_using_mlps` | 2.1–2.4 × {single, eos, elastic, md} | poscar_path; mlps_type: `SevenNet\|CHGNet\|Orb-v3\|MatterSim` = CHGNet; task_type: `single\|eos\|elastic\|md` = single; fmax >0 = 0.1; max_steps ≥1 = 500; scale_factors: list[float] (eos); temperature ≥0 = 1000 K; md_steps ≥1 = 1000; md_timestep >0 = 5.0 fs | `mlps_simulation/<mlp>/<task>/…`: single → `CONTCAR`, `masgent_mlps_single.log`, energies; eos → `eos_cal.csv`, `eos_fit.csv`, `eos_curve.png`, `POSCAR_equilibrium`; elastic → `elastic_constants.txt`; md → `masgent_mlps_md.{traj,log}`, `md_log.png` | sevenn, chgnet, orb-models, mattersim, torch | GPU (opt.), Net (first model download for Orb/MatterSim) | seconds (single) → long (md) | ML potentials |
| 26 | `analyze_features_for_machine_learning` | 3.1.1 | input_data_path, output_data_path: CSV (same row count) | `machine_learning/ml_feature_analysis/correlation_matrix.{csv,png}`, copies of inputs | – | – | seconds | ML toolkit → Dataset |
| 27 | `reduce_dimensions_for_machine_learning` | 3.1.2 | input_data_path: CSV; n_components ≥1 ≤ n_features = 2 | `ml_dimension_reduction/ml_input_data_reduced.csv`, `pca_reducer.pkl` (saved **unfitted**, K09) | scikit-learn | – | instant | ML toolkit → PCA |
| 28 | `augment_data_for_machine_learning` | 3.1.3 | input_data_path, output_data_path: CSV; num_augmentations ≥1 = 100 | `ml_data_augmentation/ml_{input,output}_data_augmented.csv` | torch | – | minutes | ML toolkit → CVAE |
| 29 | `design_model_for_machine_learning` | 3.2 | input_data_path, output_data_path: CSV; n_trials ≥1 = 100 | `ml_model_design/{best_model.pkl,best_model_params.log,study.pkl,study.csv,study_history.png,study_importance.png,study_slice.png,tested_models/,best_model_losses/}` | optuna, torch | GPU (opt.) | long | ML toolkit → Optuna |
| 30 | `train_model_for_machine_learning` | 3.3 | input/output CSV; best_model_path: .pkl; best_model_params_path: .log; max_epochs ≥1 = 1000; patience ≥1 = 50 | `ml_model_training/{trained_model.pkl,loss.png,performance.log,pred_vs_true_<col>.png}` | torch | GPU (opt.) | minutes | ML toolkit → Train |
| 31 | `retrain_model_for_machine_learning` | 3.4 | input/output CSV; old_model_path: .pkl; old_model_params_path: .log; max_epochs; patience | `ml_model_retraining/…` (same as #30, weights not reset) | torch | GPU (opt.) | minutes | ML toolkit → Train |
| 32 | `model_prediction_for_AlMgSiSc` | 3.5.1 (unreachable in CLI, K03) | Mg: float wt.% ∈ [0, 0.7]; Si: float wt.% ∈ [4, 13]; **must lie on a 0.01 wt.% grid** | `ml_model_prediction/AlMgSiSc_prediction.txt` (UTS, YS, EL; returned key points to wrong filename, K04) | torch | – | instant | ML toolkit → Pretrained |
| 33 | `model_prediction_for_AlCoCrFeNi` | 3.5.2 (unreachable in CLI, K03) | Al, Co, Cr, Fe: float at.% (sum ≤ 100; Ni = remainder) | `ml_model_prediction/AlCoCrFeNi_prediction.txt` (FCC/BCC formation energy, C_ij, K, G) | torch | – | instant | ML toolkit → Pretrained |
| 34 | `list_files` | AI only | – | key `files` (all files under session dir) | – | – | instant | Results library → run → Files (Masgent file tools) and AI assistant |
| 35 | `read_file` | AI only | name: path relative to session dir | key `content` | – | – | instant | Results library → run → Files; AI assistant |
| 36 | `rename_file` | AI only | name, new_name (must stay inside session dir) | copies the file (does not move, K10) | – | – | instant | Results library → run → Files; AI assistant |
| 37 | AI agent (`ai_backend.main`) | AI | provider ∈ Masgent AI (hosted, no key), OpenAI GPT‑5 Nano (`OPENAI_API_KEY`), Anthropic Claude Sonnet 4.5 (`ANTHROPIC_API_KEY`), Google Gemini 2.5 Flash (`GOOGLE_API_KEY`), xAI Grok 4.1 Fast (`GROK_API_KEY`), DeepSeek Chat (`DEEPSEEK_API_KEY`), Alibaba Qwen Flash (`DASHSCOPE_API_KEY`) | conversation log; tool runs | pydantic-ai | LLM key (except Masgent AI), Net | seconds per turn | AI assistant |

CLI-only conveniences with no tool function ("New session", "Help", "Back/Main/Exit")
map to: Projects (new project = new session), Help & glossary, and the sidebar.

## Environment variable names (confirmed from source)

| Variable | Read in | Used for |
|---|---|---|
| `MP_API_KEY` | `tools.generate_vasp_poscar`, `utils.validate_mp_api_key` (also picked up by `mp_api.MPRester`) | Materials Project |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GROK_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY` | `ai_backend.main` | LLM providers |
| `MASGENT_SESSION_RUNS_DIR` | everywhere | output directory |
| `PMG_VASP_PSP_DIR` (pymatgen) | pymatgen input sets | POTCAR library |
