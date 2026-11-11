"""Schema-to-form renderer: turns a Masgent tool signature + Pydantic schema into widgets.

Generic inference covers every tool (so new Masgent tools get a usable form
automatically); catalog ``FieldUI.kind`` selects custom renderers where a
plain widget is not good enough (structure pickers, periodic table, matrices…).
"""
from __future__ import annotations

import json
import re
from typing import Any

import streamlit as st

from core.registry import FormField, form_fields, get_tool, parse_schema_fields
from ui import widgets
from ui.state import form_state, nonce


def _label(f: FormField) -> str:
    if f.ui.label:
        return f.ui.label
    return re.sub(r"_", " ", f.name).strip().capitalize()


def _help(f: FormField) -> str | None:
    return f.ui.help or (f.spec.description or None)


def literal_options(tool_id: str, f: FormField) -> list[str] | None:
    if f.ui.options:
        return list(f.ui.options)
    opts = f.spec.literal_options
    if opts:
        return opts
    _, td = get_tool(tool_id)
    if td.schema and f.spec.schema_field:
        ann = parse_schema_fields().get(td.schema, {}).get(f.spec.schema_field, {}).get("annotation", "")
        m = re.search(r"Literal\[(.*)\]", ann, re.S)
        if m:
            import ast

            try:
                return [str(x) for x in ast.literal_eval(f"[{m.group(1)}]")]
            except Exception:
                return None
    return None


def infer_kind(tool_id: str, f: FormField) -> str:
    if f.ui.kind:
        return f.ui.kind
    if literal_options(tool_id, f):
        return "segmented" if len(literal_options(tool_id, f) or []) <= 5 else "select"
    bt = f.spec.base_type
    name = f.name.lower()
    if bt == "bool":
        return "toggle"
    if bt in ("int",):
        return "int"
    if bt in ("float", "number"):
        return "float"
    if bt in ("list_int", "list_float"):
        return bt
    if bt == "dict":
        return "json"
    if name.endswith("_dir"):
        return "folder"
    if name.endswith("_path"):
        return "structure" if "poscar" in name else "file"
    if name in ("specie", "element") or name.endswith("_element"):
        return "element"
    return "text"


def render_field(tool_id: str, f: FormField, key: str, value: Any, current: dict[str, Any]) -> Any:
    kind = infer_kind(tool_id, f)
    label, hlp, ui = _label(f), _help(f), f.ui
    if kind == "structure":
        return widgets.structure_picker(label, key, value, help=hlp)
    if kind == "csv":
        return widgets.csv_picker(label, key, value, help=hlp)
    if kind == "file":
        return widgets.generic_file_picker(label, key, value, ui.extensions, help=hlp)
    if kind == "folder":
        return widgets.folder_picker(label, key, value, ui.folder_kind, help=hlp)
    if kind == "element":
        return widgets.element_select(label, key, value, help=hlp)
    if kind == "element_in_structure":
        from core.examples import resolve_ref
        from core.structures import elements_in

        sp = current.get(ui.structure_field or "poscar_path")
        els = elements_in(resolve_ref(sp)) if sp else []
        if not els:
            st.caption(f"**{label}**: choose a structure first.")
            return None
        if key in st.session_state and st.session_state[key] not in els:
            del st.session_state[key]
        return widgets.element_select(label, key, value if value in els else els[0], options=els, help=hlp)
    if kind == "defect_amount":
        return widgets.defect_amount_input(label, key, value, help=hlp)
    if kind == "miller":
        return widgets.miller_input(label, key, value, help=hlp)
    if kind == "matrix3":
        return widgets.matrix3_input(label, key, value, help=hlp)
    if kind in ("list_int", "list_float"):
        return widgets.number_list(label, key, value, as_int=kind == "list_int", help=hlp)
    if kind == "sqs":
        return widgets.sqs_input(label, key, value, current.get(ui.structure_field or "poscar_path"), help=hlp)
    if kind == "toggle":
        return st.toggle(label, value=bool(value) if value is not None else False, key=key, help=hlp)
    if kind in ("segmented", "select"):
        opts = literal_options(tool_id, f) or []
        fmt = (lambda o: (ui.option_labels or {}).get(o, str(o)))
        default = value if value in opts else (opts[0] if opts else None)
        if kind == "segmented":
            if key not in st.session_state:
                st.session_state[key] = default
            return st.segmented_control(label, opts, key=key, help=hlp, format_func=fmt, required=True)
        return st.selectbox(label, opts, index=opts.index(default) if default in opts else 0, key=key, help=hlp, format_func=fmt)
    if kind == "int":
        v = int(value) if value is not None else int(ui.min or 0)
        kw: dict[str, Any] = {"step": int(ui.step or 1)}
        if ui.min is not None:
            kw["min_value"] = int(ui.min)
            v = max(v, int(ui.min))
        if ui.max is not None:
            kw["max_value"] = int(ui.max)
            v = min(v, int(ui.max))
        return int(st.number_input(label, value=v, key=key, help=hlp, **kw))
    if kind == "float":
        v = float(value) if value is not None else float(ui.min or 0.0)
        kw = {"step": float(ui.step or 0.1), "format": "%.4g"}
        if ui.min is not None:
            kw["min_value"] = float(ui.min)
            v = max(v, float(ui.min))
        if ui.max is not None:
            kw["max_value"] = float(ui.max)
            v = min(v, float(ui.max))
        return float(st.number_input(label, value=v, key=key, help=hlp, **kw))
    if kind == "slider_float":
        v = float(value if value is not None else (ui.min or 0.0))
        return float(st.slider(label, min_value=float(ui.min or 0), max_value=float(ui.max or 100), value=v,
                               step=float(ui.step or 0.01), key=key, help=hlp))
    if kind == "textarea":
        return st.text_area(label, value=value or "", key=key, help=hlp, height=100)
    if kind == "json":
        txt = st.text_area(label, value=json.dumps(value, indent=1) if value else "{}", key=key, help=hlp)
        try:
            return json.loads(txt)
        except json.JSONDecodeError as e:
            st.caption(f":red[Invalid JSON: {e}]")
            return None
    return st.text_input(label, value=value or "", key=key, help=hlp, placeholder=ui.placeholder or "")


def render_form(tool_id: str, disabled_note: str | None = None) -> tuple[dict[str, Any], set[str]]:
    """Render all visible fields; returns (params, visible field names)."""
    fs = form_state(tool_id)
    n = nonce(tool_id)
    params: dict[str, Any] = {}
    visible: set[str] = set()
    for f in form_fields(tool_id):
        if f.ui.hidden:
            continue
        current = {**{ff.name: ff.default() for ff in form_fields(tool_id)}, **fs, **params}
        if f.ui.visible_if and not f.ui.visible_if(current):
            continue
        key = f"w:{tool_id}:{f.name}:{n}"
        value = fs.get(f.name, f.default())
        params[f.name] = render_field(tool_id, f, key, value, current)
        visible.add(f.name)
    fs.update(params)
    return params, visible
