"""Administration page of the Modbus TCP server (*Admin → Modbus TCP server*).

* ``GET  /admin/modbus-server/`` – settings, live status and the table of published tags.
* ``POST /admin/modbus-server/settings`` – save the settings.
* ``GET  /admin/modbus-server/api/status`` – server state, clients and the published values.
* ``GET  /admin/modbus-server/api/tags`` – tags that can still be published (tag picker).
* ``POST /admin/modbus-server/api/add`` – publish tags (addresses assigned automatically).
* ``POST /admin/modbus-server/api/<id>`` – change type, address, scale, STRING length or *Allow write*.
* ``POST /admin/modbus-server/api/remove`` – stop publishing tags.
* ``GET  /admin/modbus-server/addresses.xlsx`` / ``.csv`` – address list for the SCADA engineer.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from types import SimpleNamespace

from flask import Response, flash, jsonify, redirect, render_template, request, url_for

from app.extensions import db
from app.i18n.catalog import gettext
from app.i18n.forms import FlaskForm
from app.modbus_server import bp
from app.modbus_server.config import BAD_VALUE, MAX_INTERVAL, MIN_INTERVAL, ServerConfig
from app.modbus_server.encoding import MAX_STRING, ORDERS, TYPES, default_type, notation, register_count
from app.modbus_server.layout import area_of, check, check_config, next_free, reserved, used
from app.modbus_server.server import ModbusPublisher
from app.models import DataPoint, ModbusExport, Perm
from app.utils.decorators import permission_required
from app.utils.xlsx import build_workbook, send_xlsx
from wtforms import BooleanField, FloatField, IntegerField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, NumberRange, Optional


class ServerForm(FlaskForm):
    """Settings of the Modbus TCP server."""

    enabled = BooleanField("Enable the Modbus TCP server")
    host = StringField("Listen on (IP address)", default="0.0.0.0", validators=[DataRequired()],
                       description="0.0.0.0 = every network card of this computer, or the IP address of one card "
                                   "(e.g. the OT network).")
    port = IntegerField("TCP port", default=502, validators=[DataRequired(), NumberRange(min=1, max=65535)],
                        description="502 is the Modbus standard. On Linux ports below 1024 need administrator rights "
                                    "– use e.g. 5020.")
    unit_id = IntegerField("Unit id", default=0, validators=[Optional(), NumberRange(min=0, max=255)],
                           description="0 = answer every unit id (usual for Modbus TCP); 1–255 = only this one.")
    order = SelectField("Value order (32/64-bit)", choices=list(ORDERS.items()), default="ABCD",
                        description="Must match the setting in the SCADA's Modbus driver.")
    interval = FloatField("Update interval (s)", default=1.0,
                          validators=[DataRequired(), NumberRange(min=MIN_INTERVAL, max=MAX_INTERVAL)],
                          description="How often the published values are refreshed from the PLCs.")
    bad_value = SelectField("Tags without a valid value", choices=list(BAD_VALUE.items()), default="hold")
    allowed = TextAreaField("Allowed clients (optional)",
                            description="IP addresses or networks of the SCADA servers, separated by commas or new "
                                        "lines (e.g. 10.0.0.20, 10.0.1.0/24). Empty = every client may connect.")
    heartbeat = IntegerField("Heartbeat register (optional)", validators=[Optional(), NumberRange(min=0, max=65535)],
                             description="A counter that grows by 1 on every update – the SCADA can raise an alarm "
                                         "when it stops changing.")
    quality_base = IntegerField("Quality bits from bit (optional)",
                                validators=[Optional(), NumberRange(min=0, max=65535)],
                                description="One bit per published tag (in address order): ON = the value is valid.")
    submit = SubmitField("Save")

    def to_config(self) -> ServerConfig:
        """The form as :class:`ServerConfig`."""
        return ServerConfig(enabled=bool(self.enabled.data), host=(self.host.data or "").strip(),
                            port=self.port.data or 502, unit_id=self.unit_id.data or 0, order=self.order.data,
                            interval=self.interval.data or 1.0, bad_value=self.bad_value.data,
                            allowed=(self.allowed.data or "").strip(), heartbeat=self.heartbeat.data,
                            quality_base=self.quality_base.data)

    def fill(self, cfg: ServerConfig) -> None:
        """Show stored settings in the form."""
        for name in ("enabled", "host", "port", "unit_id", "order", "interval", "bad_value", "allowed", "heartbeat",
                     "quality_base"):
            getattr(self, name).data = getattr(cfg, name)


def _exports() -> list[ModbusExport]:
    """Published tags in address order (registers first)."""
    return ModbusExport.query.order_by(ModbusExport.area.desc(), ModbusExport.address).all()


@bp.route("/")
@permission_required(Perm.MANAGE_PLCS)
def index():
    """The Modbus TCP server page.

    Returns:
        Rendered page.
    """
    form = ServerForm()
    form.fill(ServerConfig.load())
    return render_template("modbus_server/index.html", form=form, exports=[e.to_dict() for e in _exports()],
                           types=TYPES, max_string=MAX_STRING)


@bp.route("/settings", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def save_settings():
    """Save the settings (the server applies them within one update interval).

    Returns:
        Redirect to the page, or the page with the errors.
    """
    form = ServerForm()
    if form.validate_on_submit():
        cfg = form.to_config()
        errors = {**cfg.validate(), **check_config(cfg, _exports())}
        if not errors:
            cfg.save()
            db.session.commit()
            flash(gettext("Modbus TCP server settings saved."), "success")
            return redirect(url_for("modbus_server.index"))
        for name, message in errors.items():
            field = getattr(form, name)
            field.errors = list(field.errors) + [message]
    return render_template("modbus_server/index.html", form=form, exports=[e.to_dict() for e in _exports()],
                           types=TYPES, max_string=MAX_STRING), 400


@bp.route("/api/status")
@permission_required(Perm.MANAGE_PLCS)
def api_status():
    """Server state and the values currently published.

    Returns:
        JSON ``{"status": {...}, "points": {export id: {"words": [...]} or {"bit": bool},
        plus "display" and "quality" of the tag}}``.
    """
    pub = ModbusPublisher.get()
    image = pub.image
    points = {}
    for exp in _exports():
        if exp.area == "bit":
            point = {"bit": image.bits.get(exp.address)} if exp.address in image.bits else {}
        else:
            words = [image.regs.get(a) for a in range(exp.address, exp.address + exp.count)]
            point = {"words": words} if None not in words else {}
        point.update(pub.values.get(exp.datapoint_id) or {})
        points[exp.id] = point
    return jsonify(status=pub.status(), points=points)


@bp.route("/api/tags")
@permission_required(Perm.MANAGE_PLCS)
def api_tags():
    """Tags that can be published (not yet published, not local dummy tags).

    Returns:
        JSON list ``[{"id", "label", "plc", "data_type", "unit", "mb_type"}]``.
    """
    published = {e.datapoint_id for e in ModbusExport.query.all()}
    out = []
    for dp in DataPoint.query.order_by(DataPoint.label).all():
        if dp.id in published or dp.is_local:
            continue
        out.append({"id": dp.id, "label": dp.label, "plc": dp.plc.name, "data_type": dp.data_type,
                    "unit": dp.unit or "", "mb_type": default_type(dp)[0]})
    return jsonify(out)


@bp.route("/api/add", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def api_add():
    """Publish tags; each gets the suggested type and the next free address.

    JSON body: ``{"ids": [datapoint ids]}``.

    Returns:
        JSON ``{"ok": true, "added": n, "exports": [...]}`` or an error (400).
    """
    ids = [int(i) for i in (request.get_json(silent=True) or {}).get("ids", []) if str(i).isdigit()]
    if not ids:
        return jsonify(ok=False, error=gettext("Select at least one tag.")), 400
    cfg = ServerConfig.load()
    exports = ModbusExport.query.all()
    published = {e.datapoint_id for e in exports}
    taken = used(exports)
    added = 0
    for dp in DataPoint.query.filter(DataPoint.id.in_(ids)).order_by(DataPoint.label).all():
        if dp.id in published or dp.is_local:
            continue
        mb_type, length = default_type(dp)
        area = area_of(mb_type)
        count = 1 if area == "bit" else register_count(mb_type, length)
        blocked = taken[area] | reserved(cfg, len(exports) + added + 1)[area]
        address = next_free(area, count, blocked)
        if address is None:
            db.session.rollback()
            return jsonify(ok=False, error=gettext("No free Modbus address left for {0}.", dp.label)), 400
        db.session.add(ModbusExport(datapoint=dp, area=area, address=address, mb_type=mb_type, scale=1.0,
                                    string_length=length))
        taken[area].update(range(address, address + count))
        added += 1
    db.session.commit()
    return jsonify(ok=True, added=added, exports=[e.to_dict() for e in _exports()])


@bp.route("/api/<int:export_id>", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def api_update(export_id: int):
    """Change the Modbus type, address, scale, STRING length or *Allow write* of a published tag.

    JSON body: any of ``mb_type``, ``address`` (empty = next free), ``scale``,
    ``string_length``, ``writable`` (other SCADA systems may write the tag –
    only for tags that are writable in pyPLCHMI).

    Returns:
        JSON ``{"ok": true, "export": {...}}`` or ``{"ok": false, "errors": [...]}`` (400).
    """
    exp = db.get_or_404(ModbusExport, export_id)
    data = request.get_json(silent=True) or {}
    errors = []
    mb_type = str(data.get("mb_type", exp.mb_type)).upper()
    if mb_type not in TYPES:
        errors.append(gettext("Unknown Modbus type {0}.", mb_type))
        mb_type = exp.mb_type
    text_tag = exp.datapoint.data_type in ("STRING", "CHAR")
    if mb_type == "STRING" and not text_tag:
        errors.append(gettext("Only text tags can be published as STRING."))
    elif mb_type != "STRING" and text_tag:
        errors.append(gettext("A text tag can only be published as STRING."))
    try:
        scale = float(data.get("scale", exp.scale))
        if scale == 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(gettext("The scale must be a number other than 0."))
        scale = exp.scale
    try:
        length = int(data.get("string_length", exp.string_length) or 0)
    except (TypeError, ValueError):
        length = -1
    if mb_type == "STRING" and not 1 <= length <= MAX_STRING:
        errors.append(gettext("STRING length must be 1 to {0}.", MAX_STRING))
    area = area_of(mb_type)
    exports = ModbusExport.query.all()
    cfg = ServerConfig.load()
    raw_address = data.get("address", exp.address if area == exp.area else "")
    if raw_address in ("", None):
        count = 1 if area == "bit" else register_count(mb_type, max(length, 0))
        address = next_free(area, count, used(exports, exclude_id=exp.id)[area] | reserved(cfg, len(exports))[area])
        if address is None:
            errors.append(gettext("No free Modbus address left."))
            address = 0
    else:
        try:
            address = int(raw_address)
        except (TypeError, ValueError):
            errors.append(gettext("The address must be a whole number."))
            address = exp.address
    writable = data.get("writable", exp.writable)
    writable = writable if isinstance(writable, bool) else str(writable).lower() in ("1", "true", "on", "yes")
    dp = exp.datapoint
    if writable and not exp.writable and (not dp.writable or dp.is_local):
        errors.append(gettext("The tag {0} is read only in pyPLCHMI – make it writable first (Tag table → Edit → "
                              "Writable), then allow writing here.", dp.label))
    if errors:
        return jsonify(ok=False, errors=errors), 400
    # A plain object: a new ModbusExport linked to the tag would replace the saved row.
    candidate = SimpleNamespace(id=exp.id, area=area, address=address, mb_type=mb_type,
                                string_length=length if mb_type == "STRING" else 0)
    problems = check(candidate, exports, cfg)
    if problems:
        return jsonify(ok=False, errors=problems), 400
    exp.area, exp.address, exp.mb_type, exp.scale = area, address, mb_type, scale
    exp.string_length = length if mb_type == "STRING" else 0
    exp.writable = bool(writable)
    db.session.commit()
    return jsonify(ok=True, export=exp.to_dict())


@bp.route("/api/remove", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def api_remove():
    """Stop publishing tags.

    JSON body: ``{"ids": [export ids]}``.

    Returns:
        JSON ``{"ok": true, "removed": n}``.
    """
    ids = [int(i) for i in (request.get_json(silent=True) or {}).get("ids", []) if str(i).isdigit()]
    rows = ModbusExport.query.filter(ModbusExport.id.in_(ids)).all() if ids else []
    for row in rows:
        db.session.delete(row)
    db.session.commit()
    return jsonify(ok=True, removed=len(rows))


# ---------------------------------------------------------------------------
# Address list
# ---------------------------------------------------------------------------
COLUMNS = [("Tag", 30), ("PLC", 16), ("Unit", 8), ("Tag data type", 12), ("Modbus type", 12), ("Area", 18),
           ("Address (0-based)", 10), ("Registers / bits", 9), ("Holding / coil (1-based)", 14),
           ("Input reg. / discrete input", 14), ("Scale", 8), ("Value order", 10), ("STRING length", 9),
           ("Access", 12)]
HELP = [
    ("Area", "Registers: read with function 3 (holding registers, 4xxxx) or 4 (input registers, 3xxxx) – both "
             "return the same values. Bits: function 1 (coils, 0xxxx) or 2 (discrete inputs, 1xxxx)."),
    ("Address (0-based)", "Address in the Modbus telegram (protocol address). Many SCADA drivers use it directly."),
    ("Holding / coil (1-based)", "The same address in the classic 1-based notation (40001 = register 0)."),
    ("Modbus type", "INT16/UINT16 = 1 register, INT32/UINT32/FLOAT32 = 2, FLOAT64 = 4, STRING = 2 characters per "
                    "register, BIT = one coil / discrete input, BOOL16 = 0/1 in a register."),
    ("Scale", "The value is multiplied by this number before it is sent (e.g. 10: 21.5 is sent as 215)."),
    ("Value order", "Order of the 16-bit registers / bytes of 32/64-bit values – set the same in the SCADA."),
    ("Access", "Read only, or Read / write: the SCADA may write the tag with function 6/16 (registers – all registers "
               "of a value in one request) or 5/15 (bits). Writes to other addresses get exception 02, invalid "
               "values 03, a write the PLC refuses 04. Every write is in the audit log."),
]


def _address_rows(cfg: ServerConfig) -> list[list]:
    """Rows of the address list (published tags, then the heartbeat and quality bits)."""
    rows = []
    exports = _exports()
    for exp in exports:
        dp = exp.datapoint
        rows.append([dp.label, dp.plc.name, dp.unit or "", dp.data_type, exp.mb_type,
                     "Bit (coil / discrete input)" if exp.area == "bit" else "Register (holding / input)",
                     exp.address, exp.count, notation(exp.area, exp.address), notation(exp.area, exp.address, True),
                     exp.scale, cfg.order if exp.area == "reg" and exp.count > 1 else "",
                     exp.string_length or "", "Read / write" if exp.write_ok else "Read only"])
    if cfg.heartbeat is not None:
        rows.append(["(heartbeat counter)", "", "", "", "UINT16", "Register (holding / input)", cfg.heartbeat, 1,
                     notation("reg", cfg.heartbeat), notation("reg", cfg.heartbeat, True), 1, "", "", "Read only"])
    if cfg.quality_base is not None:
        ordered = sorted(exports, key=lambda e: (e.area, e.address))
        for index, exp in enumerate(ordered):
            bit = cfg.quality_base + index
            rows.append([f"(quality) {exp.datapoint.label}", "", "", "", "BIT", "Bit (coil / discrete input)", bit, 1,
                         notation("bit", bit), notation("bit", bit, True), "", "", "", "Read only"])
    return rows


@bp.route("/addresses.xlsx")
@permission_required(Perm.MANAGE_PLCS)
def addresses_xlsx():
    """Download the address list as an Excel file."""
    cfg = ServerConfig.load()
    return send_xlsx(build_workbook("Modbus addresses", COLUMNS, _address_rows(cfg), HELP), "modbus-addresses")


@bp.route("/addresses.csv")
@permission_required(Perm.MANAGE_PLCS)
def addresses_csv():
    """Download the address list as CSV (UTF-8 with BOM, opens correctly in Excel)."""
    cfg = ServerConfig.load()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([name for name, _ in COLUMNS])
    writer.writerows(_address_rows(cfg))
    name = f"modbus-addresses-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
