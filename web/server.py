"""Same-origin API for the Masgent React workspace."""
import base64
import csv
import hmac
from importlib.util import find_spec
import math
import os
import re
import sys
from zipfile import BadZipFile
from urllib.parse import parse_qs, urlsplit
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic

import requests

from flask import Flask, abort, jsonify, request, send_file, send_from_directory, session

WEB = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB.parent / "streamlit_app"))

from core import examples, parsers
from core.catalog import PAGES
from core.registry import form_fields, get_tool, validate
from core.runner import RunManager
from core.storage import Storage
from core.settings import get_secret, load_settings
from core.paths import UnsafePathError

MLP_TOOL = "run_simulation_using_mlps"
MLP_PACKAGES = {"CHGNet": "chgnet", "SevenNet": "sevenn", "Orb-v3": "orb_models", "MatterSim": "mattersim"}
TOOLS = (
    "convert_structure_format",
    "convert_poscar_coordinates",
    "generate_supercell_from_poscar",
    "generate_vasp_poscar_with_vacancy_defects",
    "generate_vasp_poscar_with_substitution_defects",
    "generate_vasp_poscar_with_interstitial_defects",
    "generate_surface_slab_from_poscar",
    "customize_vasp_kpoints_with_accuracy",
    "generate_interface_from_poscars",
    "generate_vasp_poscar",
    "generate_vasp_inputs_from_poscar:incar",
    "generate_vasp_inputs_from_poscar",
    "generate_vasp_workflow_of_convergence_tests",
    "generate_vasp_workflow_of_eos",
    "generate_vasp_workflow_of_elastic_constants",
    "generate_vasp_workflow_of_aimd",
    "generate_vasp_workflow_of_neb",
    "analyze_vasp_workflow_of_convergence_tests",
    "analyze_vasp_workflow_of_eos",
    "analyze_vasp_workflow_of_elastic_constants",
    "analyze_vasp_workflow_of_aimd",
    "analyze_vasp_workflow_of_neb",
    "analyze_features_for_machine_learning",
    "reduce_dimensions_for_machine_learning",
)


