"""Faceplate pages and API.

Administration (permission ``manage_screens``), under ``/faceplates``:

* ``GET  /``                                – device types (faceplates).
* ``GET/POST /new``, ``/<id>/edit``         – a device type: name, template screen, reference prefix.
* ``POST /<id>/delete``                     – delete a type and its instances.
* ``GET  /<id>``                            – a type: parameters and devices (instances).
* ``GET/POST /<id>/instances/new``, ``/instances/<iid>/edit`` – a device: name, prefix,
  hand-made bindings, optionally create its tags.
* ``POST /instances/<iid>/delete``           – delete a device.
* ``GET  /<id>/scan`` / ``POST /<id>/scan`` – find devices among the tags / create them.
* ``GET  /<id>/instances.xlsx``, ``POST /<id>/instances/import`` – Excel list of devices.
* ``GET  /api/preview``                     – parameters of a screen + prefix (type form).

Run time (everybody who may open the template screen):

* ``GET /devices``                          – all devices, each with *Open*.
* ``GET /api/instances/<iid>``              – what a pop-up window needs.
* ``GET /api/instances/<iid>/values``       – live values of the window.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from wtforms import BooleanField, IntegerField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from types import SimpleNamespace

from app.dashboards.icons import shapes_for_layout as icon_shapes_for_layout
from app.extensions import db
from app.faceplates import bp
from app.faceplates.binding import (check_prefix, create_tags, fixed_tags, instance_name, natural_key, parameters,
                                    point_map, resolve, scan, substitute_layout, suggest_prefix, tag_index)
from app.i18n.catalog import gettext
from app.i18n.forms import FlaskForm
from app.models import PLC, Dashboard, DataPoint, Faceplate, FaceplateInstance, Perm
from app.plc.service import read_datapoints
from app.utils.decorators import permission_required
from app.utils.xlsx import ImportFileError, boolean, build_workbook, read_rows, send_xlsx, text
from app.utils.xlsx import number as number_value


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------
class FaceplateForm(FlaskForm):
    """A device type."""

    name = StringField("Name", validators=[DataRequired(), Length(max=100)],
                       description="The kind of device, e.g. Electricity meter.")
    screen_id = SelectField("Template screen", coerce=int, validators=[DataRequired()],
                            description="A Plane screen drawn with the tags of one device (the reference device).")
    prefix = StringField("Tag prefix of the reference device", validators=[DataRequired(), Length(max=90)],
                         description="The common beginning of the reference device's tag names, e.g. "
                                     "Panel_01.MeterA. – the rest of each name is the parameter "
                                     "(Panel_01.MeterA.voltage → voltage).")
    description = TextAreaField("Description", validators=[Optional(), Length(max=500)])
    submit = SubmitField("Save")

    def __init__(self, *args, faceplate: Faceplate | None = None, **kwargs):
        """Fill the screen choices (Plane screens only)."""
        super().__init__(*args, **kwargs)
        self._faceplate = faceplate
        screens = Dashboard.query.filter_by(kind="image").order_by(Dashboard.name).all()
        self.screen_id.choices = [(0, gettext("— choose a Plane screen —"))] + [(d.id, d.name) for d in screens]

    def validate(self, extra_validators=None) -> bool:
        """Unique name, an existing Plane screen and a valid prefix that matches some of its tags."""
        ok = super().validate(extra_validators=extra_validators)
        name = (self.name.data or "").strip()
        other = Faceplate.query.filter(db.func.lower(Faceplate.name) == name.lower()).first()
        if other is not None and (self._faceplate is None or other.id != self._faceplate.id):
            self.name.errors = list(self.name.errors) + [gettext("A device type with this name already exists.")]
            ok = False
        screen = db.session.get(Dashboard, self.screen_id.data or 0)
        if screen is None or not screen.is_image:
            self.screen_id.errors = list(self.screen_id.errors) + [gettext("Choose a Plane screen.")]
            return False
        problems = check_prefix(self.prefix.data)
        if problems:
            self.prefix.errors = list(self.prefix.errors) + problems
            return False
        probe = SimpleNamespace(screen=screen, prefix=self.prefix.data.strip())
        if not parameters(probe):
            self.prefix.errors = list(self.prefix.errors) + [
                gettext("No tag of the screen starts with {0} – use the prefix of the tags the screen shows "
                        "(suggestion: {1}).", self.prefix.data.strip(), suggest_prefix(screen.datapoints) or "—")]
            ok = False
        return ok


class InstanceForm(FlaskForm):
    """A device (instance)."""

    name = StringField("Name", validators=[DataRequired(), Length(max=100)],
                       description="Shown as the title of the window, e.g. Panel 37 · Meter B.")
    prefix = StringField("Tag prefix", validators=[DataRequired(), Length(max=90)],
                         description="The beginning of this device's tag names, e.g. Panel_37.MeterB.")
    description = TextAreaField("Description", validators=[Optional(), Length(max=500)])
    create_tags = BooleanField("Create the missing tags by copying the reference device's tags")
    plc_id = SelectField("PLC of the new tags", coerce=int, validators=[Optional()],
                         description="Same PLC as the reference tags, or another PLC of the same kind (e.g. one "
                                     "Modbus device per meter with the same register map).")
    db_shift = IntegerField("DB number shift", default=0, validators=[Optional(), NumberRange(min=-65535, max=65535)],
                            description="S7: added to the DB number (each device in its own DB: DB50 → DB51 = 1).")
    start_shift = IntegerField("Address shift", default=0,
                               validators=[Optional(), NumberRange(min=-4194303, max=4194303)],
                               description="Added to the start address: S7 byte offset (e.g. 20 bytes per meter in "
                                           "one DB), Modbus register / coil number, BACnet object instance.")
    submit = SubmitField("Save")

    def __init__(self, *args, faceplate: Faceplate, instance: FaceplateInstance | None = None, **kwargs):
        """Fill the PLC choices."""
        super().__init__(*args, **kwargs)
        self._faceplate, self._instance = faceplate, instance
        self.plc_id.choices = [(0, gettext("Same PLC as the reference tags"))] + \
            [(p.id, f"{p.name} ({p.protocol_label})") for p in PLC.query.order_by(PLC.name)]

    def validate(self, extra_validators=None) -> bool:
        """Unique name within the type, valid prefix, not the reference prefix twice."""
        ok = super().validate(extra_validators=extra_validators)
        name = (self.name.data or "").strip()
        for other in self._faceplate.instances:
            if other.name.lower() == name.lower() and (self._instance is None or other.id != self._instance.id):
                self.name.errors = list(self.name.errors) + [gettext("A device with this name already exists.")]
                ok = False
        problems = check_prefix(self.prefix.data)
        if problems:
            self.prefix.errors = list(self.prefix.errors) + problems
            ok = False
        return ok


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _instance_rows(fp: Faceplate) -> list[dict]:
    """The devices of a type with their binding state (one tag query for all)."""
    params = parameters(fp)
    index = tag_index()
    rows = []
    for inst in sorted(fp.instances, key=lambda i: natural_key(i.name)):
        found = resolve(inst, params, index)
        missing = [p.name for p in params if found[p.key] is None]
        rows.append({"instance": inst, "found": len(params) - len(missing), "total": len(params), "missing": missing})
    return rows


def _overrides_from_form(fp: Faceplate) -> tuple[dict, list[str]]:
    """Read the per-parameter tag names of the instance form (``bind_<key>`` fields).

    Returns:
        ``(overrides {key: tag id}, problems)``.
    """
    overrides, problems = {}, []
    for p in parameters(fp):
        name = (request.form.get(f"bind_{p.key}") or "").strip()
        if not name:
            continue
        dp = DataPoint.query.filter(db.func.lower(DataPoint.label) == name.lower()).first()
        if dp is None:
            problems.append(gettext("{0}: there is no tag named {1}.", p.name, name))
        else:
            overrides[p.key] = dp.id
    return overrides, problems


def _may_open(inst: FaceplateInstance) -> bool:
    """Whether the current user may open a device's window (= may open the template screen)."""
    screen = inst.faceplate.screen if inst.faceplate else None
    return screen is not None and current_user.can_view_dashboard(screen)


