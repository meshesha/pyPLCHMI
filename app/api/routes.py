"""JSON API used by the dashboard front-end.

All endpoints except ``/api/health`` require a logged-in session.  State-changing requests (POST)
are CSRF-protected: the browser sends the token in the ``X-CSRFToken``
header (see ``static/js/dashboard.js``).
"""

import secrets

from flask import abort, jsonify, request, url_for
from flask_login import current_user
from werkzeug.datastructures import MultiDict

from app.api import bp
from app.audit import recorder as audit
from app.dashboards import icons as icon_lib
from app.dashboards.forms import DataPointForm
from app.dashboards.layout import LayoutError, sanitize_layout
from app.extensions import db
from app.alarms.engine import scan_if_due
from app.models import PLC, AlarmEvent, Asset, Dashboard, DataPoint, Perm
from app.models.alarm import SEVERITIES
from app.plc.addressing import AddressError
from app.plc.protocols import parse_tag_address
from app.plc.datatypes import json_safe
from app.plc.driver import PLCError, get_manager
from app.plc.service import read_dashboard, read_datapoints, write_datapoint
from app.utils.decorators import admin_required, permission_required
from app.i18n.catalog import gettext

#: Random id of this server process; changes when the server restarts, so the
#: connection monitor in the browser can tell "back again" from "restarted".
BOOT_ID = secrets.token_hex(6)


def _json_error(message: str, status: int):
    """Build a JSON error response.

    Args:
        message: Human-readable message.
        status: HTTP status code.

    Returns:
        ``(response, status)`` tuple.
    """
    return jsonify(error=message), status


@bp.errorhandler(404)
def api_not_found(error):
    """Return JSON instead of the HTML 404 page for API routes.

    Args:
        error: The HTTP exception.

    Returns:
        JSON error with status 404.
    """
    return _json_error(gettext('not found'), 404)


@bp.errorhandler(403)
def api_forbidden(error):
    """Return JSON instead of the HTML 403 page for API routes.

    Args:
        error: The HTTP exception.

    Returns:
        JSON error with status 403.
    """
    return _json_error(gettext('forbidden'), 403)


@bp.route("/health")
def health():
    """Liveness check for the browser's connection monitor and for Docker / load balancers.

    Public (no login) and cheap: it only runs ``SELECT 1`` on the database.
    Never cached and never sets a session cookie (read-only API request).

    Returns:
        ``{"status": "ok", "boot": <process id token>}`` with 200, or
        ``{"status": "db_error", ...}`` with 503 when the database is unreachable.
    """
    try:
        db.session.execute(db.text("SELECT 1"))
        status, code = "ok", 200
    except Exception:  # noqa: BLE001 – any database failure means "not healthy"
        db.session.rollback()
        status, code = "db_error", 503
    response = jsonify(status=status, boot=BOOT_ID)
    response.headers["Cache-Control"] = "no-store"
    return response, code


@bp.route("/dashboards/<int:dashboard_id>/values")
def dashboard_values(dashboard_id: int):
    """Read the live values of every data point on a dashboard.

    Args:
        dashboard_id: Dashboard id.

    Returns:
        JSON ``{"timestamp", "values": {id: {...}}, "plcs": {id: {...}}}``.
    """
    dashboard = db.get_or_404(Dashboard, dashboard_id)
    if not current_user.can_view_dashboard(dashboard):
        if not current_user.is_authenticated:
            return _json_error(gettext('authentication required'), 401)
        abort(403)
    return jsonify(read_dashboard(dashboard))


@bp.route("/datapoints/<int:point_id>/write", methods=["POST"])
@permission_required(Perm.WRITE_VALUES)
def datapoint_write(point_id: int):
    """Write a value (engineering units) to a writable data point.

    Request body: ``{"value": <number|bool|string>}``.

    Args:
        point_id: Data point id.

    Returns:
        JSON with the confirmed value, or an error with status 400/403/502.
    """
    dp = db.get_or_404(DataPoint, point_id)
    # Allowed on any screen the user may open; tags on no screen: screen managers only.
    allowed = current_user.can(Perm.MANAGE_SCREENS) or any(
        current_user.can_view_dashboard(screen) for screen in dp.screens)
    if not allowed:
        abort(403)
    if not dp.writable:
        return _json_error(gettext('data point is read-only'), 400)
    if not dp.may_write(current_user):  # tag limited to selected roles
        return _json_error(gettext('Your role may not write this value.'), 403)
    if dp.is_local:
        return _json_error(gettext('local dummy tag: the value is kept by each browser window (not written to the server)'),
                           400)
    payload = request.get_json(silent=True) or {}
    if "value" not in payload:
        return _json_error(gettext("missing 'value'"), 400)
    before = None
    try:
        before = read_datapoints([dp])[0].get(dp.id, {}).get("value")
    except Exception:  # noqa: BLE001 - the value before is only for the audit log
        pass
    try:
        result = write_datapoint(dp, payload["value"])
        audit.record("write", "Value", dp.id, f"{dp.label} ({dp.plc.name if dp.plc else ''} {dp.address})",
                     [{"field": "value", "before": before, "after": result.get("value", payload["value"])}])
    except ValueError as exc:
        return _json_error(gettext('invalid value: {0}', exc), 400)
    except PLCError as exc:
        return _json_error(str(exc), 502)
    return jsonify(ok=True, id=dp.id, **result)