def create_app():
    password = os.environ.get("MASGENT_WEB_PASSWORD", "")
    secret = os.environ.get("MASGENT_WEB_SESSION_KEY", "")
    if len(password) < 20 or len(secret) < 32 or password == secret:
        raise RuntimeError("Set a shared password of at least 20 characters and a separate 32-character session key.")

    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_NAME="masgent_web",
        SESSION_COOKIE_SECURE=os.environ.get("MASGENT_WEB_LOCAL_HTTP") != "1",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )
    storage = Storage(os.environ.get("MASGENT_WEB_DATA", "masgent_projects/web"))
    manager = RunManager(storage, max_concurrent=1, warm_spares=0)
    examples_list = [name for name in examples.STRUCTURES if examples.structure_path(name)]
    model_options = [name for name, package in MLP_PACKAGES.items() if find_spec(package)]
    fields_by_tool = {tool: [f for f in form_fields(tool) if f.name != "input_format"]
                      for tool in (*TOOLS, MLP_TOOL)}

    def unavailable_reason(tool):
        ui, _ = get_tool(tool)
        if tool == MLP_TOOL and not model_options:
            return "No ML-potential engine is installed on this server."
        if tool == "generate_vasp_poscar" and not get_secret("MP_API_KEY"):
            return "Add MP_API_KEY in the host environment to enable this tool."
        if "PSP" in ui.needs:
            psp_dir = load_settings().psp_dir or os.environ.get("PMG_VASP_PSP_DIR")
            if not psp_dir or not Path(psp_dir).is_dir():
                return "This workflow requires a licensed VASP pseudopotential library configured on the host."
        return None

    def field_options(field):
        if field.ui.kind == "csv":
            return [name for name in examples.ML_FILES if name.endswith(("compositions)", "properties)")) and examples.ml_path(name)]
        if field.name == "mlps_type":
            return model_options
        if field.name == "vasp_input_sets":
            return ["MPMetalRelaxSet", "MPRelaxSet", "MPStaticSet", "MPNonSCFBandSet", "MPNonSCFDOSSet", "MPMDSet"]
        return field.ui.options or field.spec.literal_options

    def field_default(field):
        if field.name == "mlps_type" and model_options and field.default() not in model_options:
            return model_options[0]
        return field.default()
    attempt_lock = Lock()
    submit_lock = Lock()
    failures = []

    @app.before_request
    def require_session():
        if request.method == "POST" and request.headers.get("X-Masgent-Request") != "1":
            abort(403)
        if request.path.startswith("/api/") and request.path != "/api/session":
            if not session.get("authenticated"):
                abort(401)

    @app.after_request
    def protect_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get("/api/session")
    def session_status():
        return {"authenticated": bool(session.get("authenticated"))}

    @app.post("/api/session")
    def sign_in():
        supplied = (request.get_json(silent=True) or {})
        supplied = supplied.get("password") if isinstance(supplied, dict) else None
        if not isinstance(supplied, str):
            abort(400)
        with attempt_lock:
            now = monotonic()
            failures[:] = [when for when in failures if now - when < 60]
            if len(failures) >= 10:
                return {"error": "Too many attempts. Try again in a minute."}, 429
            if not hmac.compare_digest(supplied.encode(), password.encode()):
                failures.append(now)
                return {"error": "Incorrect password."}, 401
        session.clear()
        session.permanent = True
        session["authenticated"] = True
        return {"authenticated": True}

    @app.post("/api/logout")
    def sign_out():
        session.clear()
        return {"authenticated": False}

    @app.get("/api/catalog")
    def catalog():
        tools = []
        for tool, fields in fields_by_tool.items():
            ui, _ = get_tool(tool)
            tools.append(dict(id=tool, title=ui.title, group=PAGES[ui.page][0], description=ui.summary,
                              unavailable=unavailable_reason(tool), fields=[
                dict(name=f.name, label=f.ui.label or f.name, kind=f.ui.kind, type=f.spec.base_type,
                     default=field_default(f), options=field_options(f),
                     min=f.ui.min, max=f.ui.max, required=f.spec.required)
                for f in fields
            ]))
        return {"tools": tools, "examples": examples_list}

    @app.get("/api/runs")
    def list_runs():
        rows, _ = storage.query_runs()
        return jsonify([{key: row[key] for key in ("run_id", "tool_title", "status", "created")} for row in rows])

    @app.post("/api/runs")
    def submit():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("tool"), str) or not isinstance(body.get("params"), dict):
            abort(400)
        tool, raw = body["tool"], body["params"]
        if tool not in fields_by_tool:
            abort(404)
        reason = unavailable_reason(tool)
        if reason:
            return {"error": reason}, 503
        params = {"input_format": "POSCAR"} if tool == "convert_structure_format" else {}
        for field in fields_by_tool[tool]:
            if field.name == "custom_kppa" and raw.get("accuracy_level") != "Custom":
                continue
            if field.ui.visible_if and not field.ui.visible_if({**raw, **params}):
                continue
            value = raw.get(field.name, field_default(field))
            try:
                params[field.name] = validated_value(field, value, examples_list, field_options(field))
                if tool == MLP_TOOL and field.name in {"max_steps", "md_steps"} and params[field.name] > 2000:
                    raise ValueError(field.name)
            except ValueError:
                return {"error": f"Check {field.ui.label or field.name}."}, 400
        with ExitStack() as inputs:
            for name, value in params.items():
                if isinstance(value, dict) and "upload" in value:
                    folder = Path(inputs.enter_context(TemporaryDirectory(dir=storage.root / "uploads")))
                    spec = next(field for field in fields_by_tool[tool] if field.name == name)
                    try:
                        params[name] = str(stage_upload(value, spec.ui.kind, folder))
                    except (ValueError, TypeError, OSError, BadZipFile, UnsafePathError):
                        return {"error": f"Could not read the file supplied for {spec.ui.label or name}."}, 422
                    continue
                if not isinstance(value, str):
                    continue
                provider, _, entry = value.partition(":")
                if provider != "mp" and provider not in PUBLIC_CATALOGS:
                    continue
                from pymatgen.io.vasp import Poscar

                structure, error = mp_structure(entry) if provider == "mp" else public_structure(provider, entry)
                if error:
                    return error
                if not structure.is_ordered or len(structure) > 128:
                    return {"error": "Simulation inputs require ordered crystals with at most 128 sites."}, 422
                folder = inputs.enter_context(TemporaryDirectory(dir=storage.root / "uploads"))
                path = Path(folder) / "POSCAR"
                Poscar(structure).write_file(path)
                params[name] = str(path)
            with submit_lock:
                if len(manager.active_ids()) >= 5:
                    return {"error": "The run queue is full. Try again after a run finishes."}, 429
                errors = validate(tool, examples.resolve_params(params))
                if errors:
                    return {"error": " ".join(errors)}, 400
                run_id = manager.submit(tool, params, device="cpu", origin="web")
        return {"run_id": run_id}, 201

    @app.get("/api/runs/<run_id>")
    def run_detail(run_id):
        run = storage.get_run(run_id)
        if run is None:
            abort(404)
        result = run.result() or {}
        return {"run_id": run.run_id, "title": run.meta["tool_title"], "status": run.status,
                "message": result.get("message"),
                "metrics": {key: value for key, value in result.items()
                            if type(value) in (float, int) and math.isfinite(value)},
                "files": [p.relative_to(run.files_dir).as_posix()
                                                           for p in run.output_files()]}

    @app.post("/api/runs/<run_id>/cancel")
    def cancel(run_id):
        if not manager.cancel(run_id):
            abort(409)
        return {"cancelled": True}

    def result_path(run_id, name):
        run = storage.get_run(run_id)
        if run is None:
            abort(404)
        for path in run.output_files():
            if not path.is_symlink() and path.relative_to(run.files_dir).as_posix() == name:
                if path.resolve().is_relative_to(run.files_dir.resolve()):
                    return path
        abort(404)

    @app.get("/api/runs/<run_id>/files/<path:name>")
    def download(run_id, name):
        return send_file(result_path(run_id, name), as_attachment=True, mimetype="application/octet-stream")

    @app.get("/api/runs/<run_id>/plot/<path:name>")
    def result_plot(run_id, name):
        path = result_path(run_id, name)
        if path.suffix.lower() != ".png":
            abort(415)
        with path.open("rb") as file:
            if file.read(8) != b"\x89PNG\r\n\x1a\n":
                abort(415)
        return send_file(path, mimetype="image/png")

    @app.get("/api/runs/<run_id>/data/<path:name>")
    def result_data(run_id, name):
        path = result_path(run_id, name)
        if path.stat().st_size > 10_000_000:
            return {"error": "This file exceeds the preview limit. Download it to view all values."}, 413
        try:
            return run_data(path)
        except (ValueError, OSError, csv.Error):
            return {"error": "Could not read the calculation data."}, 422

    @app.get("/api/catalogs/<provider>/search")
    def public_search(provider):
        from pymatgen.core import Composition, Element

        if provider not in PUBLIC_CATALOGS:
            abort(404)
        query = request.args.get("query", "").strip()
        symbols = request.args.get("elements", "").split(",") if request.args.get("elements") else []
        page = request.args.get("page", default=0, type=int)
        cursor = request.args.get("cursor", "")
        if (not 0 <= page <= 10000 or len(symbols) > 18
                or any(not Element.is_valid_symbol(symbol) for symbol in symbols)
                or (query and not PUBLIC_ID.fullmatch(query)) or (cursor and not PUBLIC_ID.fullmatch(cursor))):
            abort(400)
        filters = []
        if query:
            if ":" not in query and "-" not in query and not query.isdigit():
                try:
                    comp = Composition(query, strict=True).reduced_composition
                    aflow = provider == "aflow"
                    formula = "".join(symbol + (f"{amount:g}" if aflow or amount != 1 else "")
                                      for symbol, amount in sorted(comp.get_el_amt_dict().items()))
                    property_name = "chemical_formula_descriptive" if aflow else "chemical_formula_reduced"
                    filters.append(f'{property_name}="{formula}"')
                except (ValueError, TypeError):
                    filters.append(f'id="{query}"')
            else:
                filters.append(f'id="{query}"')
        if symbols:
            filters.append("elements HAS ALL " + ",".join(f'"{symbol}"' for symbol in symbols))
        if not filters:
            return {"error": "Enter a formula, record ID, or select an element."}, 400
        data, error = optimade_query(provider, " AND ".join(filters), 25, page * 25, cursor)
        if error:
            return error
        items = [{"material_id": doc["id"], "formula_pretty": doc["attributes"].get("chemical_formula_reduced") or doc["id"]}
                 for doc in data["data"]]
        meta = data.get("meta") or {}
        next_link = (data.get("links") or {}).get("next")
        if isinstance(next_link, dict):
            next_link = next_link.get("href")
        paging = parse_qs(urlsplit(next_link).query) if isinstance(next_link, str) else {}
        return {"items": items, "more": bool(next_link or meta.get("more_data_available")),
                "cursor": paging.get("page_after", [""])[0]}

    @app.get("/api/catalogs/<provider>/structure")
    def public_preview(provider):
        if provider not in PUBLIC_CATALOGS:
            abort(404)
        structure, error = public_structure(provider, request.args.get("id", ""))
        return error or geometry_payload(structure)

    @app.get("/api/materials-project/status")
    def materials_project_status():
        return {"configured": bool(get_secret("MP_API_KEY"))}

    @app.get("/api/materials-project/search")
    def materials_project_search():
        from pymatgen.core import Element

        formula = request.args.get("query", "").strip()
        symbols = request.args.get("elements", "").split(",") if request.args.get("elements") else []
        page = request.args.get("page", default=0, type=int)
        if (not 0 <= page <= 10_000 or len(symbols) > 18
                or any(not Element.is_valid_symbol(symbol) for symbol in symbols)
                or (formula and not re.fullmatch(r"[A-Za-z0-9.()\-]{1,80}", formula))):
            abort(400)
        if not formula and not symbols:
            return {"error": "Enter a formula, material ID, or select an element."}, 400
        params = {"_fields": "material_id,formula_pretty,elements,energy_above_hull",
                  "_limit": 25, "_skip": 25 * page, "_sort_fields": "material_id"}
        if formula:
            params["material_ids" if MP_ID.fullmatch(formula) else "formula"] = formula
        if symbols:
            params["elements"] = ",".join(symbols)
        data, error = mp_summary(params)
        if error:
            return error
        return {"items": data["data"], "total": data.get("meta", {}).get("total_doc", len(data["data"]))}

    @app.get("/api/materials-project/structure/<material_id>")
    def materials_project_structure(material_id):
        structure, error = mp_structure(material_id)
        return error or geometry_payload(structure)

    @app.get("/api/structures")
    def structures():
        from pymatgen.io.vasp import Poscar

        entries = []
        for name in examples_list:
            structure = Poscar.from_file(examples.structure_path(name), check_for_potcar=False).structure
            entries.append(dict(name=name, formula=structure.composition.reduced_formula,
                                elements=[element.symbol for element in structure.composition.elements]))
        return jsonify(entries)

    @app.get("/api/structures/<int:index>")
    def example_structure(index):
        if not 0 <= index < len(examples_list):
            abort(404)
        return crystal_geometry(examples.structure_path(examples_list[index]), "poscar")

    @app.get("/api/runs/<run_id>/structure/<path:name>")
    def result_structure(run_id, name):
        path = result_path(run_id, name)
        suffix = path.suffix.lower()
        if path.name.upper() in {"POSCAR", "CONTCAR"} or suffix in {".vasp", ".poscar"}:
            return crystal_geometry(path, "poscar")
        if suffix == ".cif":
            return crystal_geometry(path, "cif")
        abort(415)

    @app.get("/")
    def homepage():
        return send_from_directory(WEB / "dist", "index.html")

    @app.get("/assets/<path:name>")
    def asset(name):
        return send_from_directory(WEB / "dist" / "assets", name)

    return app


