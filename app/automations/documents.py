"""Automations as documents: saving from the editor, YAML text, export and import.

A document is the editable form of an automation::

    name: Cooling pump protection
    description: Stop the pump when the tank is too hot
    enabled: true
    mode: single            # single | restart | queued | parallel
    triggers:
      - type: numeric_state
        tag: Tank temperature   # id or unique tag label
        above: 80
        for: "00:00:30"
    conditions: []
    actions:
      - type: write
        tag: Pump enable
        value: off
      - type: notify
        message: "Pump stopped: {trigger}"
        severity: high

The approval for writing PLC tags (``allow_writes``) is never part of a
document: only an administrator can give it, in the editor.
"""

from __future__ import annotations

import json
import re

import yaml

from app.automations.schema import ConfigError, Lookup, validate, walk_actions
from app.i18n.catalog import gettext
from app.models.automation import MODES, Automation

#: Largest YAML text accepted (characters).
MAX_YAML = 200_000
#: Fields of a document besides the configuration.
DOC_FIELDS = ("name", "description", "enabled", "mode", "max_runs")


class _Dumper(yaml.SafeDumper):
    """YAML writer that quotes times and durations (``"06:00:00"``).

    Unquoted, YAML 1.1 would read ``06:00:00`` as a base-60 number.
    """


def _str(dumper, value: str):
    """Represent strings, quoting those that look like ``HH:MM``."""
    style = "'" if re.fullmatch(r"\d+(:\d+)+(\.\d+)?", value) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def to_yaml(doc) -> str:
    """Write a document as YAML (keys in their natural order, Unicode kept)."""
    return yaml.dump(doc, Dumper=_Dumper, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)


def from_yaml(text: str):
    """Parse YAML text (safe loader: plain data only).

    Raises:
        ConfigError: For syntax errors or texts that are too long.
    """
    if len(text or "") > MAX_YAML:
        raise ConfigError([gettext("The text is too long (at most {0} characters).", MAX_YAML)])
    try:
        return yaml.safe_load(text or "") or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = gettext("line {0}", mark.line + 1) + ": " if mark is not None else ""
        raise ConfigError([where + str(getattr(exc, "problem", None) or exc)]) from None


def check_document(doc, automation_id: int | None = None) -> dict:
    """Validate a whole document (settings and configuration).

    Args:
        doc: Parsed document.
        automation_id: Id of the automation being edited (name uniqueness, self-calls).

    Returns:
        ``{"name", "description", "enabled", "mode", "max_runs", "config"}``.

    Raises:
        ConfigError: With every problem found.
    """
    errors: list[str] = []
    if not isinstance(doc, dict):
        raise ConfigError([gettext("The automation must be an object with triggers, conditions and actions.")])
    unknown = sorted(set(doc) - set(DOC_FIELDS) - {"triggers", "conditions", "actions", "id", "allow_writes"})
    for key in unknown:
        errors.append(gettext("unknown field '{0}'", key))
    name = str(doc.get("name") or "").strip()[:100]
    if not name:
        errors.append(gettext("The automation needs a name."))
    else:
        other = Automation.query.filter(Automation.name == name).first()
        if other is not None and other.id != automation_id:
            errors.append(gettext("An automation named '{0}' already exists.", name))
    mode = str(doc.get("mode") or "single")
    if mode not in MODES:
        errors.append(gettext("mode must be one of: {0}.", ", ".join(MODES)))
    try:
        max_runs = int(doc.get("max_runs") or 10)
        if not 1 <= max_runs <= 100:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(gettext("max_runs must be a whole number from 1 to 100."))
        max_runs = 10
    config = None
    try:
        config = validate({k: doc.get(k) for k in ("triggers", "conditions", "actions")},
                          Lookup(exclude_automation=automation_id))
    except ConfigError as exc:
        errors.extend(exc.errors)
    if errors:
        raise ConfigError(errors)
    enabled = doc.get("enabled", True)
    return {"name": name, "description": str(doc.get("description") or "")[:500],
            "enabled": enabled if isinstance(enabled, bool) else str(enabled).lower() in ("1", "true", "yes", "on"),
            "mode": mode, "max_runs": max_runs, "config": config}


