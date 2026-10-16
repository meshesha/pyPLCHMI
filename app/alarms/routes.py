"""Alarm views: definitions (admin), details and the event history (all users)."""

from datetime import datetime, timedelta, timezone

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, exists, func
from sqlalchemy.orm import aliased

from app.alarms import bp
from app.alarms.forms import AlarmForm
from app.extensions import db
from app.models import PLC, Alarm, AlarmEvent, DataPoint
from app.models.alarm import SEVERITIES
from app.plc.service import plc_status, read_datapoints
from app.utils.decorators import admin_required
from app.alarms.excel import export_alarms, import_alarms
from app.utils.tables import parse_state, table_page, table_query
from app.utils.xlsx import ImportFileError, send_xlsx
from app.i18n.catalog import gettext

_SEVERITY_RANK = case({k: v[2] for k, v in SEVERITIES.items()}, value=Alarm.severity, else_=0)
_EVENT_RANK = case({k: v[2] for k, v in SEVERITIES.items()}, value=AlarmEvent.severity, else_=0)
#: (value, label) pairs for the severity filter.
SEVERITY_OPTIONS = [(k, v[0]) for k, v in SEVERITIES.items()]
_OPEN_EVENT = exists().where(AlarmEvent.alarm_id == Alarm.id, AlarmEvent.cleared_at.is_(None))


# ---------------------------------------------------------------------------
# Alarm definitions (admin)
# ---------------------------------------------------------------------------
def alarm_query(args):
    """Base query, table state, sort columns, search columns and filters of the alarm table.

    Args:
        args: ``request.args``.

    Returns:
        ``(query, state, sort_columns, search_columns, filters)`` for
        :func:`~app.utils.tables.table_page`.
    """
    comm_plc = aliased(PLC)
    sort_columns = {"name": Alarm.name, "severity": _SEVERITY_RANK, "enabled": Alarm.enabled,
                    "tag": func.coalesce(DataPoint.label, comm_plc.name), "state": _OPEN_EVENT}
    state = parse_state(args, sort_columns, "name", filter_names=("severity", "enabled", "state", "kind"))
    filters = {
        "severity": lambda q, v: q.filter(Alarm.severity == v),
        "enabled": lambda q, v: q.filter(Alarm.enabled.is_(v == "yes")),
        "state": lambda q, v: q.filter(_OPEN_EVENT if v == "active" else ~_OPEN_EVENT),
        "kind": lambda q, v: q.filter(Alarm.kind == v),
    }
    query = (Alarm.query.outerjoin(DataPoint, Alarm.datapoint_id == DataPoint.id)
             .outerjoin(comm_plc, Alarm.plc_id == comm_plc.id))
    return query, state, sort_columns, (Alarm.name, Alarm.description, DataPoint.label, comm_plc.name), filters


@bp.route("/")
@admin_required
def index():
    """Alarm table with search, filters (severity, enabled, state), sorting and pages.

    Returns:
        The rendered alarm list.
    """
    query, state, sort_columns, search, filters = alarm_query(request.args)
    pagination = table_page(query, state, sort_columns, search, filters)
    from app.alarms.engine import scanner_status  # noqa: PLC0415

    return render_template("alarms/index.html", pagination=pagination, state=state, severity_options=SEVERITY_OPTIONS,
                           kind_options=[("tag", "Tag condition"), ("comm", "Communication")],
                           scanner=scanner_status())


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def create():
    """Add an alarm.

    Returns:
        The form, or a redirect to the alarm list.
    """
    form = AlarmForm()
    if request.method == "GET" and request.args.get("tag", type=int):
        form.datapoint_id.data = request.args.get("tag", type=int)
    if request.method == "GET" and request.args.get("plc", type=int):
        form.kind.data, form.plc_id.data = "comm", request.args.get("plc", type=int)
    if form.validate_on_submit():
        alarm = Alarm()
        form.populate(alarm)
        db.session.add(alarm)
        db.session.commit()
        flash(gettext("Alarm '{0}' created.", alarm.name), "success")
        return redirect(url_for("alarms.index"))
    return render_template("alarms/form.html", form=form, alarm=None)


