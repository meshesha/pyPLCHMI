"""JSON / XML documents of web devices: keys (paths), values and data types.

A web device sends one document (JSON or XML).  Every *leaf value* in it
gets a **key path** that a tag uses as its address:

JSON – dots and indexes, like JavaScript::

    {"meter": {"power": 12.5, "phases": [230.1, 229.8]}, "ok": true}

    meter.power        → 12.5
    meter.phases[0]    → 230.1
    meter.phases[1]    → 229.8
    ok                 → true
    ["key with space"] → keys that are not simple names are quoted
    $                  → the whole document when it is a single value

XML – slashes, like a simple XPath (indexes start at **1**; ``@`` = attribute;
name spaces are ignored)::

    <status><sensor id="T1"><temp unit="C">21.5</temp></sensor>
            <sensor id="T2"><temp unit="C">19.0</temp></sensor></status>

    status/sensor[1]/@id         → T1
    status/sensor[1]/temp        → 21.5
    status/sensor[1]/temp/@unit  → C
    status/sensor[2]/temp        → 19.0

``sensor`` without an index means the first ``sensor``.

:func:`flatten` lists all keys of a document (used by the *Process* table of
the Add device wizard), :func:`resolve` finds the value of one key, and
:func:`convert` turns the value into the tag's data type.
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET  # noqa: N817 (stdlib alias)

from app.i18n.catalog import gettext
from app.plc.datatypes import DATA_TYPES

#: Data formats.
FORMATS = {"json": "JSON", "xml": "XML"}
#: Data types available for web device tags.
WEB_TYPES = ("BOOL", "BYTE", "USINT", "SINT", "CHAR", "WORD", "UINT", "INT", "DWORD", "UDINT", "DINT",
             "REAL", "LREAL", "STRING")
#: Longest key path.
MAX_PATH = 255
#: Most keys listed by :func:`flatten`.
MAX_KEYS = 2000
#: Longest STRING suggested for text values.
MAX_STRING = 254

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
_JSON_TOKEN = re.compile(r'\.?([A-Za-z_][A-Za-z0-9_\-]*)|\[(\d+)\]|\[("(?:[^"\\]|\\.)*")\]')
_XML_STEP = re.compile(r"^([^\s/\[\]@]+)(?:\[(\d+)\])?$")
_TRUE = {"true", "on", "yes", "1"}
_FALSE = {"false", "off", "no", "0"}
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


class WebDataError(ValueError):
    """A document cannot be parsed, a key path is invalid or a value does not fit."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def parse_document(text: str, fmt: str):
    """Parse the text a device sent.

    Args:
        text: The received text.
        fmt: ``json`` or ``xml``.

    Returns:
        The JSON value, or the XML root :class:`~xml.etree.ElementTree.Element`.

    Raises:
        WebDataError: If the text is not valid JSON / XML.  XML with entity
            declarations (``<!ENTITY``) is refused for safety.
    """
    text = (text or "").strip().lstrip("﻿")
    if not text:
        raise WebDataError(gettext("The device sent no data."))
    if fmt == "xml":
        if "<!ENTITY" in text.upper():
            raise WebDataError(gettext("XML with entity declarations (<!ENTITY>) is not accepted."))
        try:
            return ET.fromstring(text)
        except ET.ParseError as exc:
            raise WebDataError(gettext("The data is not valid XML: {0}", exc)) from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise WebDataError(gettext("The data is not valid JSON: {0}", exc)) from exc


