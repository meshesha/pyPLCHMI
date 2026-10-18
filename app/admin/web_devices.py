"""*Add device* wizard for web devices (HTTP/HTTPS or WebSocket, JSON or XML).

Workflow (page ``admin/web_device.html`` + ``static/js/web-device.js``):

1. **Device** – name, protocol, address (URL), data format, request interval
   and timeout (plus advanced options: HTTP request mode, certificate check,
   message for WebSocket devices).
2. **Test** – :func:`api_test` performs one exchange with the device and
   returns the received data (shown formatted) and every key found in it.
3. **Process** – the keys are shown in a table: a checkbox, a suggested tag
   name (unique and valid under the tag naming rules), the data type and
   the value.  Names can be edited; :func:`api_check_names` checks them live.
4. **Save Selected / Save All** – :func:`api_save` creates the device (when
   new) and one read-only tag per selected key, all in one transaction:
   if any row is invalid nothing is saved and the problems are shown per row.

The same page opened for a saved device (``/admin/plcs/<id>/web``) tests
the saved settings and adds tags for keys that have none yet.
"""

from __future__ import annotations

import re

from flask import abort, flash, jsonify, render_template, request, url_for
from flask_login import current_user

from app.admin import bp
from app.extensions import db
from app.i18n.catalog import gettext
from app.models import PLC, Alarm, DataPoint, Perm
from app.plc.driver import get_manager
from app.plc.web import (CONTENT_TYPES, HTTP_MODES, MAX_BODY, MAX_HEADERS_TEXT, MAX_INTERVAL, MAX_MESSAGE,
                         MAX_TIMEOUT, MIN_INTERVAL, MIN_TIMEOUT, WebSettings, parse_web_address, probe)
from app.plc.web_client import example_url
from app.plc.web_data import FORMATS, MAX_KEYS, MAX_STRING, WEB_TYPES, WebDataError, check_path
from app.tags import naming
from app.utils.decorators import permission_required


