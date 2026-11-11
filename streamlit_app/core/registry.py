"""Tool registry: discover Masgent tools and pair them with their Pydantic schemas.

Discovery is done by parsing ``masgent/utils/tools.py`` and ``schemas.py`` with
``ast`` (milliseconds, no heavy imports). This yields, per tool:

* the ``@with_metadata`` information (name, description, requires, optional, defaults),
* the function signature (annotations as text, literal defaults),
* which ``schemas.X(...)`` class validates it and how parameters map to schema
  fields (from the keyword arguments of that call),
* field descriptions from the schema's ``Field(..., description=...)``.

The catalog (``core/catalog.py``) adds UI metadata on top. Tools that exist in
Masgent but not in the catalog still get an auto-generated UI entry, so future
Masgent tools appear automatically.

Validation (``validate``) imports the real ``masgent.utils.schemas`` lazily.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.paths import RUNS_DIR_PLACEHOLDER, ensure_masgent_importable


class _Missing:
    def __repr__(self) -> str:
        return "<required>"


MISSING = _Missing()


@dataclass
class SessionDefault:
    """Default derived from ``MASGENT_SESSION_RUNS_DIR`` (e.g. '<session>/POSCAR')."""

    suffix: str

    def resolve(self, session_dir: str | Path) -> str:
        return str(Path(session_dir) / self.suffix.lstrip("/\\"))

    def __repr__(self) -> str:
        return f"<session>/{self.suffix.lstrip('/')}"


@dataclass
class ParamSpec:
    name: str
    annotation: str
    default: Any
    schema_field: str | None = None
    description: str = ""

    @property
    def required(self) -> bool:
        return self.default is MISSING

    @property
    def literal_options(self) -> list[str] | None:
        m = re.search(r"Literal\[(.*)\]", self.annotation)
        if not m:
            return None
        try:
            val = ast.literal_eval(f"[{m.group(1)}]")
            return [str(v) for v in val]
        except Exception:
            return None

    @property
    def base_type(self) -> str:
        a = self.annotation.replace(" ", "")
        if self.literal_options:
            return "literal"
        if a in ("bool",):
            return "bool"
        if a in ("int",):
            return "int"
        if a in ("float",):
            return "float"
        if a in ("float|int", "int|float"):
            return "number"
        if a.lower().startswith(("list[int]", "list[int")) or a in ("List[int]",):
            return "list_int"
        if a.lower().startswith("list[float]") or a in ("list", "List[float]"):
            return "list_float"
        if a.lower().startswith("dict"):
            return "dict"
        return "str"


@dataclass
class ToolDef:
    func: str
    name: str
    description: str
    requires: list[str]
    optional: list[str]
    meta_defaults: dict[str, Any]
    params: list[ParamSpec]
    schema: str | None
    docstring: str
    lineno: int

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)


def _literal(node: ast.AST | None) -> Any:
    if node is None:
        return MISSING
    if isinstance(node, ast.JoinedStr):
        # f'{os.environ.get("MASGENT_SESSION_RUNS_DIR")}/POSCAR'
        tail = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
        return SessionDefault(tail or "POSCAR")
    try:
        return ast.literal_eval(node)
    except Exception:
        return ast.unparse(node)


def _masgent_utils_dir() -> Path:
    ensure_masgent_importable()
    import importlib.util

    spec = importlib.util.find_spec("masgent")
    if spec is None or not spec.submodule_search_locations:
        raise ImportError("masgent is not importable; install it with `uv pip install -e .`")
    return Path(list(spec.submodule_search_locations)[0]) / "utils"


@lru_cache(maxsize=4)
def parse_schema_fields(schemas_path: str | None = None) -> dict[str, dict[str, dict]]:
    path = Path(schemas_path) if schemas_path else _masgent_utils_dir() / "schemas.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, dict]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields: dict[str, dict] = {}
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                info: dict[str, Any] = {"annotation": ast.unparse(stmt.annotation), "default": MISSING, "description": ""}
                v = stmt.value
                if isinstance(v, ast.Call) and getattr(v.func, "id", None) == "Field":
                    if v.args:
                        d = v.args[0]
                        info["default"] = MISSING if isinstance(d, ast.Constant) and d.value is Ellipsis else _literal(d)
                    for kw in v.keywords:
                        if kw.arg == "description":
                            info["description"] = _literal(kw.value)
                elif v is not None:
                    info["default"] = _literal(v)
                fields[stmt.target.id] = info
        out[node.name] = fields
    return out


@lru_cache(maxsize=4)
def discover_tools(tools_path: str | None = None) -> dict[str, ToolDef]:
    """Parse ``tools.py`` and return every ``@with_metadata`` function."""
    path = Path(tools_path) if tools_path else _masgent_utils_dir() / "tools.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    schema_fields = parse_schema_fields(str(path.with_name("schemas.py")))
    tools: dict[str, ToolDef] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        meta_call = None
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and getattr(dec.func, "id", None) == "with_metadata" and dec.args:
                meta_call = dec.args[0]
        if meta_call is None or not isinstance(meta_call, ast.Call):
            continue
        meta = {kw.arg: kw.value for kw in meta_call.keywords}

        def lit(key: str, default: Any) -> Any:
            v = _literal(meta.get(key)) if key in meta else default
            return default if v is MISSING else v

        defaults_node = meta.get("defaults")
        meta_defaults: dict[str, Any] = {}
        if isinstance(defaults_node, ast.Dict):
            for k, v in zip(defaults_node.keys, defaults_node.values):
                meta_defaults[_literal(k)] = _literal(v)

        # signature
        args = node.args.args
        defs = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
        params = [
            ParamSpec(a.arg, ast.unparse(a.annotation) if a.annotation else "str", _literal(d))
            for a, d in zip(args, defs)
        ]

        # schema used + kwarg mapping (first schemas.X(...) call in the body)
        schema, mapping = None, {}
        calls = sorted(
            (sub for stmt in node.body for sub in ast.walk(stmt)
             if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
             and getattr(sub.func.value, "id", None) == "schemas" and sub.func.attr != "ToolMetadata"),
            key=lambda c: (c.lineno, c.col_offset),
        )
        if calls:
            schema = calls[0].func.attr
            for kw in calls[0].keywords:
                if isinstance(kw.value, ast.Name):
                    mapping[kw.value.id] = kw.arg
        sfields = schema_fields.get(schema or "", {})
        for p in params:
            p.schema_field = mapping.get(p.name)
            # Stricter than Masgent: also validate same-named schema fields that the
            # tool forgot to pass (e.g. custom_kppa K18, MLP temperature/md_steps).
            if p.schema_field is None and p.name in sfields:
                p.schema_field = p.name
            if p.schema_field and p.schema_field in sfields:
                p.description = str(sfields[p.schema_field].get("description") or "")

        tools[node.name] = ToolDef(
            func=node.name,
            name=str(lit("name", node.name)),
            description=str(lit("description", "")),
            requires=list(lit("requires", [])),
            optional=list(lit("optional", [])),
            meta_defaults=meta_defaults,
            params=params,
            schema=schema,
            docstring=(ast.get_docstring(node) or "").strip(),
            lineno=node.lineno,
        )
    return tools


def runtime_tool_functions() -> list[str]:
    """Names of tool functions found by actually importing Masgent (slow; tests)."""
    import os

    os.environ.setdefault("MASGENT_SESSION_RUNS_DIR", RUNS_DIR_PLACEHOLDER)
    ensure_masgent_importable()
    from masgent.utils import tools  # noqa: WPS433

    return sorted(n for n, f in vars(tools).items() if callable(f) and hasattr(f, "_tool_metadata"))


# ------------------------------------------------------------------ validation
def _clean_error(msg: str) -> str:
    return re.sub(r"^(Value error|Assertion failed),\s*", "", msg).strip()


def validate_with_schema(tool: ToolDef, params: dict[str, Any]) -> list[str]:
    """Validate parameters with the tool's own Pydantic schema. Returns messages."""
    if not tool.schema:
        return []
    ensure_masgent_importable()
    import os

    os.environ.setdefault("MASGENT_SESSION_RUNS_DIR", RUNS_DIR_PLACEHOLDER)
    from pydantic import ValidationError

    from masgent.utils import schemas

    cls = getattr(schemas, tool.schema, None)
    if cls is None:
        return []
    kwargs = {}
    for p in tool.params:
        if p.schema_field and p.name in params and params[p.name] is not None:
            kwargs[p.schema_field] = params[p.name]
    try:
        cls(**kwargs)
        return []
    except ValidationError as e:
        msgs = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()) if x != "__root__")
            m = _clean_error(err.get("msg", ""))
            msgs.append(f"{loc}: {m}" if loc else m)
        return msgs
    except Exception as e:  # validators that raise other exceptions
        return [_clean_error(str(e))]