def pretty(text: str, fmt: str, limit: int = 65536) -> str:
    """The received text formatted for display (indented when it parses).

    Args:
        text: Received text.
        fmt: ``json`` or ``xml``.
        limit: Longest text returned (longer text is cut and marked).
    """
    out = text or ""
    try:
        doc = parse_document(text, fmt)
        if fmt == "xml":
            ET.indent(doc)
            out = ET.tostring(doc, encoding="unicode")
        else:
            out = json.dumps(doc, indent=2, ensure_ascii=False)
    except WebDataError:
        pass
    if len(out) > limit:
        out = out[:limit] + "\n… " + gettext("(cut: {0} characters in total)", len(out))
    return out


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    """XML tag without its name space (``{urn:x}temp`` → ``temp``)."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else str(tag)


def _json_key(key: str) -> str:
    """Path step of an object key: ``.name`` or ``["other key"]``."""
    return f".{key}" if _IDENT.match(key) else "[" + json.dumps(key, ensure_ascii=False) + "]"


def flatten(doc, fmt: str, limit: int = MAX_KEYS) -> list[tuple[str, object]]:
    """List every leaf value of a document with its key path.

    Args:
        doc: Result of :func:`parse_document`.
        fmt: ``json`` or ``xml``.
        limit: Stop after this many keys.

    Returns:
        ``[(path, value), …]`` in document order.  JSON values keep their
        type (``None`` for ``null``); XML values are text.
    """
    out: list[tuple[str, object]] = []
    if fmt == "xml":
        _flatten_xml(doc, _local(doc.tag), out, limit)
        return out

    def walk(value, path):
        if len(out) >= limit:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, path + _json_key(str(key)))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]")
        else:
            out.append((path.lstrip(".") or "$", value))

    walk(doc, "")
    return out


def _flatten_xml(elem, path: str, out: list, limit: int) -> None:
    """Add the attributes, text and children of an XML element to ``out``."""
    if len(out) >= limit:
        return
    for name, value in elem.attrib.items():
        out.append((f"{path}/@{_local(name)}", value))
    children = list(elem)
    text = (elem.text or "").strip()
    if not children:
        if text or not elem.attrib:
            out.append((path, text))
        return
    if text:
        out.append((path, text))
    counts: dict[str, int] = {}
    for child in children:
        if not isinstance(child.tag, str):   # comments, processing instructions
            continue
        name = _local(child.tag)
        counts[name] = counts.get(name, 0) + 1
    seen: dict[str, int] = {}
    for child in children:
        if not isinstance(child.tag, str):
            continue
        name = _local(child.tag)
        seen[name] = seen.get(name, 0) + 1
        step = f"{name}[{seen[name]}]" if counts[name] > 1 else name
        _flatten_xml(child, f"{path}/{step}", out, limit)


def check_path(path: str, fmt: str) -> str:
    """Validate the syntax of a key path.

    Args:
        path: Key path.
        fmt: ``json`` or ``xml``.

    Returns:
        The path without surrounding spaces.

    Raises:
        WebDataError: With instructions when the path is invalid.
    """
    path = (path or "").strip()
    if not path:
        raise WebDataError(gettext("Enter the key of the value, e.g. {0}.",
                                   "status/sensor[1]/temp" if fmt == "xml" else "meter.power"))
    if len(path) > MAX_PATH:
        raise WebDataError(gettext("The key is too long (at most {0} characters).", MAX_PATH))
    if fmt == "xml":
        _xml_steps(path)
    else:
        _json_steps(path)
    return path


def _json_steps(path: str) -> list:
    """Split a JSON key path into steps (``str`` keys and ``int`` indexes)."""
    if path == "$":
        return []
    steps, pos = [], 0
    while pos < len(path):
        match = _JSON_TOKEN.match(path, pos)
        if not match or (pos == 0 and path.startswith(".")) or \
                (pos > 0 and match.group(1) is not None and path[pos] != "."):
            raise WebDataError(gettext("Invalid JSON key '{0}' near position {1} – use names with dots and "
                                       "[n] indexes, e.g. meter.phases[0], or [\"key with space\"].", path, pos + 1))
        name, index, quoted = match.groups()
        if name is not None:
            steps.append(name)
        elif index is not None:
            steps.append(int(index))
        else:
            steps.append(json.loads(quoted))
        pos = match.end()
    return steps


def _xml_steps(path: str) -> tuple[list, str | None]:
    """Split an XML key path into ``([(name, index), …], attribute)``."""
    parts = path.strip("/").split("/")
    attribute = None
    if parts and parts[-1].startswith("@"):
        attribute = parts.pop()[1:]
        if not attribute:
            raise WebDataError(gettext("Write the attribute name after @, e.g. sensor/@id."))
    if not parts:
        raise WebDataError(gettext("The XML key must start with the name of the root element, e.g. status/temp."))
    steps = []
    for part in parts:
        match = _XML_STEP.match(part)
        if not match or (match.group(2) is not None and int(match.group(2)) < 1):
            raise WebDataError(gettext("Invalid XML key step '{0}' – use element names separated by /, "
                                       "[n] (from 1) for repeated elements and @name for attributes.", part))
        steps.append((match.group(1), int(match.group(2) or 1)))
    return steps, attribute


class Missing(Exception):
    """The key does not exist in this document."""


def resolve(doc, path: str, fmt: str):
    """Find the value of a key path in a parsed document.

    Args:
        doc: Result of :func:`parse_document`.
        path: Key path (see the module help).
        fmt: ``json`` or ``xml``.

    Returns:
        The value (JSON value or XML text).  An object or array (JSON) or an
        element with children (XML) is returned as is.

    Raises:
        Missing: If the key is not in the document.
        WebDataError: If the path is invalid.
    """
    if fmt == "xml":
        steps, attribute = _xml_steps(path)
        name, index = steps[0]
        if name != _local(doc.tag) or index != 1:
            raise Missing(path)
        elem = doc
        for name, index in steps[1:]:
            matches = [c for c in elem if isinstance(c.tag, str) and _local(c.tag) == name]
            if len(matches) < index:
                raise Missing(path)
            elem = matches[index - 1]
        if attribute is not None:
            for key, value in elem.attrib.items():
                if _local(key) == attribute:
                    return value
            raise Missing(path)
        return (elem.text or "").strip()
    value = doc
    for step in _json_steps(path):
        if isinstance(step, int):
            if not isinstance(value, list) or step >= len(value):
                raise Missing(path)
        elif not isinstance(value, dict) or step not in value:
            raise Missing(path)
        value = value[step]
    return value


# ---------------------------------------------------------------------------
# Values and data types
# ---------------------------------------------------------------------------
def suggest_type(value) -> tuple[str, int]:
    """Data type that fits a received value.

    Args:
        value: JSON value or XML text.

    Returns:
        ``(data_type, string_length)`` – ``BOOL`` for true/false, ``DINT`` for
        whole numbers, ``LREAL`` for other numbers, otherwise ``STRING``.
    """
    if isinstance(value, bool):
        return "BOOL", 0
    if isinstance(value, int):
        return ("DINT", 0) if -2**31 <= value < 2**31 else ("LREAL", 0)
    if isinstance(value, float):
        return "LREAL", 0
    if value is None:
        return "LREAL", 0
    text = str(value).strip()
    if text.lower() in ("true", "false"):
        return "BOOL", 0
    if _INT_RE.match(text):
        return ("DINT", 0) if -2**31 <= int(text) < 2**31 else ("LREAL", 0)
    if _FLOAT_RE.match(text):
        return "LREAL", 0
    return "STRING", min(MAX_STRING, max(20, len(text) * 2))


def display(value) -> str:
    """Short text of a received value for tables (``null``, ``true``, ``12.5``)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text if len(text) <= 200 else text[:200] + "…"