def _instance_or_403(instance_id: int) -> FaceplateInstance:
    """Load a device the current user may open (401 for visitors, 403 otherwise)."""
    inst = db.get_or_404(FaceplateInstance, instance_id)
    if not _may_open(inst):
        if not current_user.is_authenticated:
            abort(401)
        abort(403)
    return inst


def visible_instances() -> list[FaceplateInstance]:
    """All devices the current user may open, sorted by type and name."""
    out = []
    for fp in Faceplate.query.order_by(Faceplate.name).all():
        if fp.screen is None or not current_user.can_view_dashboard(fp.screen):
            continue
        out.extend(sorted(fp.instances, key=lambda i: natural_key(i.name)))
    return out


def faceplate_choices() -> list[dict]:
    """Devices for the designer's *Click opens faceplate* list: ``[{"id", "name"}]``."""
    out = []
    for fp in Faceplate.query.order_by(Faceplate.name).all():
        for inst in sorted(fp.instances, key=lambda i: natural_key(i.name)):
            out.append({"id": inst.id, "name": f"{fp.name} – {inst.name}"})
    return out


# ---------------------------------------------------------------------------
# Device types
# ---------------------------------------------------------------------------
@bp.route("/")
@permission_required(Perm.MANAGE_SCREENS)
def index():
    """The device types.

    Returns:
        Rendered page.
    """
    items = []
    for fp in Faceplate.query.order_by(Faceplate.name).all():
        items.append({"faceplate": fp, "parameters": len(parameters(fp)), "instances": len(fp.instances)})
    return render_template("faceplates/index.html", items=items)


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:faceplate_id>/edit", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_SCREENS)
def edit(faceplate_id: int | None = None):
    """Create or edit a device type.

    Args:
        faceplate_id: The type to edit (``None`` = new).

    Returns:
        The form, or a redirect to the type's page.
    """
    fp = db.get_or_404(Faceplate, faceplate_id) if faceplate_id else None
    form = FaceplateForm(obj=fp, faceplate=fp)
    if fp is None and not form.is_submitted() and request.args.get("screen", type=int):
        form.screen_id.data = request.args.get("screen", type=int)
    if form.validate_on_submit():
        if fp is None:
            fp = Faceplate()
            db.session.add(fp)
        fp.name = form.name.data.strip()
        fp.screen_id = form.screen_id.data
        fp.prefix = form.prefix.data.strip()
        fp.description = (form.description.data or "").strip()
        db.session.commit()
        flash(gettext("Device type '{0}' saved.", fp.name), "success")
        return redirect(url_for("faceplates.detail", faceplate_id=fp.id))
    return render_template("faceplates/form.html", form=form, faceplate=fp)


