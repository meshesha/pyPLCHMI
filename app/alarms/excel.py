"""Excel export and import of alarm definitions.

Export: one row per alarm (see :data:`COLUMNS`) plus a *Help* sheet.

Import (create or update), all-or-nothing like the tag import:

* a row whose **ID** is an existing alarm updates it; other rows create alarms;
* **Trigger** is ``tag`` (condition on a tag) or ``comm`` (PLC communication lost);
* the tag is found by **Tag ID**, or else by **Tag** label + **Tag PLC**;
* **Condition** accepts ``=``, ``≠``/``!=``/``<>``, ``>``, ``≥``/``>=``, ``<``,
  ``≤``/``<=`` or the codes ``eq ne gt ge lt le``;
* **Severity** is Critical / High / Low; **Screen** is a screen name (optional);
* every row is validated exactly like the alarm form (unique name, value
  matching the tag type, one communication alarm per PLC).
"""

from __future__ import annotations

from werkzeug.datastructures import MultiDict

from app.alarms.forms import AlarmForm
from app.extensions import db
from app.models import PLC, Alarm, Dashboard, DataPoint
from app.models.alarm import OPERATORS, SEVERITIES
from app.tags.excel import ImportResult
from app.utils.xlsx import ImportFileError, boolean, build_workbook, number, read_rows, text, yes_no
from app.i18n.catalog import gettext

COLUMNS = [
    ("ID", 7), ("Name", 30), ("Description", 40), ("Trigger", 9), ("Tag ID", 8), ("Tag", 24), ("Tag PLC", 16),
    ("Condition", 10), ("Value", 12), ("PLC", 16), ("Severity", 10), ("Screen", 22), ("Enabled", 9),
]

HELP = [
    ("ID", "Alarm id. Keep it to update that alarm when importing; leave it empty to create new alarms."),
    ("Name", "Unique alarm name (required)."),
    ("Description", "Text shown in the alarm window."),
    ("Trigger", "tag = condition on a tag · comm = PLC communication lost (uses the PLC's communication timeout)."),
    ("Tag ID / Tag / Tag PLC", "The watched tag (trigger tag): by Tag ID, or by Tag label together with Tag PLC."),
    ("Condition", "=, ≠, >, ≥, <, ≤ (also !=, <>, >=, <= or eq, ne, gt, ge, lt, le). BOOL tags: = or ≠ only."),
    ("Value", "Number for numeric tags, true/false for BOOL tags, text for STRING tags."),
    ("PLC", "PLC name (trigger comm only)."),
    ("Severity", "Critical (red), High (orange) or Low (green)."),
    ("Screen", "Screen opened by 'Open Screen' (optional, exact screen name)."),
    ("Enabled", "yes / no."),
]

ALIASES = {
    "id": "id", "name": "name", "alarm": "name", "description": "description", "trigger": "kind", "kind": "kind",
    "type": "kind", "tag id": "tag_id", "tag": "tag", "tag label": "tag", "tag plc": "tag_plc",
    "condition": "operator", "operator": "operator", "value": "setpoint", "setpoint": "setpoint", "plc": "plc",
    "severity": "severity", "screen": "screen", "enabled": "enabled",
}

#: Accepted spellings of the operators.
OPERATOR_INPUT = {"=": "eq", "==": "eq", "eq": "eq", "≠": "ne", "!=": "ne", "<>": "ne", "ne": "ne", ">": "gt",
                  "gt": "gt", "≥": "ge", ">=": "ge", "ge": "ge", "<": "lt", "lt": "lt", "≤": "le", "<=": "le", "le": "le"}


def _row(alarm: Alarm) -> list:
    """Export values of one alarm.

    Args:
        alarm: The alarm.

    Returns:
        Row values in :data:`COLUMNS` order.
    """
    dp = alarm.datapoint
    comm = alarm.is_comm
    return [alarm.id, alarm.name, alarm.description, alarm.kind,
            None if comm or dp is None else dp.id, "" if comm or dp is None else dp.label,
            "" if comm or dp is None else dp.plc.name,
            "" if comm else OPERATORS.get(alarm.operator, ("?",))[0], "" if comm else alarm.setpoint,
            alarm.plc.name if comm and alarm.plc else "", alarm.severity_label,
            alarm.screen.name if alarm.screen else "", yes_no(alarm.enabled)]


def export_alarms(alarms) -> bytes:
    """Build the .xlsx file for a list of alarms.

    Args:
        alarms: Iterable of :class:`Alarm`.

    Returns:
        Workbook bytes.
    """
    return build_workbook("Alarms", COLUMNS, (_row(a) for a in alarms), HELP)


def _find_tag(record: dict, columns: set):
    """Resolve the watched tag of a row.

    Args:
        record: Row values.
        columns: Columns present.

    Returns:
        ``(DataPoint or None, error message or None)``.
    """
    if "tag_id" in columns and text(record.get("tag_id")):
        try:
            dp = db.session.get(DataPoint, int(number(record.get("tag_id"))))
        except (TypeError, ValueError):
            return None, gettext("Tag ID '{0}' is not a number", record.get('tag_id'))
        if dp is not None:
            return dp, None
    label, plc_name = text(record.get("tag")), text(record.get("tag_plc"))
    if not label:
        return None, gettext('Tag not found (give Tag ID, or Tag and Tag PLC)')
    query = DataPoint.query.filter(DataPoint.label == label)
    if plc_name:
        query = query.join(PLC, DataPoint.plc_id == PLC.id).filter(PLC.name == plc_name)
    matches = query.all()
    if not matches:
        return None, f"Tag '{label}'" + (f" on PLC '{plc_name}'" if plc_name else "") + " not found"
    if len(matches) > 1:
        return None, gettext("Several tags are called '{0}' – add Tag PLC or Tag ID", label)
    return matches[0], None