def validated_value(field, value, example_names, options):
    kind = field.ui.kind
    if kind in {"structure", "folder", "csv"} and isinstance(value, dict):
        if isinstance(value.get("upload"), str) and isinstance(value.get("filename"), str):
            return value
    if kind == "csv" and isinstance(value, str) and value in options:
        return "example_ml:" + value
    if field.name == "formula" and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9.()\-]{1,80}", value):
        return value
    if kind == "list_int" and isinstance(value, list) and 1 <= len(value) <= 24:
        if all(type(n) is int and 1 <= n <= 100000 for n in value):
            return value
    if kind == "structure" and isinstance(value, str) and value in example_names:
        return f"example:{value}"
    if kind == "structure" and isinstance(value, str) and value.startswith("mp:") and MP_ID.fullmatch(value[3:]):
        return value
    if kind == "structure" and isinstance(value, str):
        provider, _, entry = value.partition(":")
        if provider in PUBLIC_CATALOGS and PUBLIC_ID.fullmatch(entry):
            return value
    if kind == "matrix3" and isinstance(value, str) and re.fullmatch(r"[1-4] 0 0; 0 [1-4] 0; 0 0 [1-4]", value):
        return value
    if kind == "miller" and isinstance(value, list) and len(value) == 3:
        if all(type(n) is int and abs(n) <= 3 for n in value) and any(value):
            return value
    if kind in {"element", "element_in_structure"} and isinstance(value, str) and re.fullmatch(r"[A-Z][a-z]?", value):
        return value
    if options and isinstance(value, str) and value in options:
        return value
    if field.spec.base_type == "bool" and type(value) is bool:
        return value
    if kind == "list_float" and isinstance(value, list) and 4 <= len(value) <= 15:
        if all(type(n) in (float, int) and .7 <= n <= 1.3 for n in value) and len(set(value)) == len(value):
            return value
    if type(value) in (float, int) and field.spec.base_type in {"int", "float", "number"}:
        low = field.ui.min if field.ui.min is not None else 0
        high = field.ui.max if field.ui.max is not None else 100
        if math.isfinite(value) and low <= value <= high and (field.spec.base_type != "int" or type(value) is int):
            return value
    raise ValueError(field.name)


