"""UI catalog: hand-written presentation metadata layered on the discovered tools.

Nothing here re-implements Masgent logic. Each ``UiTool`` points at a Masgent
tool function (``func``) and adds: page/section, plain-language help, runtime
class, widget hints per field, presets, fixed parameters, UI-level validators
and parameter transforms (workarounds for KNOWN_ISSUES).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class _NoDefault:
    pass


NODEFAULT = _NoDefault()


@dataclass
class FieldUI:
    kind: str | None = None
    label: str | None = None
    help: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[Any] | None = None
    option_labels: dict[Any, str] | None = None
    default: Any = NODEFAULT
    hidden: bool = False
    visible_if: Callable[[dict], bool] | None = None
    extensions: list[str] | None = None
    structure_field: str | None = None
    placeholder: str | None = None
    folder_kind: str | None = None  # analysis example key for folder pickers


@dataclass
class UiTool:
    id: str
    func: str
    title: str
    page: str
    section: str
    summary: str
    help: str
    runtime: str = "instant"  # instant | seconds | minutes | long
    needs: list[str] = field(default_factory=list)  # capability keys + MP / PSP / VASP_OUTPUTS
    gpu: bool = False
    cli: str = ""
    fixed: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, FieldUI] = field(default_factory=dict)
    presets: dict[str, dict[str, Any]] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    validators: list[Callable[[dict], list[str]]] = field(default_factory=list)
    transform: Callable[[dict], dict] | None = None
    order: list[str] | None = None  # field order override
    auto: bool = False  # generated for a tool missing from the catalog


RUNTIME_LABELS = {
    "instant": ("Instant", "green"),
    "seconds": ("Seconds", "blue"),
    "minutes": ("Minutes", "orange"),
    "long": ("Long (can take hours)", "red"),
}

PAGES = {
    "build": ("Build structure", ":material/deployed_code:"),
    "defects": ("Defects & alloys", ":material/grain:"),
    "surfaces": ("Surfaces & interfaces", ":material/layers:"),
    "vasp": ("VASP inputs", ":material/description:"),
    "workflows": ("DFT workflows", ":material/account_tree:"),
    "mlp": ("ML potentials", ":material/bolt:"),
    "mltoolkit": ("ML toolkit", ":material/model_training:"),
    "library": ("Results library", ":material/folder_open:"),
    "assistant": ("AI assistant", ":material/smart_toy:"),
    "more": ("More tools", ":material/extension:"),
}

S = "structure"
EX_AL = {"poscar_path": "example:fcc Al (mp-134)"}


# --------------------------------------------------------------- validators
def _kpoints_custom(p: dict) -> list[str]:
    if p.get("accuracy_level") == "Custom" and not (p.get("custom_kppa") or 0) >= 1:
        return ["custom_kppa: enter a k-point density (≥ 1) when accuracy is Custom."]
    return []


def _snap_almgsisc(p: dict) -> dict:
    out = dict(p)
    for k in ("Mg", "Si"):
        if out.get(k) is not None:
            out[k] = round(round(float(out[k]) * 100) / 100, 2)  # 0.01 wt.% grid (K20)
    return out


def _hea_sum(p: dict) -> list[str]:
    vals = [float(p.get(k) or 0) for k in ("Al", "Co", "Cr", "Fe")]
    errs = []
    if any(v < 0 for v in vals):
        errs.append("Concentrations cannot be negative.")
    if sum(vals) > 100 + 1e-9:
        errs.append(f"Al + Co + Cr + Fe = {sum(vals):.2f} at.% exceeds 100 % (Ni is the remainder).")
    return errs


def _conv_transform(p: dict) -> dict:
    out = dict(p)
    if out.get("test_type") == "both":
        out["test_type"] = "all"  # K02: schema accepts 'all', tool default 'both' fails
    return out


def _floats(*keys: str) -> Callable[[dict], dict]:
    def t(p: dict) -> dict:
        out = dict(p)
        for k in keys:
            if isinstance(out.get(k), (list, tuple)):
                out[k] = [float(x) for x in out[k]]
        return out
    return t


def _folder_has(kind: str, patterns: list[str]) -> Callable[[dict], list[str]]:
    key = {"convergence": "convergence_tests_dir", "eos": "eos_dir", "elastic": "elastic_constants_dir",
           "aimd": "aimd_dir", "neb": "neb_dir"}[kind]

    def v(p: dict) -> list[str]:
        d = p.get(key)
        if not d:
            return [f"{key}: choose a folder (upload a ZIP, pick a run, or load the example)."]
        root = Path(str(d))
        if not root.is_dir():
            return [f"{key}: folder not found."]
        if not any(any(root.glob(pat)) for pat in patterns):
            return [f"{key}: no expected VASP outputs found (looked for {', '.join(patterns)})."]
        return []
    return v


def _defect_amount(p: dict) -> list[str]:
    v = p.get("defect_amount")
    if v is None:
        return ["defect_amount: required."]
    return []


# ------------------------------------------------------------------ catalog
CATALOG: list[UiTool] = [
    # ============================================================ BUILD
    UiTool(
        id="generate_vasp_poscar", func="generate_vasp_poscar", title="Structure from Materials Project",
        page="build", section="Materials Project", cli="1.1.1", runtime="seconds", needs=["mp_api", "MP"],
        summary="Download every known crystal structure for a chemical formula.",
        help=("**What it does:** searches the Materials Project database for your formula (e.g. `Al`, `NaCl`, "
              "`LaCoO3`), saves *all* polymorphs as POSCAR files and marks the most stable one "
              "(lowest energy above the convex hull) as `POSCAR`.\n\n**When to use it:** the start of almost every "
              "project, to get a reliable starting structure instead of building one by hand. Needs a free MP API key."),
        fields={"formula": FieldUI(label="Chemical formula", placeholder="e.g. Al, NaCl, LaCoO3",
                                   help="Element symbols with optional integer counts, case-sensitive (Fe2O3, not fe2o3).")},
        presets={"Aluminium (Al)": {"formula": "Al"}, "Rock salt (NaCl)": {"formula": "NaCl"}},
        keywords=["materials project", "mp", "download", "poscar", "polymorph", "formula", "generate"],
    ),
    UiTool(
        id="convert_structure_format", func="convert_structure_format", title="Convert file format",
        page="build", section="Convert", cli="1.1.3",
        summary="Convert between POSCAR, CIF and XYZ.",
        help=("**What it does:** reads a structure file and writes it in another format (POSCAR ↔ CIF ↔ XYZ) using ASE.\n\n"
              "**When to use it:** you found a CIF in a paper or database and need a POSCAR for VASP, or you want an XYZ for a molecular viewer."),
        fields={
            "input_path": FieldUI(kind="structure", label="Input structure file", extensions=["vasp", "poscar", "cif", "xyz", "contcar", ""]),
            "input_format": FieldUI(kind="segmented", label="Input format", options=["POSCAR", "CIF", "XYZ"], default="POSCAR"),
            "output_format": FieldUI(kind="segmented", label="Output format", options=["POSCAR", "CIF", "XYZ"], default="CIF"),
        },
        presets={"fcc Al POSCAR → CIF": {"input_path": "example:fcc Al (mp-134)", "input_format": "POSCAR", "output_format": "CIF"}},
        keywords=["cif", "xyz", "format", "convert", "export"],
    ),
    UiTool(
        id="convert_poscar_coordinates", func="convert_poscar_coordinates", title="Direct ↔ Cartesian",
        page="build", section="Convert", cli="1.1.2",
        summary="Rewrite a POSCAR in fractional (Direct) or Cartesian coordinates.",
        help=("**What it does:** keeps the structure identical but changes how atom positions are written: *Direct* = fractions of "
              "the lattice vectors, *Cartesian* = Ångström.\n\n**When to use it:** some scripts/tools expect one convention, or you want to read off real-space distances."),
        fields={"poscar_path": FieldUI(kind=S, label="Input POSCAR"),
                "to_cartesian": FieldUI(kind="toggle", label="Convert to Cartesian (off = Direct)", default=True)},
        presets={"fcc Al → Cartesian": dict(EX_AL, to_cartesian=True)},
        order=["poscar_path", "to_cartesian"],
        keywords=["cartesian", "direct", "fractional", "coordinates"],
    ),
    UiTool(
        id="generate_supercell_from_poscar", func="generate_supercell_from_poscar", title="Supercell",
        page="build", section="Supercell", cli="1.1.5",
        summary="Repeat the unit cell with a 3×3 integer scaling matrix.",
        help=("**What it does:** builds a larger periodic cell. A diagonal matrix `2 0 0; 0 2 0; 0 0 2` makes a 2×2×2 supercell "
              "(8× the atoms); off-diagonal entries create sheared/rotated cells.\n\n**When to use it:** before adding defects "
              "(so they are far apart), for MD, or for SQS/alloy models."),
        fields={"poscar_path": FieldUI(kind=S, label="Input structure"),
                "scaling_matrix": FieldUI(kind="matrix3", label="Scaling matrix", default="2 0 0; 0 2 0; 0 0 2")},
        presets={"2×2×2 supercell of fcc Al": dict(EX_AL, scaling_matrix="2 0 0; 0 2 0; 0 0 2"),
                 "3×3×3 supercell of fcc Al": dict(EX_AL, scaling_matrix="3 0 0; 0 3 0; 0 0 3")},
        order=["poscar_path", "scaling_matrix"],
        keywords=["supercell", "repeat", "scale", "expand"],
    ),
    UiTool(
        id="visualize_structure_from_poscar", func="visualize_structure_from_poscar", title="Masgent HTML viewer",
        page="build", section="Viewer", cli="1.1.9",
        summary="Export Masgent's standalone 3Dmol.js HTML viewer for a structure.",
        help=("**What it does:** writes `OPEN_IN_BROWSER.html`, an interactive 3D view you can open in any browser or e-mail to a colleague.\n\n"
              "**When to use it:** to share a structure. (Every structure in this app also has a built-in 3D viewer.)"),
        fields={"poscar_path": FieldUI(kind=S, label="Structure")},
        presets={"fcc Al": EX_AL},
        keywords=["visualize", "view", "3d", "html", "3dmol"],
    ),
    # ============================================================ DEFECTS
    UiTool(
        id="generate_vasp_poscar_with_vacancy_defects", func="generate_vasp_poscar_with_vacancy_defects", title="Vacancies",
        page="defects", section="Point defects", cli="1.1.4",
        summary="Randomly remove atoms of one element.",
        help=("**What it does:** deletes a fraction (0–1) or a number of atoms of the chosen element at random positions.\n\n"
              "**When to use it:** to model vacancy defects (e.g. oxygen vacancies). Use a supercell first so that defects are dilute. "
              "Random choice is reproducible with the run seed (Settings)."),
        fields={"poscar_path": FieldUI(kind=S, label="Host structure"),
                "original_element": FieldUI(kind="element_in_structure", label="Element to remove", structure_field="poscar_path"),
                "defect_amount": FieldUI(kind="defect_amount", label="How many")},
        presets={"1 vacancy in fcc Al": dict(EX_AL, original_element="Al", defect_amount=1)},
        order=["poscar_path", "original_element", "defect_amount"], validators=[_defect_amount],
        keywords=["vacancy", "defect", "remove atom"],
    ),
    UiTool(
        id="generate_vasp_poscar_with_substitution_defects", func="generate_vasp_poscar_with_substitution_defects", title="Substitutions",
        page="defects", section="Point defects", cli="1.1.4",
        summary="Randomly replace atoms of one element by another.",
        help=("**What it does:** swaps a fraction or number of atoms of one element for another element (e.g. Al → Mg).\n\n"
              "**When to use it:** dopants, solid solutions, quick alloy models (for statistically random alloys use SQS)."),
        fields={"poscar_path": FieldUI(kind=S, label="Host structure"),
                "original_element": FieldUI(kind="element_in_structure", label="Element to replace", structure_field="poscar_path"),
                "defect_element": FieldUI(kind="element", label="New element"),
                "defect_amount": FieldUI(kind="defect_amount", label="How many")},
        presets={"1 Mg in fcc Al": dict(EX_AL, original_element="Al", defect_element="Mg", defect_amount=1)},
        order=["poscar_path", "original_element", "defect_element", "defect_amount"], validators=[_defect_amount],
        keywords=["substitution", "dopant", "doping", "replace", "defect"],
    ),
    UiTool(
        id="generate_vasp_poscar_with_interstitial_defects", func="generate_vasp_poscar_with_interstitial_defects", title="Interstitials (Voronoi)",
        page="defects", section="Point defects", cli="1.1.4", runtime="seconds", needs=["defects"],
        summary="Insert an extra atom at each symmetry-distinct Voronoi void.",
        help=("**What it does:** finds empty spaces (Voronoi interstitial sites) in the crystal and writes one POSCAR per distinct site "
              "with the new atom inserted.\n\n**When to use it:** hydrogen/lithium/carbon in metals, self-interstitials, diffusion studies."),
        fields={"poscar_path": FieldUI(kind=S, label="Host structure"),
                "defect_element": FieldUI(kind="element", label="Element to insert", default="H")},
        presets={"H interstitial in fcc Al": dict(EX_AL, defect_element="H")},
        order=["poscar_path", "defect_element"],
        keywords=["interstitial", "voronoi", "insert", "defect"],
    ),
    UiTool(
        id="generate_sqs_from_poscar", func="generate_sqs_from_poscar", title="Special quasirandom structure (SQS)",
        page="defects", section="Alloys (SQS)", cli="1.1.6", runtime="minutes", needs=["icet"],
        summary="Build the most 'random-looking' alloy supercell for target concentrations.",
        help=("**What it does:** uses *icet* Monte Carlo to arrange atoms on each chosen site (sublattice) so that short-range "
              "correlations mimic a perfectly random alloy at your concentrations.\n\n**When to use it:** DFT/MLP of random solid "
              "solutions and high-entropy alloys. Concentrations on each site must sum to 1 and be representable in the supercell "
              "(e.g. 0.25 steps for a 4-site cell × size)."),
        fields={"poscar_path": FieldUI(kind=S, label="Parent structure"),
                "target_configurations": FieldUI(kind="sqs", label="Target concentrations per site", structure_field="poscar_path"),
                "cutoffs": FieldUI(kind="list_float", label="Cluster cutoffs (Å): pairs, triplets, …", default=[8.0, 4.0]),
                "max_supercell_size": FieldUI(kind="int", label="Max supercell size (× primitive cells)", min=1, max=64, default=8),
                "mc_steps": FieldUI(kind="int", label="Monte Carlo steps", min=1000, max=1_000_000, step=1000, default=10000)},
        presets={"Al₀.₅Cu₀.₅ on fcc Al (fast)": dict(EX_AL, target_configurations={"Al": {"Al": 0.5, "Cu": 0.5}},
                                                     cutoffs=[6.0], max_supercell_size=4, mc_steps=1000)},
        order=["poscar_path", "target_configurations", "cutoffs", "max_supercell_size", "mc_steps"],
        keywords=["sqs", "alloy", "random", "icet", "high entropy", "hea", "quasirandom"],
    ),
    # ============================================================ SURFACES
    UiTool(
        id="generate_surface_slab_from_poscar", func="generate_surface_slab_from_poscar", title="Surface slab",
        page="surfaces", section="Slab", cli="1.1.7",
        summary="Cut a slab along Miller indices with vacuum.",
        help=("**What it does:** cleaves the bulk crystal along the (h k l) plane, keeps *N* atomic layers and adds vacuum so the "
              "slab does not interact with its periodic image.\n\n**When to use it:** surface energies, adsorption, catalysis, "
              "work functions. 10–20 Å vacuum is typical."),
        fields={"poscar_path": FieldUI(kind=S, label="Bulk structure"),
                "miller_indices": FieldUI(kind="miller", label="Miller indices (h k l)", default=[1, 1, 1]),
                "vacuum_thickness": FieldUI(kind="float", label="Vacuum (Å)", min=0.5, max=100.0, step=0.5),
                "slab_layers": FieldUI(kind="int", label="Atomic layers", min=1, max=50)},
        presets={"Al(111), 4 layers, 15 Å": dict(EX_AL, miller_indices=[1, 1, 1], vacuum_thickness=15.0, slab_layers=4)},
        order=["poscar_path", "miller_indices", "slab_layers", "vacuum_thickness"],
        keywords=["slab", "surface", "miller", "vacuum", "cleave"],
    ),
    UiTool(
        id="generate_interface_from_poscars", func="generate_interface_from_poscars", title="Interface builder",
        page="surfaces", section="Interface", cli="1.1.8", runtime="seconds",
        summary="Lattice-match two slabs and stack them into interfaces.",
        help=("**What it does:** Masgent's interface maker cuts both crystals along the given planes, searches in-plane supercells "
              "whose lattice vectors and angles match within your tolerances and area window, then stacks them with a gap.\n\n"
              "**When to use it:** heterostructures, coatings, thin films, grain-boundary-like models. Results are ranked in "
              "`interface_maker.csv` (misfits per interface)."),
        fields={"lower_poscar_path": FieldUI(kind=S, label="Lower (substrate) structure"),
                "upper_poscar_path": FieldUI(kind=S, label="Upper (film) structure"),
                "lower_hkl": FieldUI(kind="miller", label="Lower plane (h k l)", default=[0, 0, 1]),
                "upper_hkl": FieldUI(kind="miller", label="Upper plane (h k l)", default=[0, 0, 1]),
                "uv_tolerance": FieldUI(kind="float", label="Lattice-vector tolerance (%)", min=0.0, max=50.0),
                "angle_tolerance": FieldUI(kind="float", label="Angle tolerance (°)", min=0.0, max=45.0),
                "shape_filter": FieldUI(kind="toggle", label="Keep only the most square-like interface")},
        presets={"LaCoO3(001) / La2NiO4(001)": {"lower_poscar_path": "example:Perovskite LaCoO3", "upper_poscar_path": "example:La2NiO4",
                                                 "lower_hkl": [0, 0, 1], "upper_hkl": [0, 0, 1], "max_area": 200.0}},
        keywords=["interface", "heterostructure", "lattice match", "film", "substrate"],
    ),
    # ============================================================ VASP INPUTS
    UiTool(
        id="generate_vasp_inputs_from_poscar", func="generate_vasp_inputs_from_poscar", title="Full VASP input set",
        page="vasp", section="Input files", cli="1.2.1", needs=["PSP"],
        summary="INCAR, KPOINTS, POSCAR and POTCAR from a Materials Project input set.",
        help=("**What it does:** applies a pymatgen/Materials Project input set to your structure and writes all four VASP inputs.\n\n"
              "* **MPMetalRelaxSet** – relax metals · **MPRelaxSet** – relax anything · **MPStaticSet** – single-point energy · "
              "**MPNonSCFBandSet / DOSSet** – band structure / density of states after a static run · **MPMDSet** – molecular dynamics.\n\n"
              "POTCARs are licensed: without a POTCAR library configured (Settings) the app writes `POTCAR.spec` listing the files to assemble on your cluster."),
        fixed={"only_incar": False},
        fields={"poscar_path": FieldUI(kind=S, label="Structure"),
                "vasp_input_sets": FieldUI(kind="select", label="Input set", default="MPRelaxSet")},
        presets={"Relax fcc Al (MPMetalRelaxSet)": dict(EX_AL, vasp_input_sets="MPMetalRelaxSet")},
        order=["poscar_path", "vasp_input_sets"],
        keywords=["incar", "kpoints", "potcar", "vasp", "input set", "relax", "static"],
    ),
    UiTool(
        id="generate_vasp_inputs_from_poscar:incar", func="generate_vasp_inputs_from_poscar", title="INCAR template only",
        page="vasp", section="Input files", cli="1.2.2",
        summary="Only the INCAR (and POSCAR) for an input set: no POTCAR needed.",
        help="**What it does:** the same Materials Project input sets, but only writes the INCAR (plus POSCAR). Handy when you manage KPOINTS/POTCAR yourself.",
        fixed={"only_incar": True},
        fields={"poscar_path": FieldUI(kind=S, label="Structure"),
                "vasp_input_sets": FieldUI(kind="select", label="Input set", default="MPStaticSet")},
        presets={"Static INCAR for fcc Al": dict(EX_AL, vasp_input_sets="MPStaticSet")},
        order=["poscar_path", "vasp_input_sets"],
        keywords=["incar", "template"],
    ),
    UiTool(
        id="customize_vasp_kpoints_with_accuracy", func="customize_vasp_kpoints_with_accuracy", title="KPOINTS by accuracy",
        page="vasp", section="KPOINTS", cli="1.2.3",
        summary="Automatic k-point mesh from a density (k-points per reciprocal atom).",
        help=("**What it does:** builds a Γ-centred (or Monkhorst-Pack) mesh with Low = 1000, Medium = 3000, High = 5000 k-points "
              "per reciprocal atom, or your custom density.\n\n**When to use it:** after a convergence test told you which density is enough. "
              "Metals need denser meshes than insulators."),
        fields={"poscar_path": FieldUI(kind=S, label="Structure"),
                "accuracy_level": FieldUI(kind="segmented", label="Accuracy", default="Medium"),
                "custom_kppa": FieldUI(kind="int", label="Custom k-points per reciprocal atom", min=1, max=100000, default=3000,
                                       visible_if=lambda p: p.get("accuracy_level") == "Custom"),
                "gamma_centered": FieldUI(kind="toggle", label="Γ-centred mesh (off = Monkhorst-Pack)")},
        presets={"Medium for fcc Al": dict(EX_AL, accuracy_level="Medium")},
        order=["poscar_path", "accuracy_level", "custom_kppa", "gamma_centered"], validators=[_kpoints_custom],
        keywords=["kpoints", "k-points", "mesh", "monkhorst", "gamma", "kppa"],
    ),
    UiTool(
        id="generate_vasp_inputs_hpc_slurm_script", func="generate_vasp_inputs_hpc_slurm_script", title="HPC Slurm script",
        page="vasp", section="Slurm", cli="1.2.4",
        summary="A ready-to-edit `sbatch` submission script.",
        help=("**What it does:** writes `masgent_submit.sh` with your partition, node/task counts, wall time and run command.\n\n"
              "**When to use it:** to submit VASP jobs on a Slurm cluster (`sbatch masgent_submit.sh`). Ask your HPC admins for the right partition and modules."),
        fields={"partition": FieldUI(label="Partition / queue"), "nodes": FieldUI(kind="int", min=1, max=1000),
                "ntasks": FieldUI(kind="int", label="Tasks (MPI ranks)", min=1, max=100000),
                "walltime": FieldUI(label="Wall time (HH:MM:SS)", placeholder="01:00:00"),
                "jobname": FieldUI(label="Job name"),
                "command": FieldUI(kind="textarea", label="Commands (modules + run line)",
                                   help="Add `module load …` lines above the run command if your cluster needs them.")},
        presets={"Default 1 node × 8 tasks": {}, "4 nodes × 128 tasks, 24 h": {"nodes": 4, "ntasks": 512, "walltime": "24:00:00",
                 "command": "module load vasp\nsrun vasp_std > vasp.out"}},
        keywords=["slurm", "hpc", "sbatch", "cluster", "job", "submit"],
    ),
    # ============================================================ DFT WORKFLOWS: generate
    UiTool(
        id="generate_vasp_workflow_of_convergence_tests", func="generate_vasp_workflow_of_convergence_tests", title="Convergence tests: generate",
        page="workflows", section="Convergence", cli="1.3.1", runtime="seconds", needs=["PSP"],
        summary="Folders for ENCUT and k-point convergence studies.",
        help=("**What it does:** creates one VASP static calculation per plane-wave cutoff (ENCUT) and/or per k-point density, plus "
              "`RUN_ME.sh` that copies templates and submits all jobs.\n\n**When to use it:** first thing for a new material: find the "
              "cheapest settings where the energy changes by < 1 meV/atom."),
        fields={"poscar_path": FieldUI(kind=S, label="Structure"),
                "test_type": FieldUI(kind="segmented", label="Test", options=["both", "kpoints", "encut"], default="both",
                                     option_labels={"both": "Both", "kpoints": "k-points", "encut": "ENCUT"}),
                "kpoint_levels": FieldUI(kind="list_int", label="k-point densities (per reciprocal atom)",
                                         visible_if=lambda p: p.get("test_type") in ("both", "kpoints", "all")),
                "encut_levels": FieldUI(kind="list_int", label="ENCUT values (eV)",
                                        visible_if=lambda p: p.get("test_type") in ("both", "encut", "all"))},
        presets={"fcc Al, both tests": dict(EX_AL, test_type="both")},
        transform=_conv_transform, order=["poscar_path", "test_type", "kpoint_levels", "encut_levels"],
        keywords=["convergence", "encut", "cutoff", "kpoints"],
    ),
    UiTool(
        id="generate_vasp_workflow_of_eos", func="generate_vasp_workflow_of_eos", title="Equation of state: generate",
        page="workflows", section="EOS", cli="1.3.2", runtime="seconds", needs=["PSP"],
        summary="Scaled-volume calculations for an E(V) curve.",
        help=("**What it does:** scales the cell volume by each factor and prepares a static calculation per volume.\n\n"
              "**When to use it:** equilibrium lattice constant, bulk modulus B₀ and its pressure derivative B₀'."),
        fields={"poscar_path": FieldUI(kind=S, label="Structure"),
                "scale_factors": FieldUI(kind="list_float", label="Volume scale factors")},
        presets={"fcc Al, ±6 %": EX_AL}, transform=_floats("scale_factors"),
        keywords=["eos", "equation of state", "bulk modulus", "volume", "lattice constant"],
    ),
    UiTool(
        id="generate_vasp_workflow_of_elastic_constants", func="generate_vasp_workflow_of_elastic_constants", title="Elastic constants: generate",
        page="workflows", section="Elastic", cli="1.3.3", runtime="seconds", needs=["PSP"],
        summary="13 strained cells (±1 % normal, ±0.5 % shear) for the stress-strain method.",
        help=("**What it does:** applies small normal and shear strains and prepares one static calculation each.\n\n"
              "**When to use it:** full 6×6 elastic tensor, bulk/shear/Young's moduli, mechanical stability. Relax the structure first."),
        fields={"poscar_path": FieldUI(kind=S, label="Relaxed structure")},
        presets={"fcc Al": EX_AL},
        keywords=["elastic", "stiffness", "cij", "modulus", "stress strain"],
    ),
    UiTool(
        id="generate_vasp_workflow_of_aimd", func="generate_vasp_workflow_of_aimd", title="AIMD: generate",
        page="workflows", section="AIMD", cli="1.3.4", runtime="seconds", needs=["PSP"],
        summary="NVT ab-initio molecular dynamics at several temperatures.",
        help=("**What it does:** one MD run per temperature (MPMDSet). Analysis later gives temperature/energy traces, mean squared "
              "displacement, diffusion coefficients and an Arrhenius activation energy.\n\n**When to use it:** ionic conductors, "
              "diffusion, melting, finite-temperature stability. Use a supercell (≥ 50 atoms)."),
        fields={"poscar_path": FieldUI(kind=S, label="Structure (supercell)"),
                "temperatures": FieldUI(kind="list_int", label="Temperatures (K)", default=[500, 1000, 1500, 2000, 2500]),
                "md_steps": FieldUI(kind="int", label="MD steps", min=1, max=10_000_000, default=1000),
                "md_timestep": FieldUI(kind="float", label="Time step (fs)", min=0.1, max=10.0, step=0.5, default=5.0)},
        presets={"fcc Al, 3 temperatures": dict(EX_AL, temperatures=[600, 900, 1200], md_steps=1000, md_timestep=2.0)},
        order=["poscar_path", "temperatures", "md_steps", "md_timestep"],
        keywords=["aimd", "molecular dynamics", "diffusion", "md", "temperature"],
    ),
    UiTool(
        id="generate_vasp_workflow_of_neb", func="generate_vasp_workflow_of_neb", title="NEB: generate",
        page="workflows", section="NEB", cli="1.3.5", runtime="seconds", needs=["PSP"],
        summary="Climbing-image nudged elastic band between two structures.",
        help=("**What it does:** linearly interpolates images between an initial and a final structure (same atoms, same order) and "
              "prepares a climbing-image NEB calculation.\n\n**When to use it:** migration barriers (diffusion hops), reaction barriers."),
        fields={"initial_poscar_path": FieldUI(kind=S, label="Initial state"),
                "final_poscar_path": FieldUI(kind=S, label="Final state"),
                "num_images": FieldUI(kind="int", label="Intermediate images", min=1, max=32)},
        presets={"La-Ni-O vacancy hop (example)": {"initial_poscar_path": "example:NEB initial image (La-Ni-O)",
                                                    "final_poscar_path": "example:NEB final image (La-Ni-O)", "num_images": 5}},
        keywords=["neb", "barrier", "migration", "transition state", "nudged elastic band"],
    ),
    # ============================================================ DFT WORKFLOWS: analyze
    UiTool(
        id="analyze_vasp_workflow_of_convergence_tests", func="analyze_vasp_workflow_of_convergence_tests", title="Convergence tests: analyze",
        page="workflows", section="Convergence", cli="1.4.1", runtime="seconds", needs=["VASP_OUTPUTS"],
        summary="Energy-difference curves vs ENCUT and k-points.",
        help="Upload the finished `convergence_tests` folder (as a ZIP). Each step's energy change is plotted against the 1 meV/atom threshold.",
        fields={"convergence_tests_dir": FieldUI(kind="folder", label="Convergence-test results folder", folder_kind="convergence")},
        presets={"Masgent example (Al)": {"convergence_tests_dir": "example_folder:convergence"}},
        validators=[_folder_has("convergence", ["*_tests/*/vasprun.xml"])],
        keywords=["convergence", "analysis", "encut", "kpoints"],
    ),
    UiTool(
        id="analyze_vasp_workflow_of_eos", func="analyze_vasp_workflow_of_eos", title="Equation of state: analyze",
        page="workflows", section="EOS", cli="1.4.2", runtime="seconds", needs=["VASP_OUTPUTS"],
        summary="E(V) fit, equilibrium volume and POSCAR_equilibrium.",
        help="Upload the finished `eos_calculations` folder. Masgent fits E(V) and writes the equilibrium structure; this app additionally reports Birch–Murnaghan V₀, B₀ and B₀'.",
        fields={"eos_dir": FieldUI(kind="folder", label="EOS results folder", folder_kind="eos")},
        presets={"Masgent example": {"eos_dir": "example_folder:eos"}},
        validators=[_folder_has("eos", ["scale_*/vasprun.xml"])],
        keywords=["eos", "analysis", "bulk modulus"],
    ),
    UiTool(
        id="analyze_vasp_workflow_of_elastic_constants", func="analyze_vasp_workflow_of_elastic_constants", title="Elastic constants: analyze",
        page="workflows", section="Elastic", cli="1.4.3", runtime="seconds", needs=["VASP_OUTPUTS"],
        summary="6×6 elastic tensor and Voigt/Reuss/Hill moduli.",
        help="Upload the finished `elastic_constants` folder (13 strain sub-folders with vasprun.xml).",
        fields={"elastic_constants_dir": FieldUI(kind="folder", label="Elastic results folder", folder_kind="elastic")},
        presets={"Masgent example": {"elastic_constants_dir": "example_folder:elastic"}},
        validators=[_folder_has("elastic", ["*_strain_*/vasprun.xml"])],
        keywords=["elastic", "analysis", "cij", "modulus"],
    ),
    UiTool(
        id="analyze_vasp_workflow_of_aimd", func="analyze_vasp_workflow_of_aimd", title="AIMD: analyze",
        page="workflows", section="AIMD", cli="1.4.4", runtime="seconds", needs=["VASP_OUTPUTS"],
        summary="T/E traces, MSD, diffusion coefficients, activation energy.",
        help="Upload the finished `aimd_simulations` folder (T_xxxK sub-folders with INCAR, OSZICAR, XDATCAR) and choose the diffusing element.",
        fields={"aimd_dir": FieldUI(kind="folder", label="AIMD results folder", folder_kind="aimd"),
                "specie": FieldUI(kind="element", label="Diffusing element", default="O")},
        presets={"Masgent example (O diffusion)": {"aimd_dir": "example_folder:aimd", "specie": "O"}},
        validators=[_folder_has("aimd", ["T_*K/OSZICAR"])],
        keywords=["aimd", "analysis", "msd", "diffusion", "arrhenius"],
    ),
    UiTool(
        id="analyze_vasp_workflow_of_neb", func="analyze_vasp_workflow_of_neb", title="NEB: analyze",
        page="workflows", section="NEB", cli="1.4.5", runtime="seconds", needs=["VASP_OUTPUTS"],
        summary="Minimum-energy path and barrier.",
        help="Upload the finished `neb_calculations` folder (00 … NN image folders with OUTCAR).",
        fields={"neb_dir": FieldUI(kind="folder", label="NEB results folder", folder_kind="neb")},
        presets={"Masgent example": {"neb_dir": "example_folder:neb"}},
        validators=[_folder_has("neb", ["0*/OUTCAR", "[0-9][0-9]/OUTCAR"])],
        keywords=["neb", "analysis", "barrier"],
    ),
    # ============================================================ MLP
    UiTool(
        id="run_simulation_using_mlps", func="run_simulation_using_mlps", title="ML potential simulation",
        page="mlp", section="Simulation", cli="2.1–2.4", runtime="seconds", needs=["torch"], gpu=True,
        summary="Near-DFT accuracy in seconds: relax, EOS, elastic constants or MD with a universal ML potential.",
        help=("**What it does:** replaces DFT with a pre-trained universal machine-learning interatomic potential "
              "(SevenNet, CHGNet, Orb-v3 or MatterSim) inside ASE.\n\n"
              "* **single** – relax atom positions (LBFGS) and report the energy · **eos** – E(V) curve · "
              "**elastic** – elastic tensor · **md** – NVT molecular dynamics (Nosé–Hoover).\n\n"
              "**When to use it:** fast screening before DFT, large cells, long MD. Results are approximate; validate key numbers with DFT."),
        fields={"poscar_path": FieldUI(kind=S, label="Structure"),
                "mlps_type": FieldUI(kind="segmented", label="Potential", default="CHGNet"),
                "task_type": FieldUI(kind="segmented", label="Task", default="single",
                                     option_labels={"single": "Single point / relax", "eos": "EOS", "elastic": "Elastic", "md": "MD"}),
                "fmax": FieldUI(kind="float", label="Force tolerance fmax (eV/Å)", min=0.001, max=10.0, step=0.01,
                                visible_if=lambda p: p.get("task_type") != "md"),
                "max_steps": FieldUI(kind="int", label="Max optimiser steps", min=1, max=100000,
                                     visible_if=lambda p: p.get("task_type") != "md"),
                "scale_factors": FieldUI(kind="list_float", label="Volume scale factors",
                                         visible_if=lambda p: p.get("task_type") == "eos"),
                "temperature": FieldUI(kind="int", label="Temperature (K)", min=0, max=10000,
                                       visible_if=lambda p: p.get("task_type") == "md"),
                "md_steps": FieldUI(kind="int", label="MD steps", min=1, max=10_000_000,
                                    visible_if=lambda p: p.get("task_type") == "md"),
                "md_timestep": FieldUI(kind="float", label="Time step (fs)", min=0.1, max=10.0, step=0.5,
                                       visible_if=lambda p: p.get("task_type") == "md")},
        presets={"CHGNet single point, fcc Al (CPU, < 1 min)": dict(EX_AL, mlps_type="CHGNet", task_type="single"),
                 "CHGNet EOS, fcc Al": dict(EX_AL, mlps_type="CHGNet", task_type="eos"),
                 "CHGNet MD 300 steps @ 600 K, fcc Al": dict(EX_AL, mlps_type="CHGNet", task_type="md", temperature=600, md_steps=300, md_timestep=2.0)},
        transform=_floats("scale_factors"),
        order=["poscar_path", "mlps_type", "task_type", "fmax", "max_steps", "scale_factors", "temperature", "md_steps", "md_timestep"],
        keywords=["mlp", "machine learning potential", "chgnet", "sevenn", "sevennet", "orb", "mattersim", "md", "relax", "single point"],
    ),
    # ============================================================ ML TOOLKIT
    UiTool(
        id="analyze_features_for_machine_learning", func="analyze_features_for_machine_learning", title="Feature correlation",
        page="mltoolkit", section="Dataset", cli="3.1.1", runtime="seconds",
        summary="Correlation matrix between all input features and targets.",
        help=("**What it does:** joins your input (features) and output (targets) CSVs column-wise and computes the Pearson correlation matrix.\n\n"
              "**When to use it:** to spot redundant features and which features drive each target. Both CSVs need the same number of rows."),
        fields={"input_data_path": FieldUI(kind="csv", label="Input features CSV"),
                "output_data_path": FieldUI(kind="csv", label="Output targets CSV")},
        presets={"AlCoCrFeNi example": {"input_data_path": "example_ml:AlCoCrFeNi inputs (compositions)",
                                        "output_data_path": "example_ml:AlCoCrFeNi outputs (FCC properties)"}},
        keywords=["correlation", "feature", "dataset", "heatmap"],
    ),
    UiTool(
        id="reduce_dimensions_for_machine_learning", func="reduce_dimensions_for_machine_learning", title="PCA (dimensionality reduction)",
        page="mltoolkit", section="PCA", cli="3.1.2",
        summary="Project features onto the first principal components.",
        help="**What it does:** principal component analysis keeps the directions of largest variance.\n\n**When to use it:** many correlated features, few samples. (Note K09: the saved `pca_reducer.pkl` is unfitted.)",
        fields={"input_data_path": FieldUI(kind="csv", label="Input features CSV"),
                "n_components": FieldUI(kind="int", label="Number of components", min=1, max=1000)},
        presets={"AlCoCrFeNi → 2 PCs": {"input_data_path": "example_ml:AlCoCrFeNi inputs (compositions)", "n_components": 2}},
        keywords=["pca", "dimension", "reduction", "principal component"],
    ),
    UiTool(
        id="augment_data_for_machine_learning", func="augment_data_for_machine_learning", title="Data augmentation (CVAE)",
        page="mltoolkit", section="CVAE", cli="3.1.3", runtime="minutes", needs=["torch"], gpu=False,
        summary="Generate synthetic samples with a conditional variational auto-encoder.",
        help="**What it does:** trains a small CVAE on your data and samples new feature vectors conditioned on existing targets.\n\n**When to use it:** very small datasets. Treat synthetic data with care and never evaluate on it.",
        fields={"input_data_path": FieldUI(kind="csv", label="Input features CSV"),
                "output_data_path": FieldUI(kind="csv", label="Output targets CSV"),
                "num_augmentations": FieldUI(kind="int", label="Synthetic samples", min=1, max=100000)},
        presets={"AlCoCrFeNi +100 samples": {"input_data_path": "example_ml:AlCoCrFeNi inputs (compositions)",
                                             "output_data_path": "example_ml:AlCoCrFeNi outputs (FCC properties)", "num_augmentations": 100}},
        keywords=["augmentation", "cvae", "vae", "synthetic"],
    ),
    UiTool(
        id="design_model_for_machine_learning", func="design_model_for_machine_learning", title="Model design (Optuna)",
        page="mltoolkit", section="Design", cli="3.2", runtime="long", needs=["torch", "optuna"], gpu=True,
        summary="Search neural-network architectures and optimisers with Optuna (5-fold CV).",
        help=("**What it does:** each trial builds an MLP (2–8 layers, 8–64 units, dropout, Adam/SGD/RMSprop, learning rate, weight decay), "
              "trains it with 5-fold cross-validation and records the validation loss. The best model and its parameters are saved for training.\n\n"
              "**When to use it:** before *Train*. Start with 10–20 trials; 100 trials can take hours on CPU."),
        fields={"input_data_path": FieldUI(kind="csv", label="Input features CSV"),
                "output_data_path": FieldUI(kind="csv", label="Output targets CSV"),
                "n_trials": FieldUI(kind="int", label="Trials", min=1, max=10000, default=20)},
        presets={"AlCoCrFeNi, 5 trials (quick)": {"input_data_path": "example_ml:AlCoCrFeNi inputs (compositions)",
                                                  "output_data_path": "example_ml:AlCoCrFeNi outputs (FCC properties)", "n_trials": 5}},
        keywords=["optuna", "hyperparameter", "design", "architecture", "neural network"],
    ),
    UiTool(
        id="train_model_for_machine_learning", func="train_model_for_machine_learning", title="Train & evaluate",
        page="mltoolkit", section="Train", cli="3.3", runtime="minutes", needs=["torch"], gpu=True,
        summary="Train the designed model from scratch with early stopping; parity plots and RMSE/R².",
        help="**What it does:** re-initialises the designed network, trains on 80 % / validates on 20 %, stops early when validation loss stalls, and reports RMSE/R² plus predicted-vs-true plots per target.",
        fields={"input_data_path": FieldUI(kind="csv", label="Input features CSV"),
                "output_data_path": FieldUI(kind="csv", label="Output targets CSV"),
                "best_model_path": FieldUI(kind="file", label="Designed model (best_model.pkl)", extensions=["pkl"]),
                "best_model_params_path": FieldUI(kind="file", label="Model parameters (best_model_params.log)", extensions=["log"]),
                "max_epochs": FieldUI(kind="int", label="Max epochs", min=1, max=1_000_000),
                "patience": FieldUI(kind="int", label="Early-stopping patience (epochs)", min=1, max=100000)},
        presets={"AlCoCrFeNi with example model": {
            "input_data_path": "example_ml:AlCoCrFeNi inputs (compositions)", "output_data_path": "example_ml:AlCoCrFeNi outputs (FCC properties)",
            "best_model_path": "example_ml:Example designed model (best_model.pkl)",
            "best_model_params_path": "example_ml:Example model parameters (best_model_params.log)", "max_epochs": 500, "patience": 50}},
        keywords=["train", "training", "fit", "parity", "rmse"],
    ),
    UiTool(
        id="retrain_model_for_machine_learning", func="retrain_model_for_machine_learning", title="Re-train with new data",
        page="mltoolkit", section="Train", cli="3.4", runtime="minutes", needs=["torch"], gpu=True,
        summary="Continue training an existing model (weights kept) on new data.",
        help="**What it does:** like *Train*, but starts from the existing weights (transfer / fine-tuning). Use a previously trained model.",
        fields={"input_data_path": FieldUI(kind="csv", label="Input features CSV"),
                "output_data_path": FieldUI(kind="csv", label="Output targets CSV"),
                "old_model_path": FieldUI(kind="file", label="Existing model (.pkl)", extensions=["pkl"]),
                "old_model_params_path": FieldUI(kind="file", label="Model parameters (.log)", extensions=["log"]),
                "max_epochs": FieldUI(kind="int", label="Max epochs", min=1, max=1_000_000),
                "patience": FieldUI(kind="int", label="Early-stopping patience", min=1, max=100000)},
        presets={"AlCoCrFeNi with example model": {
            "input_data_path": "example_ml:AlCoCrFeNi inputs (compositions)", "output_data_path": "example_ml:AlCoCrFeNi outputs (FCC properties)",
            "old_model_path": "example_ml:Example designed model (best_model.pkl)",
            "old_model_params_path": "example_ml:Example model parameters (best_model_params.log)", "max_epochs": 300, "patience": 30}},
        keywords=["retrain", "fine tune", "transfer"],
    ),
    UiTool(
        id="model_prediction_for_AlMgSiSc", func="model_prediction_for_AlMgSiSc", title="Al-Mg-Si-Sc mechanical properties",
        page="mltoolkit", section="Pretrained", cli="3.5.1", needs=["torch"],
        summary="Predict UTS, yield strength and elongation of Sc-modified Al-Mg-Si cast alloys.",
        help=("**What it does:** a pre-trained network (with CALPHAD phase fractions from a bundled database) predicts ultimate tensile "
              "strength, yield strength and elongation. Sc content and Al balance come from the database.\n\n"
              "Valid ranges: Mg 0–0.7 wt.%, Si 4–13 wt.% in 0.01 wt.% steps."),
        fields={"Mg": FieldUI(kind="slider_float", label="Mg (wt.%)", min=0.0, max=0.7, step=0.01, default=0.3),
                "Si": FieldUI(kind="slider_float", label="Si (wt.%)", min=4.0, max=13.0, step=0.01, default=7.0)},
        presets={"A356-like (Mg 0.3, Si 7)": {"Mg": 0.3, "Si": 7.0}},
        transform=_snap_almgsisc,
        keywords=["pretrained", "almgsisc", "aluminium alloy", "uts", "yield strength", "elongation", "casting"],
    ),
    UiTool(
        id="model_prediction_for_AlCoCrFeNi", func="model_prediction_for_AlCoCrFeNi", title="Al-Co-Cr-Fe-Ni high-entropy alloy",
        page="mltoolkit", section="Pretrained", cli="3.5.2", needs=["torch"],
        summary="FCC/BCC formation energy and elastic properties of AlCoCrFeNi HEAs.",
        help="**What it does:** predicts formation energies and cubic elastic constants (C11, C12, C44) of FCC and BCC phases, then derives bulk and shear moduli. Ni = 100 − (Al + Co + Cr + Fe) at.%.",
        fields={k: FieldUI(kind="slider_float", label=f"{k} (at.%)", min=0.0, max=100.0, step=0.5, default=20.0)
                for k in ("Al", "Co", "Cr", "Fe")},
        presets={"Equiatomic AlCoCrFeNi": {"Al": 20.0, "Co": 20.0, "Cr": 20.0, "Fe": 20.0},
                 "Cantor-like (no Al)": {"Al": 0.0, "Co": 25.0, "Cr": 25.0, "Fe": 25.0}},
        validators=[_hea_sum],
        keywords=["pretrained", "hea", "high entropy", "alcocrfeni", "formation energy", "elastic"],
    ),
    # ============================================================ FILE TOOLS (AI tools, exposed in library)
    UiTool(
        id="list_files", func="list_files", title="List files (Masgent)", page="library", section="Session files", cli="AI",
        summary="List every file in a session folder.", help="Masgent's own `list_files` tool, run against a copy of the selected run's files.",
        keywords=["list files", "files"],
    ),
    UiTool(
        id="read_file", func="read_file", title="Read file (Masgent)", page="library", section="Session files", cli="AI",
        summary="Read a text file from a session folder.", help="Masgent's own `read_file` tool.",
        fields={"name": FieldUI(label="File (relative path)")}, keywords=["read file"],
    ),
    UiTool(
        id="rename_file", func="rename_file", title="Copy / rename file (Masgent)", page="library", section="Session files", cli="AI",
        summary="Copy a file to a new name inside the session folder (Masgent's rename keeps the original, K10).",
        help="Masgent's own `rename_file` tool.",
        fields={"name": FieldUI(label="Existing file (relative path)"), "new_name": FieldUI(label="New name (relative path)")},
        keywords=["rename", "copy file"],
    ),
]

CATALOG_BY_ID: dict[str, UiTool] = {t.id: t for t in CATALOG}