def _apply_row(record: dict, columns: set) -> tuple[str, Alarm | None, str]:
    """Validate one row and apply it to a new or existing alarm (not committed).

    Args:
        record: Row values.
        columns: Columns present.

    Returns:
        ``(action, alarm, message)``.
    """
    try:
        alarm_id = number(record.get("id")) if "id" in columns else None
    except ValueError:
        return "error", None, gettext("ID '{0}' is not a number", record.get('id'))
    existing = db.session.get(Alarm, int(alarm_id)) if alarm_id else None
    fields = {
        "name": existing.name if existing else "", "description": existing.description if existing else "",
        "kind": existing.kind if existing else "tag", "datapoint_id": existing.datapoint_id if existing else 0,
        "operator": existing.operator if existing else "gt", "setpoint": existing.setpoint if existing else "",
        "plc_id": existing.plc_id if existing else 0, "severity": existing.severity if existing else "high",
        "screen_id": existing.screen_id if existing else 0, "enabled": existing.enabled if existing else True,
    }
    try:
        for key in ("name", "description", "setpoint"):
            if key in columns:
                fields[key] = text(record.get(key))
        if "kind" in columns and text(record.get("kind")):
            kind = text(record.get("kind")).lower()
            fields["kind"] = "comm" if kind.startswith("comm") else "tag" if kind.startswith("tag") else kind
        if fields["kind"] == "tag" and ({"tag_id", "tag"} & columns):
            dp, problem = _find_tag(record, columns)
            if problem:
                return "error", None, problem
            fields["datapoint_id"] = dp.id
        if "operator" in columns and text(record.get("operator")):
            op = OPERATOR_INPUT.get(text(record.get("operator")).lower())
            if op is None:
                return "error", None, gettext("Unknown condition '{0}'", record.get('operator'))
            fields["operator"] = op
        if fields["kind"] == "comm" and "plc" in columns:
            name = text(record.get("plc"))
            plc = PLC.query.filter(db.func.lower(PLC.name) == name.lower()).first() if name else None
            if plc is None:
                return "error", None, f"PLC '{name}' does not exist" if name else "PLC is empty"
            fields["plc_id"] = plc.id
        if "severity" in columns and text(record.get("severity")):
            raw = text(record.get("severity")).lower()
            code = next((k for k, v in SEVERITIES.items() if raw in (k, v[0].lower())), None)
            if code is None:
                return "error", None, gettext("Unknown severity '{0}' (Critical, High or Low)", record.get('severity'))
            fields["severity"] = code
        if "screen" in columns:
            name = text(record.get("screen"))
            screen = Dashboard.query.filter(db.func.lower(Dashboard.name) == name.lower()).first() if name else None
            if name and screen is None:
                return "error", None, gettext("Screen '{0}' does not exist", name)
            fields["screen_id"] = screen.id if screen else 0
        if "enabled" in columns:
            fields["enabled"] = boolean(record.get("enabled"), default=True)
    except ValueError as exc:
        return "error", None, str(exc)

    data = {k: ("" if v is None else str(v)) for k, v in fields.items() if k != "enabled"}
    if fields["enabled"]:
        data["enabled"] = "y"
    form = AlarmForm(formdata=MultiDict(data), meta={"csrf": False}, alarm=existing)
    if not form.validate():
        problems = "; ".join(f"{k.replace('_id', '').replace('_', ' ')}: {', '.join(v)}" for k, v in form.errors.items())
        return "error", None, problems
    alarm = existing or Alarm()
    if existing is None:
        db.session.add(alarm)
    was_enabled = existing.enabled if existing else False
    form.populate(alarm)
    changed = existing is None or db.session.is_modified(alarm, include_collections=False)
    if was_enabled and not alarm.enabled and alarm.open_event is not None:
        alarm.open_event.close("manual")
    db.session.flush()
    action = "created" if existing is None else ("updated" if changed else "unchanged")
    return action, alarm, alarm.condition_text


def import_alarms(stream, check_only: bool = False) -> ImportResult:
    """Import alarm definitions from an uploaded .xlsx file.

    Args:
        stream: The uploaded file.
        check_only: Validate without saving.

    Returns:
        :class:`~app.tags.excel.ImportResult`.

    Raises:
        ImportFileError: If the file cannot be read or lacks the Name column.
    """
    columns, rows = read_rows(stream, ALIASES, required=("name",), sheet_name="Alarms")
    if not rows:
        raise ImportFileError(gettext('The sheet has no data rows.'))
    result = ImportResult(check_only=check_only)
    for number_, record in rows:
        action, alarm, message = _apply_row(record, columns)
        result.rows.append((number_, action, text(record.get("name")) or (alarm.name if alarm else ""), message))
    if result.errors or check_only:
        db.session.rollback()
    else:
        db.session.commit()
        result.saved = True
    return result