def crystal_geometry(path, file_format):
    from pymatgen.core import Structure
    from pymatgen.io.vasp import Poscar

    if path.stat().st_size > 2_000_000:
        return {"error": "This structure is too large to preview."}, 413
    try:
        if file_format == "poscar":
            structure = Poscar.from_file(path, check_for_potcar=False).structure
        else:
            structure = Structure.from_file(path)
    except (ValueError, TypeError, OSError, IndexError):
        return {"error": "Could not read this crystal structure."}, 422
    return geometry_payload(structure)


def geometry_payload(structure):
    if len(structure) > 4000:
        return {"error": "The 3D viewer supports structures with up to 4,000 sites."}, 413
    return {
        "formula": structure.composition.reduced_formula,
        "lattice": structure.lattice.matrix.tolist(),
        "lengths": list(structure.lattice.abc),
        "angles": list(structure.lattice.angles),
        "sites": [{"element": max(site.species, key=site.species.get).symbol, "position": site.coords.tolist()}
                  for site in structure],
    }


MP_ID = re.compile(r"(?:mp|mvc)-[A-Za-z0-9]{1,32}")


def mp_summary(params):
    key = get_secret("MP_API_KEY")
    if not key:
        return None, ({"error": "Set MP_API_KEY on the server to enable Materials Project search."}, 503)
    try:
        response = requests.get("https://api.materialsproject.org/materials/summary/",
                                params=params, headers={"X-API-KEY": key},
                                timeout=(4, 16), allow_redirects=False)
        if response.status_code in (401, 403):
            return None, ({"error": "Materials Project rejected the server API key."}, 502)
        if response.status_code == 429:
            return None, ({"error": "Materials Project is rate-limiting requests. Try again shortly."}, 503)
        response.raise_for_status()
        return response.json(), None
    except (requests.RequestException, ValueError):
        return None, ({"error": "Materials Project search is temporarily unavailable."}, 502)