def _payload() -> dict:
    """The JSON body of an API request (``{}`` when missing or invalid)."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _web_plc(plc_id) -> PLC | None:
    """The saved web device with this id, or ``None``."""
    if plc_id in (None, "", 0):
        return None
    try:
        plc = db.session.get(PLC, int(plc_id))
    except (TypeError, ValueError):
        return None
    return plc if plc is not None and plc.is_web else None


def path_to_name(path: str) -> str:
    """Turn a key path into a readable tag name part.

    ``meter.phases[0]`` → ``meter.phases_0``; ``status/sensor[2]/@id`` →
    ``status.sensor_2.id``; ``data["Flow rate"]`` → ``data.Flow_rate``.

    Args:
        path: Key path.
    """
    text = re.sub(r'\["((?:[^"\\]|\\.)*)"\]', lambda m: "." + m.group(1), path or "")
    text = re.sub(r"\[(\d+)\]", r"_\1", text)
    text = text.replace("/", ".").replace("@", "")
    return "value" if text in ("", "$") else text


def suggest_name(device: str, path: str, taken: set) -> str:
    """A unique, valid tag name for a key: ``<device>.<key>`` (made valid, ``_2``… when taken).

    Args:
        device: Device name (prefix).
        path: Key path.
        taken: Lower-case names already suggested in this table (updated).
    """
    base = naming.normalize(device) + "." + naming.normalize(path_to_name(path)) if device else path_to_name(path)
    name = naming.suggest(base, taken=taken)
    taken.add(name.lower())
    return name


def _name_problems(names: list[str]) -> list[list[str]]:
    """Problems of each name: the tag naming rules plus duplicates inside the list.

    Args:
        names: Proposed tag names (table order).

    Returns:
        One list of messages per name (empty = valid).
    """
    seen: dict[str, int] = {}
    out = []
    for index, raw in enumerate(names):
        name = (raw or "").strip()
        problems = naming.check(name)
        key = name.lower()
        if not problems and key in seen:
            problems = [gettext("The name '{0}' is used twice in this table (row {1}).", name, seen[key] + 1)]
        seen.setdefault(key, index)
        out.append(problems)
    return out


def _allowed() -> bool:
    """Managing devices and creating tags needs both permissions."""
    return current_user.can(Perm.MANAGE_PLCS) and current_user.can(Perm.MANAGE_SCREENS)


@bp.route("/plcs/web/new")
@permission_required(Perm.MANAGE_PLCS)
def web_device_new():
    """The *Add device* wizard for a new HTTP / WebSocket device.

    Returns:
        The wizard page.
    """
    return render_template("admin/web_device.html", plc=None, settings=WebSettings(), **_choices())


@bp.route("/plcs/<int:plc_id>/web")
@permission_required(Perm.MANAGE_PLCS)
def web_device_tags(plc_id: int):
    """The wizard for a saved web device: test it and add tags for more keys.

    Args:
        plc_id: Id of an HTTP or WebSocket device.

    Returns:
        The wizard page (404 for other devices).
    """
    plc = db.get_or_404(PLC, plc_id)
    if not plc.is_web:
        abort(404)
    return render_template("admin/web_device.html", plc=plc, settings=WebSettings.from_plc(plc), **_choices())


def _choices() -> dict:
    """Choices and limits used by the wizard template."""
    return {"formats": FORMATS, "http_modes": HTTP_MODES, "web_types": WEB_TYPES, "content_types": CONTENT_TYPES,
            "limits": {"interval": (MIN_INTERVAL, MAX_INTERVAL), "timeout": (MIN_TIMEOUT, MAX_TIMEOUT),
                       "message": MAX_MESSAGE, "string": MAX_STRING, "body": MAX_BODY,
                       "headers": MAX_HEADERS_TEXT},
            "examples": {"http": example_url("http"), "websocket": example_url("websocket")}}


@bp.route("/plcs/web/api/test", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def web_device_test():
    """Test a web device and list the keys of its data.

    JSON body: the device settings (``protocol``, ``url``, ``data_format``,
    ``poll_interval``, ``timeout``, ``http_mode``, ``verify_tls``,
    ``ws_message``, ``http_method``, ``http_body``, ``http_content_type``,
    ``http_headers``) and ``name`` – or ``plc_id`` of a saved device (its saved
    settings are used).

    Returns:
        JSON ``{"ok": true, "summary", "raw", "rows", "truncated"}`` where each
        row also has ``name`` (suggested tag name) and ``existing`` (name of
        the device's tag that already reads this key, or ``null``);
        ``{"ok": false, "error", "raw"}`` when the device fails; or
        ``{"ok": false, "errors": {field: message}}`` (HTTP 400) for invalid settings.
    """
    data = _payload()
    plc = _web_plc(data.get("plc_id"))
    if plc is not None:
        settings, device = WebSettings.from_plc(plc), plc.name
    else:
        settings, errors = WebSettings.from_dict(data)
        if errors:
            return jsonify(ok=False, errors=errors), 400
        device = str(data.get("name") or "").strip()
    result = probe(settings)
    if not result.get("ok"):
        return jsonify(result)
    existing = {}
    if plc is not None:
        for dp in plc.datapoints:
            existing.setdefault(dp.path, dp.label)
    taken: set = set()
    for row in result["rows"]:
        row["existing"] = existing.get(row["path"])
        row["name"] = suggest_name(device, row["path"], taken)
    return jsonify(result)


@bp.route("/plcs/web/api/check-names", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def web_device_check_names():
    """Check the tag names of the *Process* table while they are typed.

    JSON body: ``{"names": [...]}``.

    Returns:
        JSON ``{"problems": [[messages], …]}`` – one list per name.
    """
    names = _payload().get("names") or []
    if not isinstance(names, list):
        names = []
    return jsonify(problems=_name_problems([str(n) for n in names[:MAX_KEYS]]))


@bp.route("/plcs/web/api/save", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def web_device_save():
    """Create the device (when new) and a read-only tag for each chosen key.

    JSON body: the device settings and ``name``, ``description`` (new device)
    or ``plc_id`` (saved device), and ``rows`` – ``[{"path", "name",
    "data_type", "string_length"}, …]``.

    Nothing is saved when anything is invalid.

    Returns:
        JSON ``{"ok": true, "created", "plc_id", "tags_url", "message"}`` or
        HTTP 400 with ``{"ok": false, "error"?, "errors"?: {field: message},
        "rows"?: {index: [messages]}}``.
    """
    if not _allowed():
        return jsonify(ok=False, error=gettext("Creating tags needs the permission to manage screens and tags.")), 403
    data = _payload()
    plc = _web_plc(data.get("plc_id"))
    errors: dict[str, str] = {}
    if plc is None:
        settings, errors = WebSettings.from_dict(data)
        name = str(data.get("name") or "").strip()
        if not name:
            errors["name"] = gettext("Enter a name for the device.")
        elif len(name) > 64:
            errors["name"] = gettext("The name is too long (at most {0} characters).", 64)
        elif PLC.query.filter_by(name=name).first() is not None:
            errors["name"] = gettext("A PLC with this name already exists.")
        description = str(data.get("description") or "").strip()[:255]
    else:
        settings = WebSettings.from_plc(plc)
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    if not rows:
        return jsonify(ok=False, errors=errors, error=gettext("Select at least one key.")), 400
    if len(rows) > MAX_KEYS:
        return jsonify(ok=False, errors=errors, error=gettext("At most {0} keys at once.", MAX_KEYS)), 400

    names = [str((r or {}).get("name") or "").strip() if isinstance(r, dict) else "" for r in rows]
    row_errors: dict[int, list[str]] = {}
    for index, problems in enumerate(_name_problems(names)):
        if problems:
            row_errors[index] = list(problems)
    addresses = []
    for index, row in enumerate(rows):
        row = row if isinstance(row, dict) else {}
        dtype = str(row.get("data_type") or "LREAL").upper()
        try:
            length = int(row.get("string_length") or 0)
        except (TypeError, ValueError):
            length = 0
        try:
            if dtype == "STRING" and not 1 <= length <= MAX_STRING:
                raise WebDataError(gettext("STRING length must be 1 to {0}.", MAX_STRING))
            addr = parse_web_address(str(row.get("path") or ""), dtype, length or None)
            check_path(addr.path, settings.data_format)
            addresses.append(addr)
        except WebDataError as exc:
            row_errors.setdefault(index, []).append(str(exc))
            addresses.append(None)
    if errors or row_errors:
        return jsonify(ok=False, errors=errors, rows={str(k): v for k, v in row_errors.items()},
                       error=gettext("Nothing was saved – correct the marked fields.")), 400

    new_device = plc is None
    if new_device:
        plc = PLC(name=name, description=description, enabled=True, offline_delay=10.0)
        settings.apply(plc)
        db.session.add(plc)
        db.session.flush()
        Alarm.sync_comm_alarm(plc, True, "high")
    for label, addr in zip(names, addresses):
        dp = DataPoint(plc=plc, label=label, widget="indicator" if addr.data_type == "BOOL" else "value",
                       unit="", scale=1.0, offset=0.0, writable=False, width=1,
                       decimals=0 if addr.data_type not in ("REAL", "LREAL") else 2)
        dp.set_address(addr)
        db.session.add(dp)
    db.session.commit()
    get_manager().disconnect(plc.id)
    message = gettext("Device '{0}' added with {1} tag(s).", plc.name, len(names)) if new_device else \
        gettext("{0} tag(s) added to '{1}'.", len(names), plc.name)
    flash(message, "success")
    return jsonify(ok=True, created=len(names), plc_id=plc.id, message=message,
                   tags_url=url_for("tags.index", plc=plc.id), device_url=url_for("admin.web_device_tags", plc_id=plc.id))