@bp.route("/<int:alarm_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(alarm_id: int):
    """Edit an alarm.  Disabling it closes its open event (manual close).

    Args:
        alarm_id: Alarm id.

    Returns:
        The form, or a redirect to the alarm list.
    """
    alarm = db.get_or_404(Alarm, alarm_id)
    form = AlarmForm(obj=alarm, alarm=alarm)
    if request.method == "GET":
        form.screen_id.data = alarm.screen_id or 0
    if form.validate_on_submit():
        was_enabled = alarm.enabled
        form.populate(alarm)
        alarm.suppressed = False
        if was_enabled and not alarm.enabled and alarm.open_event is not None:
            alarm.open_event.close("manual", current_user)
        db.session.commit()
        flash(gettext("Alarm '{0}' saved.", alarm.name), "success")
        return redirect(url_for("alarms.index"))
    return render_template("alarms/form.html", form=form, alarm=alarm)


@bp.route("/<int:alarm_id>/delete", methods=["POST"])
@admin_required
def delete(alarm_id: int):
    """Delete an alarm; its history is kept (open events are closed manually).

    Args:
        alarm_id: Alarm id.

    Returns:
        Redirect to the alarm list.
    """
    alarm = db.get_or_404(Alarm, alarm_id)
    Alarm.release_events([alarm], current_user)
    db.session.delete(alarm)
    db.session.commit()
    flash(gettext("Alarm '{0}' deleted (its history is kept).", alarm.name), "success")
    return redirect(url_for("alarms.index"))


@bp.route("/<int:alarm_id>/toggle", methods=["POST"])
@admin_required
def toggle(alarm_id: int):
    """Enable or disable an alarm from the list.

    Args:
        alarm_id: Alarm id.

    Returns:
        Redirect back to the list (keeping its filters).
    """
    alarm = db.get_or_404(Alarm, alarm_id)
    alarm.enabled = not alarm.enabled
    alarm.suppressed = False
    if not alarm.enabled and alarm.open_event is not None:
        alarm.open_event.close("manual", current_user)
    db.session.commit()
    return redirect(request.referrer or url_for("alarms.index"))


@bp.route("/<int:alarm_id>")
@admin_required
def details(alarm_id: int):
    """Alarm details: configuration, live tag value, state and recent events.

    Args:
        alarm_id: Alarm id.

    Returns:
        The rendered details page.
    """
    alarm = db.get_or_404(Alarm, alarm_id)
    recent = alarm.events.order_by(AlarmEvent.occurred_at.desc()).limit(10).all()
    if alarm.is_comm:
        status = plc_status(alarm.plc) if alarm.plc else None
        return render_template("alarms/details.html", alarm=alarm, reading=None, recent=recent, comm=status,
                               would_trigger=bool(status and status["state"] == "offline"))
    values, _ = read_datapoints([alarm.datapoint])
    reading = values.get(alarm.datapoint_id)
    return render_template("alarms/details.html", alarm=alarm, reading=reading, recent=recent, comm=None,
                           would_trigger=reading is not None and reading["quality"] == "good"
                           and alarm.evaluate(reading["value"]))


# ---------------------------------------------------------------------------
# Event history (all logged-in users)
# ---------------------------------------------------------------------------
def _parse_day(text: str):
    """Parse ``YYYY-MM-DD`` into a UTC datetime (``None`` if invalid).

    Args:
        text: Date text from the filter.

    Returns:
        ``datetime`` at 00:00 UTC or ``None``.
    """
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


@bp.route("/history")
@login_required
def history():
    """All alarm events with search, filters, sorting and pagination.

    Filters: severity, status (open / automatic / manual), date range.

    Returns:
        The rendered history page.
    """
    sort_columns = {"name": AlarmEvent.name, "occurred": AlarmEvent.occurred_at, "acked": AlarmEvent.acked_at,
                    "cleared": AlarmEvent.cleared_at, "severity": _EVENT_RANK, "close": AlarmEvent.close_type}
    state = parse_state(request.args, sort_columns, "occurred", "desc",
                        filter_names=("severity", "status", "from", "to"))

    def status_filter(q, v):
        """Filter by open / auto / manual."""
        if v == "open":
            return q.filter(AlarmEvent.cleared_at.is_(None))
        return q.filter(AlarmEvent.close_type == v)

    def from_filter(q, v):
        """Events that occurred on or after the given day."""
        day = _parse_day(v)
        return q.filter(AlarmEvent.occurred_at >= day) if day else q

    def to_filter(q, v):
        """Events that occurred on or before the given day."""
        day = _parse_day(v)
        return q.filter(AlarmEvent.occurred_at < day + timedelta(days=1)) if day else q

    filters = {"severity": lambda q, v: q.filter(AlarmEvent.severity == v), "status": status_filter,
               "from": from_filter, "to": to_filter}
    pagination = table_page(AlarmEvent.query, state, sort_columns,
                            (AlarmEvent.name, AlarmEvent.description, AlarmEvent.condition), filters)
    return render_template("alarms/history.html", pagination=pagination, state=state, severity_options=SEVERITY_OPTIONS)


# ---------------------------------------------------------------------------
# Excel export / import (admin)
# ---------------------------------------------------------------------------
@bp.route("/export", methods=["GET", "POST"])
@admin_required
def export():
    """Download alarm definitions as an Excel file.

    * ``POST`` with ``ids`` (repeatable): the selected alarms;
    * ``GET ?scope=all``: every alarm;
    * ``GET`` with the table's search/filter/sort arguments: the matching alarms.

    Returns:
        The .xlsx download.
    """
    if request.method == "POST":
        ids = [int(v) for v in request.form.getlist("ids") if str(v).isdigit()]
        alarms = Alarm.query.filter(Alarm.id.in_(ids)).order_by(Alarm.name).all() if ids else []
        scope = "selected"
    elif request.args.get("scope") == "all":
        alarms, scope = Alarm.query.order_by(Alarm.name).all(), "all"
    else:
        query, state, sort_columns, search, filters = alarm_query(request.args)
        alarms, scope = table_query(query, state, sort_columns, search, filters).all(), "filtered"
    return send_xlsx(export_alarms(alarms), f"alarms-{scope}")


@bp.route("/import", methods=["POST"])
@admin_required
def import_file():
    """Import alarm definitions from an uploaded Excel file (see :mod:`app.alarms.excel`).

    Form fields: ``file`` and ``check_only``.

    Returns:
        The import report, or a redirect with an error message.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash(gettext('Choose an Excel file (.xlsx) to import.'), "warning")
        return redirect(url_for("alarms.index"))
    try:
        result = import_alarms(upload.stream, check_only=bool(request.form.get("check_only")))
    except ImportFileError as exc:
        flash(gettext('Import failed: {0}', exc), "danger")
        return redirect(url_for("alarms.index"))
    return render_template("import_result.html", result=result, what="alarm", title="Alarm import",
                           back_url=url_for("alarms.index"), filename=upload.filename)