# ------------------------------------------------------------ UI registry
@dataclass
class FormField:
    spec: ParamSpec
    ui: Any  # catalog.FieldUI

    @property
    def name(self) -> str:
        return self.spec.name

    def default(self) -> Any:
        from core.catalog import NODEFAULT

        if self.ui.default is not NODEFAULT:
            return self.ui.default
        d = self.spec.default
        if d is MISSING or isinstance(d, SessionDefault):
            return None
        return d


@lru_cache(maxsize=1)
def ui_tools() -> dict[str, Any]:
    """All UI tool entries (catalog + auto entries for uncatalogued Masgent tools)."""
    from core.catalog import CATALOG, UiTool

    tools = discover_tools()
    out: dict[str, Any] = {}
    covered = set()
    for t in CATALOG:
        if t.func in tools:
            out[t.id] = t
            covered.add(t.func)
    for func, td in tools.items():
        if func not in covered:
            out[func] = UiTool(
                id=func, func=func, title=td.name, page="more", section="New in Masgent",
                summary=td.description, help=td.description or td.docstring, runtime="seconds", auto=True,
            )
    return out


def get_tool(tool_id: str) -> tuple[Any, ToolDef]:
    ui = ui_tools()[tool_id]
    return ui, discover_tools()[ui.func]


