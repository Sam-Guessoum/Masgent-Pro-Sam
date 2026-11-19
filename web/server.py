"""Same-origin API for the Masgent React workspace."""
import hmac
from importlib.util import find_spec
import math
import os
import re
import sys
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

from core import examples
from core.catalog import PAGES
from core.registry import form_fields, get_tool, validate
from core.runner import RunManager
from core.storage import Storage
from core.settings import get_secret

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
        MAX_CONTENT_LENGTH=16384,
    )
    storage = Storage(os.environ.get("MASGENT_WEB_DATA", "masgent_projects/web"))
    manager = RunManager(storage, max_concurrent=1, warm_spares=0)
    examples_list = [name for name in examples.STRUCTURES if examples.structure_path(name)]
    model_options = [name for name, package in MLP_PACKAGES.items() if find_spec(package)]
    fields_by_tool = {tool: [f for f in form_fields(tool) if f.name != "input_format"]
                      for tool in (*TOOLS, MLP_TOOL)}

    def field_options(field):
        if field.name == "mlps_type":
            return model_options
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
                              unavailable=tool == MLP_TOOL and not model_options, fields=[
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
        if tool == MLP_TOOL and not model_options:
            return {"error": "No ML-potential engine is installed on this server."}, 503
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
                if not isinstance(value, str) or not value.startswith("mp:"):
                    continue
                from pymatgen.io.vasp import Poscar

                structure, error = mp_structure(value[3:])
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
                "message": result.get("message"), "files": [p.relative_to(run.files_dir).as_posix()
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
    if kind == "structure" and isinstance(value, str) and value in example_names:
        return f"example:{value}"
    if kind == "structure" and isinstance(value, str) and value.startswith("mp:") and MP_ID.fullmatch(value[3:]):
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


MP_ID = re.compile(r"(?:mp|mvc)-[0-9]{1,15}")


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
