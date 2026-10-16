"""Collect every translatable text of pyPLCHMI into ``app/i18n/languages/en.json``.

The English file lists all message keys (each mapped to itself); it is the
reference for the coverage shown on *Admin → Languages* and the source of the
translation template that administrators download.

Sources:

* templates – ``_('…')`` / ``_("…")``;
* Python – ``gettext('…')`` / ``_('…')`` calls, the labels, descriptions,
  static choices and validator messages of every WTForms form, and the
  labels of constants shown in the user interface (protocols, widgets,
  severities, roles, permissions…);
* JavaScript – ``T('…')`` calls plus the designer's palette, property,
  menu and symbol texts (which are translated where they are displayed).

Usage (from the project folder, inside the venv)::

    python tools/i18n_extract.py            # rewrite en.json, report other languages
    python tools/i18n_extract.py --check    # only report (exit 1 if en.json is outdated)
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
APP = os.path.join(HERE, "app")
LANG_DIR = os.path.join(APP, "i18n", "languages")

_STR = r"""('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")"""
TEMPLATE_RE = re.compile(r"(?<![\w.])_\(\s*" + _STR)
# labels passed to the table/button macros (translated inside the macros)
TEMPLATE_MACRO_RES = [
    re.compile(r"sort_th\(state,\s*" + _STR + r",\s*" + _STR),
    re.compile(r"filter_select\(state,\s*" + _STR + r",\s*" + _STR + r"(?:,\s*\[([^\]]*)\])?"),
    re.compile(r"search_bar\(state,\s*" + _STR),
    re.compile(r"post_button\(url_for\([^)]*\),\s*" + _STR),
]
JS_CALL_RE = re.compile(r"(?<![\w.])T\(\s*" + _STR)
# hmi-designer.js: texts translated where they are displayed
JS_PATTERNS = [
    re.compile(r'\[\s*"\w+",\s*"(?:num|optnum|text|textarea|check|color|colorNone|select|symbol|point|dashboard|asset|range)",\s*'
               + _STR),                                           # property field labels
    re.compile(r'\[\s*"-",\s*' + _STR + r"\s*\]"),                # property section headers
    re.compile(r"\b(?:label|section):\s*" + _STR),                # palette items
    re.compile(r"\bstatus\(\s*" + _STR),                          # status bar messages
    re.compile(r"\bsection\(\s*" + _STR),                         # palette sections
    re.compile(r'\[\s*"\w+",\s*' + _STR + r"\s*\]"),              # [value, label] option pairs
    re.compile(r"\[\s*" + _STR + r",\s*\"(?:Ctrl\+\w|Shift\+[\]\[]|Del|R|\]|\[|)\""),  # context menu
    re.compile(r"\?\s*" + _STR + r"\s*:\s*" + _STR + r",\s*\"\""),  # menu: allLocked ? "Unlock" : "Lock", ""
    re.compile(r"\b(?:rect|ellipse|line|pipe|text|image|symbol|value|lamp|button|bar|icon):\s*" + _STR),  # TYPE_NAMES
    re.compile(r'"select",\s*"[^"]+",\s*\[([^\]]*)\]'),           # plain select option lists
]
SYMBOL_RE = re.compile(r'name:\s*' + _STR + r',\s*category:\s*' + _STR)
EXTRA_JS = ["all", "outline", "filled", "none", "toggle", "set", "input", "momentary", "link",
            "Building", "Piping", "HVAC", "Electrical", "Sensors", "PLC controls"]
#: WTForms' own messages that are shown unchanged.
WTFORMS = ["This field is required.", "Not a valid choice.", "Not a valid integer value.", "Not a valid float value.",
           "Invalid input.", "Invalid email address.", "The CSRF token is missing.", "The CSRF token has expired.",
           "The CSRF tokens do not match."]


def literal(text: str) -> str:
    """Evaluate a quoted string literal (JS literals are close enough to Python's)."""
    return ast.literal_eval(text)


def from_templates() -> set:
    """Texts of ``_()`` calls in the Jinja templates."""
    keys = set()
    for path in glob.glob(os.path.join(APP, "templates", "**", "*.html"), recursive=True):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for m in TEMPLATE_RE.finditer(src):
            keys.add(literal(m.group(1)))
        for rx in TEMPLATE_MACRO_RES:
            for m in rx.finditer(src):
                groups = list(m.groups())
                if rx.pattern.startswith(("sort_th", "filter_select")):
                    groups = groups[1:]  # skip the column / filter key
                if rx.pattern.startswith("filter_select") and groups[-1]:
                    pairs = re.findall(r"\(\s*" + _STR + r"\s*,\s*" + _STR + r"\s*\)", groups[-1])
                    keys.update(literal(label) for _value, label in pairs)
                    groups = groups[:-1]
                keys.update(literal(g) for g in groups if g)
    return keys


def from_python() -> set:
    """Texts of ``gettext()`` / ``_()`` calls and of form field declarations."""
    keys = set()
    field_kw = ("label", "description", "message")
    for path in glob.glob(os.path.join(APP, "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in ("gettext", "_") and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
            if name.endswith("Field") and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
            for kw in node.keywords:
                if kw.arg in field_kw and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    keys.add(kw.value.value)
    return {k for k in keys if k.strip()}


def from_runtime() -> set:
    """Labels of constants and form choices, read from the imported application."""
    from wtforms.fields.core import UnboundField  # noqa: PLC0415

    from app import create_app  # noqa: PLC0415
    from app.i18n.forms import FlaskForm  # noqa: PLC0415

    keys = set()
    app = create_app("testing")
    with app.app_context():
        import app.admin.forms  # noqa: F401,PLC0415
        import app.alarms.forms  # noqa: F401,PLC0415
        import app.auth.forms  # noqa: F401,PLC0415
        import app.dashboards.forms  # noqa: F401,PLC0415
        import app.history.forms  # noqa: F401,PLC0415
        import app.tags.forms  # noqa: F401,PLC0415
        from app.dashboards.layout import BUTTON_ACTIONS  # noqa: PLC0415
        from app.models import Dashboard, DataPoint  # noqa: PLC0415
        from app.models.alarm import KINDS, OPERATORS, SEVERITIES  # noqa: PLC0415
        from app.models.history import AXES, RANGES  # noqa: PLC0415
        from app.models.user import DEFAULT_ROLES, THEMES, Perm  # noqa: PLC0415
        from app.plc.protocols import MODBUS_LIBRARIES, PROTOCOLS  # noqa: PLC0415

        def subclasses(cls):
            for sub in cls.__subclasses__():
                yield sub
                yield from subclasses(sub)

        for form_cls in subclasses(FlaskForm):
            for name in dir(form_cls):
                unbound = getattr(form_cls, name, None)
                if not isinstance(unbound, UnboundField):
                    continue
                choices = unbound.kwargs.get("choices")
                if isinstance(choices, (list, tuple)):
                    for c in choices:
                        if isinstance(c, (list, tuple)) and len(c) >= 2 and isinstance(c[1], str):
                            keys.add(c[1])
                for v in unbound.kwargs.get("validators", []) or []:
                    msg = getattr(v, "message", None)
                    if isinstance(msg, str):
                        keys.add(msg)
        keys.update(PROTOCOLS.values())
        keys.update(f"{PROTOCOLS['modbus_tcp']} ({lib})" for lib in MODBUS_LIBRARIES.values())
        keys.update(DataPoint.WIDGETS.values())
        keys.update(DataPoint.DUMMY_SCOPES.values())
        keys.update(Dashboard.KINDS.values())
        keys.update(v[0] for v in SEVERITIES.values())
        keys.update(KINDS.values())
        keys.update(text for _sym, text in OPERATORS.values())
        keys.update(DEFAULT_ROLES.keys())
        keys.update(spec["description"] for spec in DEFAULT_ROLES.values())
        keys.update(Perm.DESCRIPTIONS.values())
        keys.update(BUTTON_ACTIONS)
        keys.update(AXES.values())
        keys.update(THEMES.values())
        from app.audit.recorder import OBJECT_TYPES  # noqa: PLC0415
        from app.models.audit import ACTIONS  # noqa: PLC0415

        keys.update(ACTIONS.values())
        keys.update(OBJECT_TYPES)
        keys.update(label for _seconds, label in RANGES.values())
        keys.add("no device")
    return {k for k in keys if isinstance(k, str) and k.strip()}


def from_javascript() -> set:
    """Texts of ``T()`` calls and of the designer/symbol definitions."""
    keys = set(EXTRA_JS)
    for path in glob.glob(os.path.join(APP, "static", "js", "*.js")):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for m in JS_CALL_RE.finditer(src):
            keys.add(literal(m.group(1)))
        if path.endswith("hmi-designer.js"):
            for rx in JS_PATTERNS:
                for m in rx.finditer(src):
                    for g in m.groups():
                        if g is None:
                            continue
                        # a single literal, or the inside of a list of literals
                        keys.update(literal(x) for x in re.findall(_STR, g))
        if path.endswith("hmi-symbols.js"):
            for m in SYMBOL_RE.finditer(src):
                keys.add(literal(m.group(1)))
                keys.add(literal(m.group(2)))
    return {k for k in keys if k.strip() and re.search(r"[A-Za-z]", k)}


def collect() -> list:
    """All message keys, sorted."""
    keys = from_templates() | from_python() | from_runtime() | from_javascript() | set(WTFORMS)
    keys = {k for k in keys if re.search(r"[^\W\d_]", k) and not k.startswith("bi-")}  # text, not icon names
    return sorted(keys, key=lambda k: (k.lower(), k))


def main() -> int:
    """Command-line entry point (see the module docstring).

    Returns:
        Exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report only; exit 1 when en.json is outdated")
    args = parser.parse_args()
    keys = collect()
    path = os.path.join(LANG_DIR, "en.json")
    with open(path, encoding="utf-8") as fh:
        old = json.load(fh)
    outdated = list(old.get("messages", {})) != keys
    if not args.check:
        doc = {"language": {"code": "en", "name": "English", "english_name": "English", "direction": "ltr"},
               "messages": {k: k for k in keys}}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print(f"en.json: {len(keys)} messages")
    for other in sorted(glob.glob(os.path.join(LANG_DIR, "*.json"))):
        if other.endswith("en.json"):
            continue
        with open(other, encoding="utf-8") as fh:
            messages = json.load(fh).get("messages", {})
        missing = [k for k in keys if not messages.get(k)]
        unused = [k for k in messages if k not in set(keys)]
        print(f"{os.path.basename(other)}: {len(keys) - len(missing)}/{len(keys)} translated, "
              f"{len(missing)} missing, {len(unused)} unused")
        for k in missing[:40]:
            print("   missing:", k)
    if args.check and outdated:
        print("en.json is outdated – run python tools/i18n_extract.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
