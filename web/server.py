"""Same-origin API for the Masgent React workspace."""
import hmac
import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from threading import Lock
from time import monotonic

from flask import Flask, abort, jsonify, request, send_file, send_from_directory, session

WEB = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB.parent / "streamlit_app"))

from core import examples
from core.catalog import PAGES
from core.registry import form_fields, get_tool, validate
from core.runner import RunManager
from core.storage import Storage

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
    fields_by_tool = {tool: [f for f in form_fields(tool) if f.name != "input_format"] for tool in TOOLS}
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
            tools.append(dict(id=tool, title=ui.title, group=PAGES[ui.page][0], description=ui.summary, fields=[
                dict(name=f.name, label=f.ui.label or f.name, kind=f.ui.kind, type=f.spec.base_type,
                     default=f.default(), options=f.ui.options or f.spec.literal_options,
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
        params = {"input_format": "POSCAR"} if tool == "convert_structure_format" else {}
        for field in fields_by_tool[tool]:
            if field.name == "custom_kppa" and raw.get("accuracy_level") != "Custom":
                continue
            value = raw.get(field.name, field.default())
            try:
                params[field.name] = validated_value(field, value, examples_list)
            except ValueError:
                return {"error": f"Check {field.ui.label or field.name}."}, 400
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

    @app.get("/api/runs/<run_id>/files/<path:name>")
    def download(run_id, name):
        run = storage.get_run(run_id)
        if run is None:
            abort(404)
        for path in run.output_files():
            if not path.is_symlink() and path.relative_to(run.files_dir).as_posix() == name:
                if path.resolve().is_relative_to(run.files_dir.resolve()):
                    return send_file(path, as_attachment=True, mimetype="application/octet-stream")
        abort(404)

    @app.get("/")
    def homepage():
        return send_from_directory(WEB / "dist", "index.html")

    @app.get("/assets/<path:name>")
    def asset(name):
        return send_from_directory(WEB / "dist" / "assets", name)

    return app


def validated_value(field, value, example_names):
    kind = field.ui.kind
    if kind == "structure" and isinstance(value, str) and value in example_names:
        return f"example:{value}"
    if kind == "matrix3" and isinstance(value, str) and re.fullmatch(r"[1-4] 0 0; 0 [1-4] 0; 0 0 [1-4]", value):
        return value
    if kind == "miller" and isinstance(value, list) and len(value) == 3:
        if all(type(n) is int and abs(n) <= 3 for n in value) and any(value):
            return value
    if kind in {"element", "element_in_structure"} and isinstance(value, str) and re.fullmatch(r"[A-Z][a-z]?", value):
        return value
    options = field.ui.options or field.spec.literal_options
    if options and isinstance(value, str) and value in options:
        return value
    if field.spec.base_type == "bool" and type(value) is bool:
        return value
    if type(value) in (float, int) and field.spec.base_type in {"int", "float", "number"}:
        low = field.ui.min if field.ui.min is not None else 0
        high = field.ui.max if field.ui.max is not None else 100
        if low <= value <= high and (field.spec.base_type != "int" or type(value) is int):
            return value
    raise ValueError(field.name)