def tools_on_page(page: str) -> list[Any]:
    return [t for t in ui_tools().values() if t.page == page]


def form_fields(tool_id: str) -> list[FormField]:
    from core.catalog import FieldUI

    ui, td = get_tool(tool_id)
    names = [p.name for p in td.params if p.name not in ui.fixed]
    if ui.order:
        names = [n for n in ui.order if n in names] + [n for n in names if n not in ui.order]
    fields = []
    for n in names:
        spec = td.param(n)
        f_ui = ui.fields.get(n) or FieldUI()
        if spec.default is MISSING and n in td.meta_defaults and not isinstance(td.meta_defaults[n], SessionDefault):
            # signature lacks a default but Masgent metadata provides one (AIMD, K05)
            from core.catalog import NODEFAULT

            if f_ui.default is NODEFAULT:
                f_ui = FieldUI(**{**f_ui.__dict__, "default": td.meta_defaults[n]})
        fields.append(FormField(spec, f_ui))
    return fields


def prepare_params(tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Apply fixed params and catalog transform (workarounds) before validation/run."""
    ui, _ = get_tool(tool_id)
    out = {k: v for k, v in params.items() if v is not None}
    out.update(ui.fixed)
    if ui.transform:
        out = ui.transform(out)
    return out


def validate(tool_id: str, params: dict[str, Any], visible: set[str] | None = None) -> list[str]:
    """Full validation: required fields, UI validators, then Masgent's own schema."""
    ui, td = get_tool(tool_id)
    errors: list[str] = []
    for f in form_fields(tool_id):
        if visible is not None and f.name not in visible:
            continue
        if f.spec.required and params.get(f.name) in (None, "", []):
            if f.default() is None:
                errors.append(f"{f.name}: required.")
    for v in ui.validators:
        errors += v(params)
    if not errors:
        errors += validate_with_schema(td, prepare_params(tool_id, params))
    return errors