def mp_structure(material_id):
    from pymatgen.core import Structure

    if not MP_ID.fullmatch(material_id):
        return None, ({"error": "Invalid material ID."}, 400)
    data, error = mp_summary({"material_ids": material_id, "_fields": "structure", "_limit": 1})
    if error:
        return None, error
    if not data.get("data"):
        return None, ({"error": "No structure was found for that material."}, 404)
    try:
        return Structure.from_dict(data["data"][0]["structure"]), None
    except (KeyError, ValueError, TypeError, IndexError):
        return None, ({"error": "Materials Project returned an unreadable structure."}, 502)


def stage_upload(value, kind, folder):
    from pymatgen.core import Structure
    from pymatgen.io.vasp import Poscar

    data = base64.b64decode(value["upload"], validate=True)
    filename = value["filename"].lower()
    if kind == "folder":
        if not filename.endswith(".zip") or len(data) > 10 * 1024 * 1024:
            raise ValueError("Expected a ZIP archive of at most 10 MB.")
        files = Storage.safe_extract_zip(data, folder, max_total_mb=80, max_files=800)
        candidates = [path for path in files if path.name in examples.ANALYSIS_WHITELIST]
        if not candidates:
            raise ValueError("No VASP outputs found.")
        root = folder
        first_parts = {p.relative_to(root).parts[0] for p in candidates}
        if len(first_parts) == 1 and not any(p.parent == root for p in candidates):
            child = root / next(iter(first_parts))
            if child.is_dir() and not any(p.parent == child for p in candidates):
                root = child
        return root
    if kind == "csv":
        import csv
        import io

        if not filename.endswith(".csv") or len(data) > 2_000_000:
            raise ValueError("Expected a CSV of at most 2 MB.")
        rows = csv.reader(io.StringIO(data.decode("utf-8-sig")))
        header = next(rows, [])
        if not 1 <= len(header) <= 100:
            raise ValueError("Invalid CSV width.")
        for i, row in enumerate(rows):
            if i >= 20000 or len(row) != len(header):
                raise ValueError("Invalid CSV dimensions.")
        path = folder / "data.csv"
        path.write_bytes(data)
        return path
    if len(data) > 1_000_000:
        raise ValueError("Crystal input is too large.")
    contents = data.decode("utf-8-sig")
    if filename.endswith(".cif"):
        structure = Structure.from_str(contents, fmt="cif")
    else:
        structure = Poscar.from_str(contents).structure
    if not structure.is_ordered or not 1 <= len(structure) <= 128:
        raise ValueError("Expected an ordered crystal with at most 128 sites.")
    path = folder / "POSCAR"
    Poscar(structure).write_file(path)
    return path


