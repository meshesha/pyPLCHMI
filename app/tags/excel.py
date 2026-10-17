"""Excel export and import of the tag table.

Export: one row per tag (see :data:`COLUMNS`) plus a *Help* sheet.

Import (create or update):

* a row whose **ID** is an existing tag updates that tag; any other row
  (empty or unknown ID) creates a new tag;
* columns that are missing from the sheet keep the tag's current values
  (or the defaults for new tags); an empty cell clears optional values;
* every row is validated exactly like the tag form (address syntax of the
  PLC's protocol, widget rules…); dummy PLC tags need no address;
* **Screens** (if the column exists) lists the screens showing the tag,
  separated by commas – the tag is added to / removed from screens to match;
* the import is all-or-nothing: if any row has an error nothing is saved, and
  *check only* validates without saving.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from werkzeug.datastructures import MultiDict

from app.tags.forms import TagForm
from app.extensions import db
from app.models import PLC, Dashboard, DataPoint, Role
from app.utils.xlsx import ImportFileError, boolean, build_workbook, number, read_rows, text, yes_no
from app.i18n.catalog import gettext

#: ``(header, width)`` of the exported sheet, in order.
COLUMNS = [
    ("ID", 7), ("Label", 28), ("PLC", 18), ("Address", 16), ("Data type", 11), ("String length", 9),
    ("Unit", 8), ("Scale", 8), ("Offset", 8), ("Decimals", 9), ("Min", 9), ("Max", 9),
    ("Text TRUE", 11), ("Text FALSE", 11), ("Writable", 9), ("Widget", 10), ("Tile width", 9), ("Screens", 34),
    ("Dummy kind", 11), ("History", 9), ("History interval (s)", 12), ("Write roles", 18),
]

HELP = [
    ("ID", "Tag id. Keep it to update that tag when importing; leave it empty (or delete the column) to create new tags."),
    ("Label", "Name of the tag (required)."),
    ("PLC", "PLC / device name exactly as in Admin → PLCs (required)."),
    ("Address", "Address in the PLC's syntax: S7 DB1.DBD4, M10.5 · Modbus HR100, C5, 40001 · BACnet AV3. "
                "A :TYPE suffix (DB1.DBD4:REAL) overrides the data type. Empty for Dummy PLCs."),
    ("Data type", "BOOL, BYTE, INT, UINT, DINT, UDINT, WORD, DWORD, REAL, LREAL, STRING, CHAR, TIME, DTL…"),
    ("String length", "Maximum length for STRING tags (1–254)."),
    ("Unit", "Unit shown after the value, e.g. °C."),
    ("Scale / Offset", "Displayed value = raw × Scale + Offset (Scale cannot be 0)."),
    ("Decimals", "Decimals shown (0–6)."),
    ("Min / Max", "Range for gauges and bars (optional)."),
    ("Text TRUE / FALSE", "Texts for BOOL values, e.g. RUNNING / STOPPED."),
    ("Writable", "yes = operators may write the value (needs the write_values permission)."),
    ("Widget", "Grid screen tile: value, gauge, indicator (BOOL), switch (BOOL, writable)."),
    ("Tile width", "Grid columns 1–4."),
    ("Screens", "Screens showing the tag, separated by commas (empty = none). If the column is missing, "
                "screens are not changed."),
    ("Dummy kind", "Dummy PLC tags only: general (same value everywhere, stored in the database) or local "
                   "(own value in each browser window). Empty = general; ignored for real PLCs."),
    ("History", "yes = save samples of the value (Trends → Tag history, trend charts). Not possible for local "
                "dummy tags."),
    ("History interval (s)", "Seconds between two history samples (1–86400, default 60)."),
    ("Write roles", "Who may write a writable tag: empty = everyone allowed to write values; otherwise role "
                    "names separated by commas (e.g. Operator, Admin) – only those roles may write. If the "
                    "column is missing, the setting is not changed."),
]

#: Accepted header spellings (lower case) -> internal field.
ALIASES = {
    "id": "id", "label": "label", "name": "label", "tag": "label", "plc": "plc", "device": "plc",
    "address": "address", "data type": "data_type", "type": "data_type", "datatype": "data_type",
    "string length": "string_length", "unit": "unit", "scale": "scale", "offset": "offset",
    "decimals": "decimals", "min": "min_value", "max": "max_value", "text true": "true_text",
    "text false": "false_text", "writable": "writable", "write roles": "write_roles",
    "write role": "write_roles", "write access": "write_roles", "widget": "widget", "tile width": "width",
    "width": "width", "screens": "screens", "screen": "screens", "dummy kind": "dummy_scope",
    "dummy scope": "dummy_scope", "scope": "dummy_scope", "kind": "dummy_scope",
    "history": "log_enabled", "log": "log_enabled", "logging": "log_enabled",
    "history interval (s)": "log_interval", "history interval": "log_interval", "log interval": "log_interval",
    "sample interval": "log_interval",
}


def _row(dp: DataPoint) -> list:
    """Export values of one tag.

    Args:
        dp: The tag.

    Returns:
        Row values in :data:`COLUMNS` order.
    """
    address = "" if dp.protocol == "dummy" else dp.address
    return [dp.id, dp.label, dp.plc.name if dp.plc else "", address, dp.data_type,
            dp.string_length if dp.data_type == "STRING" else None, dp.unit, dp.scale, dp.offset, dp.decimals,
            dp.min_value, dp.max_value, dp.true_text, dp.false_text, yes_no(dp.writable), dp.widget, dp.width,
            ", ".join(s.name for s in dp.screens), dp.scope_label, yes_no(dp.log_enabled), dp.log_interval,
            ", ".join(r.name for r in dp.write_roles) if dp.limited_write else ""]


def export_tags(tags) -> bytes:
    """Build the .xlsx file for a list of tags.

    Args:
        tags: Iterable of :class:`DataPoint`.

    Returns:
        Workbook bytes.
    """
    return build_workbook("Tags", COLUMNS, (_row(dp) for dp in tags), HELP)


@dataclass
class ImportResult:
    """Outcome of an import.

    Attributes:
        rows: ``(excel row, action, label, message)`` with action ``created``,
            ``updated``, ``unchanged`` or ``error``.
        saved: ``True`` if the changes were committed.
        check_only: The import ran in *check only* mode.
    """

    rows: list = field(default_factory=list)
    saved: bool = False
    check_only: bool = False

    @property
    def errors(self) -> int:
        """Number of rows with errors."""
        return sum(1 for r in self.rows if r[1] == "error")

    def count(self, action: str) -> int:
        """Number of rows with an action.

        Args:
            action: ``created`` / ``updated`` / ``unchanged`` / ``error``.

        Returns:
            Count.
        """
        return sum(1 for r in self.rows if r[1] == action)


def _current_fields(dp: DataPoint | None) -> dict:
    """Form fields of an existing tag (or the defaults for a new one).

    Args:
        dp: Existing tag or ``None``.

    Returns:
        ``field -> value`` for :class:`~app.tags.forms.TagForm`.
    """
    if dp is None:
        return {"widget": "value", "scale": 1, "offset": 0, "decimals": 2, "width": 1, "true_text": "ON",
                "false_text": "OFF", "string_length": 20, "data_type": "INT", "unit": "", "writable": False,
                "dummy_scope": "general", "log_enabled": False, "log_interval": 60, "write_access": "all",
                "write_roles": []}
    return {"label": dp.label, "plc_id": dp.plc_id, "address": "" if dp.protocol == "dummy" else dp.address,
            "data_type": dp.data_type, "string_length": dp.string_length or 20, "unit": dp.unit, "scale": dp.scale,
            "offset": dp.offset, "decimals": dp.decimals, "min_value": dp.min_value, "max_value": dp.max_value,
            "true_text": dp.true_text, "false_text": dp.false_text, "writable": dp.writable, "widget": dp.widget,
            "width": dp.width, "dummy_scope": dp.dummy_scope or "general", "log_enabled": dp.log_enabled,
            "log_interval": dp.log_interval or 60, "write_access": dp.write_access or "all",
            "write_roles": [r.id for r in dp.write_roles]}


#: Form fields that need whole numbers.
_INTEGER_FIELDS = ("decimals", "width", "string_length", "log_interval")


def _formdata(values: dict) -> MultiDict:
    """Turn field values into form data (unchecked checkbox = absent, ``None`` = empty).

    Args:
        values: ``field -> value``.

    Returns:
        ``MultiDict`` for :class:`~app.tags.forms.TagForm`.
    """
    data = MultiDict()
    for key, value in values.items():
        if key == "write_roles":  # several values (one per role id)
            for role_id in value or []:
                data.add(key, str(role_id))
            continue
        if key in ("writable", "log_enabled"):
            if value:
                data[key] = "y"
            continue
        if isinstance(value, float) and value.is_integer() and key in _INTEGER_FIELDS:
            value = int(value)  # "10" read as 10.0 must stay a valid integer
        data[key] = "" if value is None else str(value)
    return data


def _apply_row(record: dict, columns: set, plcs: dict, screens: dict,
               roles: dict | None = None) -> tuple[str, DataPoint | None, str]:
    """Validate one sheet row and apply it to a new or existing tag (not committed).

    Args:
        record: Row values by internal field name.
        columns: Fields present in the sheet.
        plcs: ``lower-case name -> PLC``.
        screens: ``lower-case name -> Dashboard``.
        roles: ``lower-case name -> Role`` (for the *Write roles* column).

    Returns:
        ``(action, tag, message)`` – action ``created``/``updated``/``unchanged``/``error``.
    """
    try:
        tag_id = number(record.get("id")) if "id" in columns else None
    except ValueError:
        return "error", None, gettext("ID '{0}' is not a number", record.get('id'))
    existing = db.session.get(DataPoint, int(tag_id)) if tag_id else None
    values = _current_fields(existing)
    try:
        if "label" in columns:
            values["label"] = text(record.get("label"))
        if "plc" in columns:
            name = text(record.get("plc"))
            plc = plcs.get(name.lower())
            if plc is None:
                return "error", None, f"PLC '{name}' does not exist" if name else "PLC is empty"
            values["plc_id"] = plc.id
        for key in ("address", "data_type", "unit", "true_text", "false_text", "widget"):
            if key in columns:
                values[key] = text(record.get(key)).upper() if key == "data_type" else text(record.get(key))
        for key in ("string_length", "scale", "offset", "decimals", "min_value", "max_value", "width"):
            if key in columns:
                values[key] = number(record.get(key))
        if "writable" in columns:
            values["writable"] = boolean(record.get("writable"))
        if "log_enabled" in columns:
            values["log_enabled"] = boolean(record.get("log_enabled"))
        if "log_interval" in columns:
            values["log_interval"] = number(record.get("log_interval"))
        if "dummy_scope" in columns:
            values["dummy_scope"] = text(record.get("dummy_scope")).lower() or "general"
        if "write_roles" in columns:
            names = [n.strip() for n in text(record.get("write_roles")).replace(";", ",").split(",") if n.strip()]
            unknown = [n for n in names if n.lower() not in (roles or {})]
            if unknown:
                return "error", None, gettext('Unknown role(s): {0}', ', '.join(unknown))
            values["write_access"] = "roles" if names else "all"
            values["write_roles"] = [roles[n.lower()].id for n in names]
        wanted = None
        if "screens" in columns:
            names = [n.strip() for n in text(record.get("screens")).replace(";", ",").split(",") if n.strip()]
            unknown = [n for n in names if n.lower() not in screens]
            if unknown:
                return "error", None, gettext('Unknown screen(s): {0}', ', '.join(unknown))
            wanted = {screens[n.lower()] for n in names}
    except ValueError as exc:
        return "error", None, str(exc)

    for key, default in (("scale", 1), ("offset", 0), ("decimals", 2), ("width", 1), ("string_length", 20),
                         ("log_interval", 60)):
        if values.get(key) is None:
            values[key] = default
    if not values.get("widget"):
        values["widget"] = "value"
    form = TagForm(formdata=_formdata(values), meta={"csrf": False},
                         current_plc_id=existing.plc_id if existing else None, datapoint=existing)
    if not form.validate():
        problems = "; ".join(f"{k.replace('_', ' ')}: {', '.join(v)}" for k, v in form.errors.items())
        return "error", None, problems
    dp = existing or DataPoint()
    roles_before = {r.id for r in dp.write_roles} if existing is not None else set()
    if existing is None:
        db.session.add(dp)
    form.populate_datapoint(dp)
    changed = existing is None or db.session.is_modified(dp, include_collections=False) \
        or {r.id for r in dp.write_roles} != roles_before
    if wanted is not None:
        for link in list(dp.screen_links):
            if link.dashboard not in wanted:
                link.dashboard.remove_tag(dp)
                changed = True
        for screen in wanted:
            if not screen.has_tag(dp):
                screen.add_tag(dp)
                changed = True
    db.session.flush()
    action = "created" if existing is None else ("updated" if changed else "unchanged")
    return action, dp, dp.address if dp.protocol != "dummy" else f"{dp.scope_label} dummy tag"


def import_tags(stream, check_only: bool = False) -> ImportResult:
    """Import tags from an uploaded .xlsx file.

    Args:
        stream: The uploaded file.
        check_only: Validate without saving.

    Returns:
        :class:`ImportResult`.

    Raises:
        ImportFileError: If the file cannot be read or lacks the Label/PLC columns.
    """
    columns, rows = read_rows(stream, ALIASES, required=("label", "plc"), sheet_name="Tags")
    if not rows:
        raise ImportFileError(gettext('The sheet has no data rows.'))
    plcs = {p.name.lower(): p for p in PLC.query.all()}
    screens = {d.name.lower(): d for d in Dashboard.query.all()}
    roles = {r.name.lower(): r for r in Role.query.all()}
    result = ImportResult(check_only=check_only)
    for number_, record in rows:
        action, dp, message = _apply_row(record, columns, plcs, screens, roles)
        label = text(record.get("label")) or (dp.label if dp else "")
        result.rows.append((number_, action, label, message))
    if result.errors or check_only:
        db.session.rollback()
    else:
        db.session.commit()
        result.saved = True
    return result