@bp.route("/datapoints/<int:point_id>/details")
def datapoint_details(point_id: int):
    """Configuration of a tag for the *Details* window of a screen value (right-click).

    Allowed for everybody who may open a screen showing the tag (also
    visitors on public screens); screen managers may see every tag.  The
    PLC's network address is only included for logged-in users; the history
    summary needs the ``view_history`` permission.

    Args:
        point_id: Tag id.

    Returns:
        JSON with the tag, PLC and history information.
    """
    from app.models import TagSample  # noqa: PLC0415
    from app.models.alarm import iso  # noqa: PLC0415

    dp = db.get_or_404(DataPoint, point_id)
    visible = [s for s in dp.screens if current_user.can_view_dashboard(s)]
    if not (current_user.can(Perm.MANAGE_SCREENS) or visible):
        abort(403)
    plc = dp.plc
    info = {
        "id": dp.id,
        "label": dp.label,
        "plc_id": dp.plc_id,
        "plc": plc.name if plc else "",
        "plc_description": plc.description if plc else "",
        "protocol": plc.protocol_label if plc else "",
        "endpoint": plc.endpoint if plc and current_user.is_authenticated else None,
        "plc_enabled": bool(plc.enabled) if plc else False,
        "address": dp.address if dp.protocol != "dummy" else gettext("internal (no device)"),
        "data_type": dp.data_type + (f"[{dp.string_length}]" if dp.data_type == "STRING" else ""),
        "unit": dp.unit,
        "scale": dp.scale,
        "offset": dp.offset,
        "decimals": dp.decimals,
        "min": dp.min_value,
        "max": dp.max_value,
        "true_text": dp.true_text if dp.is_bool else None,
        "false_text": dp.false_text if dp.is_bool else None,
        "writable": dp.writable,
        "write_roles": ([r.name for r in dp.write_roles] if dp.limited_write else None) if dp.writable else None,
        "may_write": dp.may_write(current_user),
        "dummy": dp.scope_label if dp.protocol == "dummy" else None,
        "screens": [s.name for s in visible],
        "alarms": [a.name for a in dp.alarms] if current_user.is_authenticated else [],
        "history": None,
        "files": [{"name": f.name, "url": url_for("documents.file", file_id=f.id), "kind": f.kind}
                  for f in (plc.files if plc else [])] if current_user.is_authenticated else [],
    }
    if current_user.can(Perm.VIEW_HISTORY):
        query = TagSample.query.filter_by(datapoint_id=dp.id)
        first = query.order_by(TagSample.ts.asc()).first()
        last = query.order_by(TagSample.ts.desc()).first()
        info["history"] = {"enabled": dp.is_logged, "interval": dp.log_interval, "count": query.count(),
                           "first": iso(first.ts) if first else None, "last": iso(last.ts) if last else None}
    return jsonify(info)


@bp.route("/plcs")
@permission_required(Perm.MANAGE_SCREENS)
def plc_list():
    """List PLCs with their cached connection status.

    Returns:
        JSON list of PLC dictionaries including a ``status`` key.
    """
    manager = get_manager()
    return jsonify([{**p.to_dict(), "status": manager.status(p)} for p in PLC.query.order_by(PLC.name)])


@bp.route("/plcs/<int:plc_id>/status")
@permission_required(Perm.MANAGE_PLCS)
def plc_status(plc_id: int):
    """Return the cached connection status of one PLC.

    Args:
        plc_id: PLC id.

    Returns:
        JSON ``{"connected", "error", "last_success"}``.
    """
    plc = db.get_or_404(PLC, plc_id)
    return jsonify(get_manager().status(plc))