def convert(value, data_type: str, string_length: int = 0):
    """Convert a received value to a tag's data type.

    Args:
        value: JSON value or XML text.
        data_type: Tag data type (one of :data:`WEB_TYPES`).
        string_length: Maximum length for ``STRING``.

    Returns:
        ``bool``, ``int``, ``float`` or ``str``.

    Raises:
        WebDataError: If the value is missing (``null``) or does not fit.
    """
    if value is None:
        raise WebDataError(gettext("no value (null)"))
    if data_type == "STRING":
        text = value if isinstance(value, str) else display(value) if not isinstance(value, (dict, list)) \
            else json.dumps(value, ensure_ascii=False)
        return text[: string_length or MAX_STRING]
    if data_type == "CHAR":
        return str(value)[:1]
    if isinstance(value, (dict, list)):
        raise WebDataError(gettext("the key holds an object/list, not a single value"))
    if data_type == "BOOL":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise WebDataError(gettext("'{0}' is not a BOOL value (true/false, on/off, 1/0)", display(value)))
    number = _number(value)
    info = DATA_TYPES[data_type]
    if info.integer:
        if math.isnan(number) or math.isinf(number):
            raise WebDataError(gettext("'{0}' is not a number", display(value)))
        whole = int(round(number))
        if info.min is not None and not info.min <= whole <= info.max:
            raise WebDataError(gettext("{0} is out of range for {1} ({2}..{3})", whole, data_type, info.min, info.max))
        return whole
    return number


def _number(value) -> float:
    """A received value as a number (``true`` = 1)."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.lower() in ("true", "false"):
        return 1.0 if text.lower() == "true" else 0.0
    try:
        return float(text.replace(",", ".") if text.count(",") == 1 and "." not in text else text)
    except ValueError:
        raise WebDataError(gettext("'{0}' is not a number", display(value))) from None