def write_signature(config: dict) -> str:
    """Text identifying every *Write tag* and *Calculate* action, to notice changes."""
    writes = [(a.get("type"), a.get("tag"), a.get("value"), a.get("expression"), a.get("on_range"),
               a.get("enabled", True))
              for a in walk_actions(config.get("actions", [])) if a.get("type") in ("write", "calculate")]
    return json.dumps(writes)


def apply(automation: Automation, checked: dict, user, allow_writes=None) -> list[str]:
    """Store a checked document on an automation (the caller commits).

    The approval for writing tags is kept only while the *Write tag* actions
    stay the same, unless the user is an administrator (who decides with
    ``allow_writes``).

    Args:
        automation: New or existing automation.
        checked: Result of :func:`check_document`.
        user: The user saving.
        allow_writes: The administrator's choice (``None`` = unchanged).

    Returns:
        Notes for the user (e.g. the approval was withdrawn).
    """
    notes = []
    old_writes = write_signature(automation.config) if automation.id else None
    automation.name = checked["name"]
    automation.description = checked["description"]
    automation.enabled = checked["enabled"]
    automation.mode = checked["mode"]
    automation.max_runs = checked["max_runs"]
    automation.config = checked["config"]
    new_writes = write_signature(checked["config"])
    if user.is_admin and allow_writes is not None:
        automation.allow_writes = bool(allow_writes)
    elif automation.allow_writes and old_writes != new_writes:
        automation.allow_writes = False
        notes.append(gettext("The Write tag / Calculate actions changed, so an administrator must approve writing again."))
    if automation.writes_tags() and not automation.allow_writes:
        notes.append(gettext("This automation writes PLC tags but is not approved for writing: its Write tag and "
                             "Calculate actions will fail until an administrator allows it."))
    return notes


def unique_name(name: str) -> str:
    """``name``, or ``name (2)``, ``name (3)`` … if taken."""
    base, n = name[:90], 1
    candidate = base
    while Automation.query.filter_by(name=candidate).first() is not None:
        n += 1
        candidate = f"{base} ({n})"
    return candidate


def readable(doc: dict) -> dict:
    """Copy of a document with references written as names where the name is unique.

    ``tag: 12`` becomes ``tag: Tank temperature`` when no other tag has that
    label, so YAML exports stay readable and can be imported into another
    installation with the same tag names.  Ambiguous names keep the id.
    """
    from app.automations.schema import KINDS  # noqa: PLC0415
    from app.extensions import db  # noqa: PLC0415
    from app.models import PLC, Alarm, DataPoint  # noqa: PLC0415

    models = {"tag": (DataPoint, "label"), "tag_writable": (DataPoint, "label"), "alarm": (Alarm, "name"),
              "plc": (PLC, "name"), "automation": (Automation, "name")}
    cache: dict = {}

    def name_of(kind, ref):
        key = (kind, ref)
        if key not in cache:
            model, attr = models[kind]
            obj = db.session.get(model, ref) if isinstance(ref, int) else None
            value = ref
            if obj is not None:
                label = getattr(obj, attr)
                if label and not str(label).strip().isdigit() and \
                        model.query.filter(getattr(model, attr) == label).count() == 1:
                    value = label
            cache[key] = value
        return cache[key]

    def blocks(items, kind):
        out = []
        for b in items or []:
            spec = KINDS[kind].get(b.get("type"), {"fields": []})
            c = dict(b)
            for f in spec["fields"]:
                if f["key"] not in c:
                    continue
                if f["kind"] in ("conditions", "actions"):
                    c[f["key"]] = blocks(c[f["key"]], f["kind"])
                elif f["kind"] in models:
                    c[f["key"]] = name_of(f["kind"], c[f["key"]])
                elif f["kind"] == "duration":
                    from app.automations.schema import format_duration  # noqa: PLC0415

                    c[f["key"]] = format_duration(c[f["key"]])
            out.append(c)
        return out

    result = {k: doc[k] for k in DOC_FIELDS if k in doc}
    if doc.get("mode") not in ("queued", "parallel"):
        result.pop("max_runs", None)
    for kind in ("triggers", "conditions", "actions"):
        result[kind] = blocks(doc.get(kind), kind)
    return result