@bp.route("/plcs/<int:plc_id>/read", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def plc_read(plc_id: int):
    """Read an arbitrary address once – used by the *Test read* button.

    Request body: ``{"address": "DB1.DBD4", "data_type": "REAL",
    "string_length": 20}`` (``data_type``/``string_length`` optional).  The
    address syntax follows the PLC's protocol (S7 or Modbus).

    Args:
        plc_id: PLC id.

    Returns:
        JSON ``{"address", "value"}`` or an error (400 bad address,
        502 communication failure).
    """
    plc = db.get_or_404(PLC, plc_id)
    body = request.get_json(silent=True) or {}
    try:
        addr = parse_tag_address(body.get("address", ""), body.get("data_type"), body.get("string_length"),
                                 plc.protocol or "s7")
    except AddressError as exc:
        return _json_error(str(exc), 400)
    result = get_manager().read_one(plc, addr)
    if not result.ok:
        return _json_error(result.error, 502)
    return jsonify(address=str(addr), value=json_safe(result.value))


# ---------------------------------------------------------------------------
# Graphic (Image) dashboards
# ---------------------------------------------------------------------------
@bp.route("/dashboards/<int:dashboard_id>/layout", methods=["GET"])
def dashboard_layout(dashboard_id: int):
    """Return the stored graphic layout of a dashboard.

    Args:
        dashboard_id: Dashboard id.

    Returns:
        JSON layout document (see :mod:`app.dashboards.layout`).
    """
    dashboard = db.get_or_404(Dashboard, dashboard_id)
    if not current_user.can_view_dashboard(dashboard):
        if not current_user.is_authenticated:
            return _json_error(gettext('authentication required'), 401)
        abort(403)
    return jsonify(dashboard.layout_data)


@bp.route("/dashboards/<int:dashboard_id>/layout", methods=["PUT", "POST"])
@permission_required(Perm.MANAGE_SCREENS)
def dashboard_layout_save(dashboard_id: int):
    """Validate and store the layout sent by the designer.

    The cleaned layout is returned so the designer can adopt server-side
    corrections (generated ids, clamped values, dropped references).

    Args:
        dashboard_id: Dashboard id.

    Returns:
        JSON ``{"ok": true, "layout": {...}}`` or a 400 error.
    """
    dashboard = db.get_or_404(Dashboard, dashboard_id)
    if not dashboard.is_image:
        return _json_error(gettext('not a Plane screen'), 400)
    data = request.get_json(silent=True)
    try:
        layout = sanitize_layout(
            data,
            point_ids=[dp.id for dp in dashboard.datapoints],
            asset_ids=[a.id for a in Asset.query.with_entities(Asset.id)],
            dashboard_ids=[d.id for d in Dashboard.query.with_entities(Dashboard.id)],
        )
    except LayoutError as exc:
        return _json_error(str(exc), 400)
    dashboard.set_layout(layout)
    db.session.commit()
    return jsonify(ok=True, layout=layout)


@bp.route("/dashboards/<int:dashboard_id>/datapoints", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def datapoint_create(dashboard_id: int):
    """Create a data point (PLC tag) from the designer's *New tag* dialog.

    The JSON body uses the same field names as the data point form
    (``label``, ``plc_id``, ``address``, ``data_type``, ``string_length``,
    ``unit``, ``decimals``, ``scale``, ``offset``, ``writable``...) and is
    validated by :class:`~app.dashboards.forms.DataPointForm`.

    Args:
        dashboard_id: Dashboard id.

    Returns:
        JSON of the new point (201) or ``{"errors": {...}}`` (400).
    """
    dashboard = db.get_or_404(Dashboard, dashboard_id)
    body = request.get_json(silent=True) or {}
    defaults = {"widget": "value", "scale": 1, "offset": 0, "decimals": 2, "width": 1, "position": 0,
                "true_text": "ON", "false_text": "OFF", "string_length": 20}
    fields = {**defaults, **{k: v for k, v in body.items() if v is not None and v != ""}}
    if not fields.get("writable"):
        fields.pop("writable", None)  # unchecked checkbox = absent
    else:
        fields["writable"] = "y"
    form = DataPointForm(formdata=MultiDict({k: str(v) for k, v in fields.items()}), meta={"csrf": False})
    if not form.validate():
        return jsonify(errors=form.errors), 400
    dp = DataPoint()
    form.populate_datapoint(dp)
    db.session.add(dp)
    dashboard.add_tag(dp)
    db.session.commit()
    return jsonify(dp.to_dict()), 201


@bp.route("/icons")
@permission_required(Perm.MANAGE_SCREENS)
def icon_search():
    """Search the Tabler icon set (designer palette).

    Query: ``q`` (words), ``style`` (``outline``/``filled``), ``category``,
    ``limit`` (default 60, max 200).

    Returns:
        JSON ``{"available", "total", "icons": [{name, style, category, shape}], "categories"}``.
    """
    if not icon_lib.available():
        return jsonify(available=False, total=0, icons=[], categories=[])
    style = request.args.get("style") if request.args.get("style") in icon_lib.STYLES else None
    limit = min(max(request.args.get("limit", 60, type=int), 1), 200)
    found, total = icon_lib.search(request.args.get("q", "")[:100], style, request.args.get("category") or None, limit)
    for item in found:
        item["shape"] = icon_lib.shape(item["name"], item["style"])
    return jsonify(available=True, total=total, icons=found, categories=icon_lib.categories())


@bp.route("/icons/<style>/<name>")
@permission_required(Perm.MANAGE_SCREENS)
def icon_shape(style: str, name: str):
    """The drawable shapes of one icon.

    Args:
        style: ``outline`` or ``filled``.
        name: Icon name.

    Returns:
        JSON ``{"viewBox", "mode", "nodes"}`` or 404.
    """
    shape = icon_lib.shape(name, style)
    if shape is None:
        return _json_error(gettext('unknown icon'), 404)
    return jsonify(shape)


@bp.route("/tags/search")
@permission_required(Perm.MANAGE_SCREENS)
def tag_search():
    """Search the tag table (the *existing tag* picker of the screen editors).

    Query: ``q`` (label, address, PLC name, type), ``screen`` (screen id –
    marks tags already on it), ``limit`` (default 50, max 200).

    Returns:
        JSON ``{"tags": [{id, label, plc, address, data_type, unit, screens, on_screen}], "more"}``.
    """
    q = request.args.get("q", "").strip()[:100]
    limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
    screen = db.session.get(Dashboard, request.args.get("screen", type=int)) if request.args.get("screen") else None
    on_screen = {lk.datapoint_id for lk in screen.tag_links} if screen else set()
    query = DataPoint.query.join(PLC, DataPoint.plc_id == PLC.id)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(DataPoint.label.ilike(like), DataPoint.address_text.ilike(like),
                                    PLC.name.ilike(like), DataPoint.data_type.ilike(like)))
    rows = query.order_by(DataPoint.label, DataPoint.id).limit(limit + 1).all()
    tags = [{"id": dp.id, "label": dp.label, "plc": dp.plc.name, "address": dp.address_text or dp.address,
             "data_type": dp.data_type, "unit": dp.unit, "screens": dp.screen_names,
             "on_screen": dp.id in on_screen} for dp in rows[:limit]]
    return jsonify(tags=tags, more=len(rows) > limit)


@bp.route("/dashboards/<int:dashboard_id>/tags", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def dashboard_tag_add(dashboard_id: int):
    """Show an existing tag on a screen (designer's *existing tag* picker).

    Request body: ``{"datapoint_id": <id>}``.

    Args:
        dashboard_id: Screen id.

    Returns:
        JSON of the tag (``dp.to_dict()``).
    """
    dashboard = db.get_or_404(Dashboard, dashboard_id)
    body = request.get_json(silent=True) or {}
    try:
        dp = db.get_or_404(DataPoint, int(body.get("datapoint_id")))
    except (TypeError, ValueError):
        return _json_error(gettext("missing 'datapoint_id'"), 400)
    dashboard.add_tag(dp)
    db.session.commit()
    return jsonify(dp.to_dict())


@bp.route("/dashboards/<int:dashboard_id>/tags/<int:point_id>", methods=["DELETE"])
@permission_required(Perm.MANAGE_SCREENS)
def dashboard_tag_remove(dashboard_id: int, point_id: int):
    """Remove a tag from a screen (the tag stays in the tag table).

    Args:
        dashboard_id: Screen id.
        point_id: Tag id.

    Returns:
        JSON ``{"removed": bool}``.
    """
    dashboard = db.get_or_404(Dashboard, dashboard_id)
    removed = dashboard.remove_tag(point_id)
    db.session.commit()
    return jsonify(removed=removed)


@bp.route("/bacnet/whois", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def bacnet_whois():
    """Discover BACnet devices (Who-Is) for the PLC form.

    Request body: ``{"address": "192.168.1.40"}`` (optional; without it a
    broadcast Who-Is is sent on the local network).

    Returns:
        JSON ``{"devices": [{"device_id", "address"}]}`` or an error (502).
    """
    body = request.get_json(silent=True) or {}
    address = (body.get("address") or "").strip() or None
    try:
        devices = get_manager().bacnet_who_is(address, timeout=float(body.get("timeout") or 3))
    except PLCError as exc:
        return _json_error(str(exc), 502)
    return jsonify(devices=devices)


# ---------------------------------------------------------------------------
# Alarms / events
# ---------------------------------------------------------------------------
def _event_json(event) -> dict:
    """Serialise an event with the screen URL (if the user may open that screen).

    Args:
        event: :class:`~app.models.alarm.AlarmEvent`.

    Returns:
        JSON-ready dict.
    """
    screen_url = None
    if event.screen is not None and current_user.can_view_dashboard(event.screen):
        screen_url = url_for("dashboards.view", slug=event.screen.slug, alarm=event.id)
    return event.to_dict(screen_url)


@bp.route("/alarms/active")
def alarms_active():
    """Open (active) alarm events for the alarm popup and the Events window.

    Also runs an alarm scan when the background scanner has not run recently.

    Returns:
        JSON ``{"events": [...], "count", "unacked", "highest", "can_remove"}``.
    """
    if not current_user.is_authenticated:
        return _json_error(gettext('authentication required'), 401)
    scan_if_due()
    events = AlarmEvent.query.filter(AlarmEvent.cleared_at.is_(None)).order_by(AlarmEvent.occurred_at.desc()).all()
    rank = {k: v[2] for k, v in SEVERITIES.items()}
    highest = max((e.severity for e in events), key=lambda s: rank.get(s, 0), default=None)
    return jsonify(events=[_event_json(e) for e in events], count=len(events),
                   unacked=sum(1 for e in events if e.acked_at is None), highest=highest,
                   color=SEVERITIES[highest][1] if highest in SEVERITIES else None,
                   can_remove=current_user.is_admin)


@bp.route("/alarms/events/<int:event_id>")
def alarm_event(event_id: int):
    """One event (for the Details popup).

    Args:
        event_id: Event id.

    Returns:
        JSON event.
    """
    if not current_user.is_authenticated:
        return _json_error(gettext('authentication required'), 401)
    return jsonify(_event_json(db.get_or_404(AlarmEvent, event_id)))


@bp.route("/alarms/events/<int:event_id>/ack", methods=["POST"])
def alarm_ack(event_id: int):
    """Acknowledge an event (any logged-in user).

    Args:
        event_id: Event id.

    Returns:
        JSON event after acknowledgement.
    """
    if not current_user.is_authenticated:
        return _json_error(gettext('authentication required'), 401)
    event = db.get_or_404(AlarmEvent, event_id)
    event.acknowledge(current_user)
    db.session.commit()
    return jsonify(_event_json(event))


@bp.route("/alarms/events/<int:event_id>/remove", methods=["POST"])
@admin_required
def alarm_remove(event_id: int):
    """Close an active event manually (administrators only).

    If the condition is still true the alarm is *suppressed* until the
    condition clears, so it does not pop up again immediately.

    Args:
        event_id: Event id.

    Returns:
        JSON event after closing.
    """
    event = db.get_or_404(AlarmEvent, event_id)
    if event.is_open:
        event.close("manual", current_user)
        if event.alarm is not None:
            event.alarm.suppressed = True
        db.session.commit()
    return jsonify(_event_json(event))


@bp.route("/tags/values")
@admin_required
def tag_values():
    """Live values of selected tags (tag table).

    Query: ``ids=1,2,3`` (max 200).

    Returns:
        JSON ``{"values": {id: {...}}}``.
    """
    ids = [int(x) for x in request.args.get("ids", "").split(",") if x.strip().isdigit()][:200]
    points = DataPoint.query.filter(DataPoint.id.in_(ids)).all() if ids else []
    values, _ = read_datapoints(points)
    return jsonify(values={str(k): v for k, v in values.items()})


# ---------------------------------------------------------------------------
# UI templates: plant overview and quick search (Ctrl+K)
# ---------------------------------------------------------------------------
#: Values shown per screen card on the plant overview.
OVERVIEW_VALUES = 3


@bp.route("/overview")
def overview():
    """Live status of every screen the user may open (plant overview page).

    For each screen: open alarms (count, unacknowledged, highest severity),
    communication state of its PLCs and the first few tag values.  Local
    dummy tags are skipped.

    Returns:
        JSON ``{"timestamp", "screens": [...], "plcs": {id: {...}}}``.
    """
    if not current_user.is_authenticated:
        return _json_error(gettext('authentication required'), 401)
    screens = [s for s in Dashboard.query.order_by(Dashboard.name) if current_user.can_view_dashboard(s)]
    shown = {s.id: [dp for dp in s.datapoints if not dp.is_local][:OVERVIEW_VALUES] for s in screens}
    unique = {dp.id: dp for points in shown.values() for dp in points}
    values, plcs = read_datapoints(unique.values())
    rank = {k: v[2] for k, v in SEVERITIES.items()}
    events = AlarmEvent.query.filter(AlarmEvent.cleared_at.is_(None)).all()
    out = []
    for screen in screens:
        mine = [e for e in events if e.screen_id == screen.id]
        highest = max((e.severity for e in mine), key=lambda s: rank.get(s, 0), default=None)
        screen_plcs = [p for p in screen.plcs if p.enabled]
        for plc in screen_plcs:
            if plc.id not in plcs:
                plcs[plc.id] = {"name": plc.name, **{k: v for k, v in get_manager().status(plc).items()
                                                     if k in ("state", "connected", "error", "down_for")}}
        out.append({
            "id": screen.id, "name": screen.name, "description": screen.description or "",
            "url": url_for("dashboards.view", slug=screen.slug), "kind": screen.kind,
            "alarms": len(mine), "unacked": sum(1 for e in mine if e.acked_at is None),
            "severity": highest, "color": SEVERITIES[highest][1] if highest in SEVERITIES else None,
            "plcs": [p.id for p in screen_plcs],
            "values": [{"id": dp.id, "label": dp.label, "unit": dp.unit or "", **{
                k: values.get(dp.id, {}).get(k) for k in ("display", "quality", "stale")}} for dp in shown[screen.id]],
        })
    return jsonify(timestamp=datetime_now_iso(), screens=out, plcs={str(k): v for k, v in plcs.items()})


def datetime_now_iso() -> str:
    """Current UTC time as ISO 8601 text."""
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.now(timezone.utc).isoformat()


@bp.route("/search")
def search():
    """Quick search for the command palette (Ctrl+K): pages, screens, tags, PLCs.

    Query: ``q`` (case-insensitive text; empty = pages and screens only).
    Only items the user may open are returned.

    Returns:
        JSON ``{"results": [{"kind", "label", "detail", "url", "icon"}]}`` (at most 30).
    """
    if not current_user.is_authenticated:
        return _json_error(gettext('authentication required'), 401)
    from app.ui_templates.nav import nav_items  # noqa: PLC0415
    from app.ui_templates.registry import active_template  # noqa: PLC0415

    q = (request.args.get("q") or "").strip().lower()
    results: list = []

    def hit(*texts) -> bool:
        """Whether the search text occurs in any of the texts."""
        return not q or any(q in (t or "").lower() for t in texts)

    for item in nav_items(current_user, active_template().feature("home") == "overview"):
        if hit(item.label, item.group):
            results.append({"kind": "page", "label": item.label, "detail": item.group, "url": item.url,
                            "icon": item.icon})
    for screen in Dashboard.query.order_by(Dashboard.name):
        if current_user.can_view_dashboard(screen) and hit(screen.name, screen.description, screen.slug):
            results.append({"kind": "screen", "label": screen.name, "detail": gettext("Screen"),
                            "url": url_for("dashboards.view", slug=screen.slug), "icon": "grid-1x2"})
    if q and current_user.can(Perm.MANAGE_ROLES):
        like = f"%{q}%"
        tags = DataPoint.query.filter(db.or_(DataPoint.label.ilike(like), DataPoint.address_text.ilike(like))
                                      ).order_by(DataPoint.label).limit(15)
        for dp in tags:
            results.append({"kind": "tag", "label": dp.label, "detail": f"{dp.plc.name} · {dp.address_text or dp.address}",
                            "url": url_for("tags.edit", tag_id=dp.id), "icon": "tag"})
    if q and current_user.can(Perm.MANAGE_PLCS):
        for plc in PLC.query.filter(PLC.name.ilike(f"%{q}%")).order_by(PLC.name).limit(10):
            results.append({"kind": "plc", "label": plc.name, "detail": gettext("PLC"),
                            "url": url_for("admin.plc_edit", plc_id=plc.id), "icon": "hdd-network"})
    return jsonify(results=results[:30])