def run_data(path):
    if path.name.lower() == "elastic_constants.txt":
        parsed = parsers.parse_elastic_txt(path)
        return {"headers": ["Component", "C1", "C2", "C3", "C4", "C5", "C6"],
                "rows": [[f"C{i}", *row] for i, row in enumerate(parsed.get("C") or [], 1)],
                "metrics": parsed.get("moduli", {}), "matrix": True}
    if path.name.lower() == "masgent_mlps_md.log":
        rows = parsers.parse_md_log(path)
        columns = ["time_ps", "T", "etot", "epot", "ekin"]
        return {"headers": ["Time (ps)", "Temperature (K)", "Total energy (eV/atom)",
                            "Potential energy (eV/atom)", "Kinetic energy (eV/atom)"],
                "rows": [[row[key] for key in columns] for row in rows[:2000]], "truncated": len(rows) > 2000}
    if path.name.lower() == "masgent_mlps_single.log":
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[0].rstrip(":") == "LBFGS" and fields[1].isdigit():
                rows.append([int(fields[1]), numeric_cell(fields[3]), numeric_cell(fields[4])])
        return {"headers": ["Optimizer step", "Energy (eV)", "Maximum force (eV/Å)"],
                "rows": rows[:2000], "truncated": len(rows) > 2000}
    if path.suffix.lower() != ".csv":
        return {"error": "This file format has no calculation preview."}, 415
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        headers = next(reader, [])[:40]
        rows = []
        for row in reader:
            if len(rows) == 2000:
                return {"headers": headers, "rows": rows, "truncated": True}
            rows.append([numeric_cell(cell) for cell in row[:len(headers)]])
    return {"headers": headers, "rows": rows}