@bp.route("/<int:faceplate_id>/delete", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def delete(faceplate_id: int):
    """Delete a device type and its devices (the tags and the template screen stay).

    Returns:
        Redirect to the list.
    """
    fp = db.get_or_404(Faceplate, faceplate_id)
    db.session.delete(fp)
    db.session.commit()
    flash(gettext("Device type '{0}' and its devices deleted (tags and screens are kept).", fp.name), "success")
    return redirect(url_for("faceplates.index"))


@bp.route("/<int:faceplate_id>")
@permission_required(Perm.MANAGE_SCREENS)
def detail(faceplate_id: int):
    """A device type: its parameters and its devices.

    Returns:
        Rendered page.
    """
    fp = db.get_or_404(Faceplate, faceplate_id)
    return render_template("faceplates/detail.html", faceplate=fp, parameters=parameters(fp),
                           fixed=fixed_tags(fp), rows=_instance_rows(fp), tag_menu=_empty_tag_menu())


@bp.route("/api/preview")
@permission_required(Perm.MANAGE_SCREENS)
def api_preview():
    """Parameters for a screen and prefix (live preview in the type form).

    Query: ``screen`` (id), ``prefix``.

    Returns:
        JSON ``{"suggested", "parameters": [{"name", "tag", "type"}], "fixed": [labels], "problems"}``.
    """
    screen = db.session.get(Dashboard, request.args.get("screen", type=int) or 0)
    if screen is None:
        return jsonify(suggested="", parameters=[], fixed=[], problems=[])
    prefix = (request.args.get("prefix") or "").strip()
    probe = SimpleNamespace(screen=screen, prefix=prefix)
    params = parameters(probe) if prefix else []
    keys = {p.ref.id for p in params}
    result = {"suggested": suggest_prefix(screen.datapoints),
              "parameters": [{"name": p.name, "tag": p.ref.label, "type": p.ref.data_type} for p in params],
              "fixed": [dp.label for dp in screen.datapoints if dp.id not in keys],
              "problems": check_prefix(prefix) if prefix else []}
    return jsonify(result)


# ---------------------------------------------------------------------------
# Devices (instances)
# ---------------------------------------------------------------------------
@bp.route("/<int:faceplate_id>/instances/new", methods=["GET", "POST"])
@bp.route("/instances/<int:instance_id>/edit", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_SCREENS)
def instance_edit(faceplate_id: int | None = None, instance_id: int | None = None):
    """Create or edit a device.

    The form shows every parameter with the tag the prefix finds; a tag name
    typed next to a parameter binds it by hand.  *Create the missing tags*
    copies the reference device's tags for the parameters without a tag.

    Returns:
        The form, or a redirect to the type's page.
    """
    inst = db.get_or_404(FaceplateInstance, instance_id) if instance_id else None
    fp = inst.faceplate if inst else db.get_or_404(Faceplate, faceplate_id)
    form = InstanceForm(obj=inst, faceplate=fp, instance=inst)
    params = parameters(fp)
    errors: list[str] = []
    if form.validate_on_submit():
        overrides, errors = _overrides_from_form(fp)
        prefix = form.prefix.data.strip()
        created = []
        if not errors and form.create_tags.data:
            plc = db.session.get(PLC, form.plc_id.data) if form.plc_id.data else None
            created, errors = create_tags(fp, prefix, plc, form.db_shift.data or 0, form.start_shift.data or 0)
        if not errors:
            if inst is None:
                inst = FaceplateInstance(faceplate=fp)
                db.session.add(inst)
            inst.name = form.name.data.strip()
            inst.prefix = prefix
            inst.description = (form.description.data or "").strip()
            inst.overrides = overrides
            db.session.commit()
            msg = gettext("Device '{0}' saved.", inst.name)
            if created:
                msg += " " + gettext("{0} tag(s) created.", len(created))
            flash(msg, "success")
            return redirect(url_for("faceplates.detail", faceplate_id=fp.id))
        db.session.rollback()
    if inst is None and not form.is_submitted():
        form.prefix.data = request.args.get("prefix", "")
        form.name.data = request.args.get("name", "") or (instance_name(form.prefix.data) if form.prefix.data else "")
    # The binding table (what the current prefix finds).
    probe_prefix = (form.prefix.data or (inst.prefix if inst else "")).strip()
    current = inst.overrides if inst else {}
    if form.is_submitted():
        current = {p.key: request.form.get(f"bind_{p.key}", "") for p in params}
    rows = []
    index = tag_index()
    for p in params:
        auto = index.get((probe_prefix + p.name).lower()) if probe_prefix else None
        bound = current.get(p.key)
        if isinstance(bound, int):
            dp = db.session.get(DataPoint, bound)
            bound = dp.label if dp else ""
        rows.append({"param": p, "auto": auto, "bound": bound or ""})
    ref_addresses = ", ".join(f"{p.name} = {p.ref.address}" for p in params[:6]) + (" …" if len(params) > 6 else "")
    return render_template("faceplates/instance_form.html", form=form, faceplate=fp, instance=inst, rows=rows,
                           errors=errors, ref_addresses=ref_addresses)


@bp.route("/instances/<int:instance_id>/delete", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def instance_delete(instance_id: int):
    """Delete a device (its tags stay).

    Returns:
        Redirect to the type's page.
    """
    inst = db.get_or_404(FaceplateInstance, instance_id)
    fp_id = inst.faceplate_id
    db.session.delete(inst)
    db.session.commit()
    flash(gettext("Device '{0}' deleted (its tags are kept).", inst.name), "success")
    return redirect(url_for("faceplates.detail", faceplate_id=fp_id))


@bp.route("/<int:faceplate_id>/scan", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_SCREENS)
def scan_tags(faceplate_id: int):
    """Find devices among the existing tags (GET) or create the chosen ones (POST).

    POST JSON: ``{"items": [{"prefix", "name"}]}``.

    Returns:
        JSON ``{"candidates": [...]}`` or ``{"ok", "created", "errors"}``.
    """
    fp = db.get_or_404(Faceplate, faceplate_id)
    if request.method == "GET":
        return jsonify(candidates=scan(fp))
    items = (request.get_json(silent=True) or {}).get("items") or []
    names = {i.name.lower() for i in fp.instances}
    prefixes = {i.prefix.lower() for i in fp.instances} | {fp.prefix.lower()}
    created, errors = 0, []
    for item in items[:2000]:
        if not isinstance(item, dict):
            continue
        prefix = str(item.get("prefix") or "").strip()
        name = str(item.get("name") or "").strip()[:100] or instance_name(prefix)
        if check_prefix(prefix) or prefix.lower() in prefixes:
            errors.append(gettext("{0}: invalid prefix or already used.", prefix))
            continue
        base, n = name, 1
        while name.lower() in names:
            n += 1
            name = f"{base} ({n})"
        db.session.add(FaceplateInstance(faceplate=fp, name=name, prefix=prefix))
        names.add(name.lower())
        prefixes.add(prefix.lower())
        created += 1
    db.session.commit()
    return jsonify(ok=True, created=created, errors=errors)


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
COLUMNS = [("Name", 30), ("Prefix", 30), ("Description", 40), ("Bindings", 50), ("Create tags", 12), ("PLC", 20),
           ("DB shift", 10), ("Address shift", 12)]
HELP = [
    ("Name", "Name of the device, shown as the window title (unique within the device type)."),
    ("Prefix", "Beginning of the device's tag names, e.g. Panel_37.MeterB. – the parameter names follow it."),
    ("Bindings", "Optional: parameters bound to other tags, as parameter=tag name separated by ; "
                 "(e.g. voltage_l1=Panel_37.Spare.u1)."),
    ("Create tags", "yes = create the tags this device does not have yet, as copies of the reference device's tags "
                    "(see PLC, DB shift and Address shift). Existing tags are never changed."),
    ("PLC", "PLC of the new tags (empty = the PLC of the reference tags; another PLC must be of the same kind)."),
    ("DB shift", "S7: added to the DB number of the copied addresses (DB50 with shift 3 → DB53)."),
    ("Address shift", "Added to the start address: S7 byte offset, Modbus register / coil, BACnet instance. On the "
                      "reference PLC a shift is required."),
    ("Import", "Rows are matched by Name: existing devices are updated, new names are added. "
               "Devices that are not in the file are kept. Nothing is imported when a row is invalid."),
]
ALIASES = {"name": "name", "prefix": "prefix", "tag prefix": "prefix", "description": "description",
           "bindings": "bindings", "create tags": "create", "create": "create", "plc": "plc",
           "db shift": "db_shift", "address shift": "start_shift"}


@bp.route("/<int:faceplate_id>/instances.xlsx")
@permission_required(Perm.MANAGE_SCREENS)
def instances_xlsx(faceplate_id: int):
    """Download the devices of a type as an Excel file."""
    fp = db.get_or_404(Faceplate, faceplate_id)
    rows = []
    for inst in sorted(fp.instances, key=lambda i: natural_key(i.name)):
        binds = []
        for key, tag_id in sorted(inst.overrides.items()):
            dp = db.session.get(DataPoint, tag_id)
            if dp is not None:
                binds.append(f"{key}={dp.label}")
        rows.append([inst.name, inst.prefix, inst.description, "; ".join(binds), "", "", "", ""])
    return send_xlsx(build_workbook("Devices", COLUMNS, rows, HELP), f"devices-{fp.name}")


@bp.route("/<int:faceplate_id>/instances/import", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def instances_import(faceplate_id: int):
    """Import devices from an Excel file (all rows or nothing).

    Returns:
        Redirect to the type's page with the result.
    """
    fp = db.get_or_404(Faceplate, faceplate_id)
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash(gettext("Choose an Excel file."), "warning")
        return redirect(url_for("faceplates.detail", faceplate_id=fp.id))
    try:
        _cols, rows = read_rows(upload.stream, ALIASES, ("name", "prefix"))
    except ImportFileError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("faceplates.detail", faceplate_id=fp.id))
    keys = {p.key for p in parameters(fp)}
    by_name = {i.name.lower(): i for i in fp.instances}
    problems, plan, seen = [], [], set()
    for number, row in rows:
        name, prefix = text(row.get("name"))[:100], text(row.get("prefix"))
        if not name:
            problems.append(gettext("Row {0}: the name is empty.", number))
            continue
        if name.lower() in seen:
            problems.append(gettext("Row {0}: {1} appears twice in the file.", number, name))
            continue
        seen.add(name.lower())
        issues = check_prefix(prefix)
        if issues:
            problems.append(gettext("Row {0}: {1}", number, " ".join(issues)))
            continue
        overrides = {}
        for part in text(row.get("bindings")).split(";"):
            if not part.strip():
                continue
            key, sep, label = part.partition("=")
            key, label = key.strip().lower(), label.strip()
            dp = DataPoint.query.filter(db.func.lower(DataPoint.label) == label.lower()).first() if sep else None
            if not sep or key not in keys or dp is None:
                problems.append(gettext("Row {0}: invalid binding '{1}' (parameter=tag name).", number, part.strip()))
                continue
            overrides[key] = dp.id
        tags = None
        if boolean(row.get("create")):
            plc = None
            if text(row.get("plc")):
                plc = PLC.query.filter(db.func.lower(PLC.name) == text(row.get("plc")).lower()).first()
                if plc is None:
                    problems.append(gettext("Row {0}: there is no PLC named {1}.", number, text(row.get("plc"))))
                    continue
            try:
                shifts = int(number_value(row.get("db_shift")) or 0), int(number_value(row.get("start_shift")) or 0)
            except (TypeError, ValueError):
                problems.append(gettext("Row {0}: the shifts must be whole numbers.", number))
                continue
            tags = (plc, *shifts)
        plan.append((name, prefix, text(row.get("description"))[:500], overrides, tags))
    if problems:
        flash(gettext("Nothing imported:") + " " + " · ".join(problems[:10]), "danger")
        return redirect(url_for("faceplates.detail", faceplate_id=fp.id))
    added = updated = created = 0
    for name, prefix, description, overrides, tags in plan:
        if tags is not None:
            new, issues = create_tags(fp, prefix, *tags)
            if issues:
                db.session.rollback()
                flash(gettext("Nothing imported:") + " " + gettext("{0}: {1}", name, " ".join(issues[:3])), "danger")
                return redirect(url_for("faceplates.detail", faceplate_id=fp.id))
            db.session.flush()
            created += len(new)
        inst = by_name.get(name.lower())
        if inst is None:
            inst = FaceplateInstance(faceplate=fp, name=name)
            db.session.add(inst)
            added += 1
        else:
            updated += 1
        inst.prefix, inst.description, inst.overrides = prefix, description, overrides
    db.session.commit()
    flash(gettext("{0} device(s) added, {1} updated.", added, updated)
          + (" " + gettext("{0} tag(s) created.", created) if created else ""), "success")
    return redirect(url_for("faceplates.detail", faceplate_id=fp.id))


# ---------------------------------------------------------------------------
# Run time
# ---------------------------------------------------------------------------
@bp.route("/devices")
def devices():
    """All devices the user may open, each with an *Open* button (pop-up window).

    Returns:
        Rendered page (log in first for screens that are not public).
    """
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    groups: dict[int, dict] = {}
    for inst in visible_instances():
        group = groups.setdefault(inst.faceplate_id, {"faceplate": inst.faceplate, "instances": []})
        group["instances"].append(inst)
    return render_template("faceplates/devices.html", groups=list(groups.values()), tag_menu=_empty_tag_menu())


def _empty_tag_menu() -> dict:
    """Tag menu data without points (the pop-up windows add theirs)."""
    from app.dashboards.routes import tag_menu_data_for  # noqa: PLC0415

    return tag_menu_data_for([])


@bp.route("/api/instances/<int:instance_id>")
def api_instance(instance_id: int):
    """Everything a pop-up window needs: the layout with the device's tags, tag data, URLs.

    Returns:
        JSON ``{"id", "title", "layout", "points", "menu_points", "icons", "can_write",
        "missing", "refresh_ms", "urls"}``.
    """
    from app.dashboards.routes import tag_menu_data_for  # noqa: PLC0415

    inst = _instance_or_403(instance_id)
    fp = inst.faceplate
    mapping, missing = point_map(inst)
    layout = substitute_layout(fp.screen.layout_data, mapping)
    ids = {i for i in mapping.values() if i}
    points = DataPoint.query.filter(DataPoint.id.in_(ids)).all() if ids else []
    menu = tag_menu_data_for(points)
    return jsonify(
        id=inst.id, title=inst.title, name=inst.name, faceplate=fp.name, description=inst.description,
        layout=layout,
        points={str(dp.id): {**dp.to_dict(), "may_write": dp.may_write(current_user)} for dp in points},
        menu_points=menu["points"], can_history=menu["can_history"],
        icons=icon_shapes_for_layout(layout), can_write=current_user.can(Perm.WRITE_VALUES),
        missing=missing, refresh_ms=fp.screen.refresh_ms,
        urls={"values": url_for("faceplates.api_values", instance_id=inst.id),
              "write": url_for("api.datapoint_write", point_id=0),
              "asset": url_for("assets.file", asset_id=0),
              "go": url_for("dashboards.go", dashboard_id=0)},
    )


@bp.route("/api/instances/<int:instance_id>/values")
def api_values(instance_id: int):
    """Live values of a device's window (same format as the screen values).

    Returns:
        JSON ``{"timestamp", "values", "plcs"}``.
    """
    inst = _instance_or_403(instance_id)
    mapping, _missing = point_map(inst)
    ids = {i for i in mapping.values() if i}
    points = DataPoint.query.filter(DataPoint.id.in_(ids)).all() if ids else []
    values, plcs = read_datapoints(points)
    return jsonify(timestamp=datetime.now(timezone.utc).isoformat(),
                   values={str(k): v for k, v in values.items()}, plcs={str(k): v for k, v in plcs.items()})