def numeric_cell(cell):
    try:
        number = float(cell)
        if math.isfinite(number):
            return number
    except ValueError:
        pass
    return cell[:160]


PUBLIC_CATALOGS = {
    "oqmd": "https://oqmd.org/optimade/v1",
    "aflow": "https://aflow.org/API/optimade/v1",
    "alexandria": "https://alexandria.icams.rub.de/pbe/v1",
    "nomad": "https://nomad-lab.eu/prod/v1/optimade/v1",
}
PUBLIC_ID = re.compile(r"[A-Za-z0-9_.:+-]{1,180}")


def optimade_query(provider, filter_expression, limit, offset=0, cursor=""):
    params = {"filter": filter_expression, "page_limit": limit}
    if cursor:
        params["page_after"] = cursor
    elif offset:
        params["page_offset"] = offset
    if limit > 1:
        params["response_fields"] = "chemical_formula_reduced"
    try:
        response = requests.get(PUBLIC_CATALOGS[provider] + "/structures", params=params,
                                timeout=(4, 15), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Provider returned an unsuccessful response.")
        data = response.json()
        if not isinstance(data.get("data"), list):
            raise ValueError("Malformed provider response.")
        return data, None
    except (requests.RequestException, ValueError, AttributeError):
        return None, ({"error": "This catalog could not complete the query. Try again or choose another source."}, 502)


def public_structure(provider, entry):
    from pymatgen.core import Structure

    if not PUBLIC_ID.fullmatch(entry):
        return None, ({"error": "Invalid catalog record ID."}, 400)
    data, error = optimade_query(provider, f'id="{entry}"', 1)
    if error:
        return None, error
    if not data["data"]:
        return None, ({"error": "This catalog record was not found."}, 404)
    try:
        attributes = data["data"][0]["attributes"]
        lattice = attributes["lattice_vectors"]
        positions = attributes["cartesian_site_positions"]
        names = attributes["species_at_sites"]
        if not 0 < len(positions) <= 4000 or len(positions) != len(names):
            raise ValueError("Unsupported site count.")
        species = {item["name"]: {element: concentration for element, concentration in
                                  zip(item["chemical_symbols"], item["concentration"])
                                  if element != "vacancy" and concentration > 0}
                   for item in (attributes.get("species") or [])}
        occupancies = [species.get(name, name) for name in names]
        structure = Structure(lattice, occupancies, positions, coords_are_cartesian=True)
        return structure, None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return None, ({"error": "This record has no supported periodic crystal geometry."}, 422)
