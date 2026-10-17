"""Views of the tag history: sample table, Excel export, trend charts and the series API.

Permissions: everything needs ``view_history``; creating, editing and
deleting saved charts also needs ``manage_screens``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db
from app.history import bp
from app.history.data import (
    DEFAULT_POINTS,
    chart_tags,
    export_workbook,
    history_tags,
    parse_tag_ids,
    samples_query,
    series_points,
)
from app.history.forms import ChartForm
from app.history.logger import logger_status, retention_days
from app.i18n.catalog import gettext
from app.models import DataPoint, Perm, TrendChart
from app.models.history import AXES, MAX_SERIES, PALETTE, RANGES
from app.utils.decorators import permission_required
from app.utils.tables import parse_state
from app.utils.timezones import format_local, parse_local, user_zone, zone_label
from app.utils.xlsx import XLSX_MIME


def _filter_args() -> tuple[list[int], datetime | None, datetime | None]:
    """Selected tags and time range of the current request.

    Query arguments: ``tags`` (``1,2,3``) and/or repeated ``t``; ``from`` and
    ``to`` in the browser's time zone (``YYYY-MM-DDTHH:MM``).

    Returns:
        ``(tag ids, start UTC, end UTC)``.
    """
    ids = parse_tag_ids(request.args.get("tags", ""), request.args.getlist("t"))
    zone = user_zone()
    return ids, parse_local(request.args.get("from"), zone), parse_local(request.args.get("to"), zone)


def _can_manage_charts() -> bool:
    """Whether the current user may create, edit and delete saved charts."""
    return current_user.is_authenticated and current_user.can(Perm.MANAGE_SCREENS)


# ---------------------------------------------------------------------------
# Sample table and Excel export
# ---------------------------------------------------------------------------
@bp.route("/")
@permission_required(Perm.VIEW_HISTORY)
def index():
    """Saved samples with tag and time filters, sorting by time and pagination.

    Returns:
        The rendered History page.
    """
    ids, start, end = _filter_args()
    state = parse_state(request.args, ("time",), "time", "desc", filter_names=("from", "to"), default_per_page=50)
    if ids:
        state.filters["tags"] = ",".join(str(i) for i in ids)
    query = samples_query(ids, start, end, newest_first=state.direction == "desc")
    pagination = query.paginate(page=state.page, per_page=state.per_page, error_out=False)
    if pagination.pages and state.page > pagination.pages:
        state.page = pagination.pages
        pagination = query.paginate(page=state.page, per_page=state.per_page, error_out=False)
    zone = user_zone()
    tags = history_tags()
    return render_template(
        "history/index.html", pagination=pagination, state=state, tags=tags, selected=set(ids), zone=zone,
        zone_name=zone_label(zone), format_local=format_local, status=logger_status(),
        retention=retention_days(), logged_count=sum(1 for dp in tags if dp.is_logged),
        bad_range=bool(start and end and start > end),
    )


@bp.route("/export.xlsx")
@permission_required(Perm.VIEW_HISTORY)
def export():
    """Download the filtered samples as an Excel file.

    ``layout=wide`` writes one column per tag instead of one row per sample.

    Returns:
        The .xlsx download.
    """
    from flask import send_file  # noqa: PLC0415
    import io  # noqa: PLC0415

    ids, start, end = _filter_args()
    layout = "wide" if request.args.get("layout") == "wide" else "list"
    data, _rows, _truncated = export_workbook(ids, start, end, user_zone(), layout)
    name = f"tag-history-{datetime.now(user_zone()).strftime('%Y%m%d-%H%M')}.xlsx"
    return send_file(io.BytesIO(data), mimetype=XLSX_MIME, as_attachment=True, download_name=name)


# ---------------------------------------------------------------------------
# Trend charts
# ---------------------------------------------------------------------------
@bp.route("/charts")
@permission_required(Perm.VIEW_HISTORY)
def charts():
    """List of saved charts and the quick-chart tag picker.

    Returns:
        The rendered charts page.
    """
    items = TrendChart.query.order_by(TrendChart.name).all()
    return render_template("history/charts.html", charts=items, tags=chart_tags(), can_manage=_can_manage_charts(),
                           ranges=RANGES, max_series=MAX_SERIES)


def _viewer_series(series: list[dict]) -> list[dict]:
    """Series as JSON-ready dicts for the chart viewer.

    Args:
        series: Resolved series (with ``datapoint``).

    Returns:
        Dicts with tag id, label, unit, colour, axis, BOOL flag and texts.
    """
    out = []
    for s in series:
        dp = s["datapoint"]
        out.append({"tag_id": dp.id, "label": dp.label, "unit": dp.unit, "color": s["color"], "axis": s["axis"],
                    "is_bool": dp.is_bool, "decimals": dp.decimals, "true_text": dp.true_text,
                    "false_text": dp.false_text, "interval": dp.log_interval, "logged": dp.is_logged,
                    "plc": dp.plc.name if dp.plc else ""})
    return out


@bp.route("/charts/<int:chart_id>")
@permission_required(Perm.VIEW_HISTORY)
def view_chart(chart_id: int):
    """Show a saved chart.

    Args:
        chart_id: Chart id.

    Returns:
        The chart viewer.
    """
    chart = db.get_or_404(TrendChart, chart_id)
    series = _viewer_series(chart.resolved_series())
    return render_template("history/chart_view.html", chart=chart, series=series, range_key=chart.range_key,
                           ranges=RANGES, can_manage=_can_manage_charts(), title=chart.name)


@bp.route("/charts/quick")
@permission_required(Perm.VIEW_HISTORY)
def quick_chart():
    """Show an unsaved chart of the tags in ``?tags=1,2`` (default colours).

    Returns:
        The chart viewer, or a redirect to the chart list when no usable tag is given.
    """
    allowed = {dp.id: dp for dp in chart_tags()}
    ids = [i for i in parse_tag_ids(request.args.get("tags", ""), request.args.getlist("t")) if i in allowed]
    if not ids:
        flash(gettext("Choose at least one tag with history logging."), "warning")
        return redirect(url_for("history.charts"))
    ids = ids[:MAX_SERIES]
    series = [{"tag_id": i, "color": PALETTE[n % len(PALETTE)], "axis": "left", "datapoint": allowed[i]}
              for n, i in enumerate(ids)]
    range_key = request.args.get("range") if request.args.get("range") in RANGES else "1h"
    return render_template("history/chart_view.html", chart=None, series=_viewer_series(series), range_key=range_key,
                           ranges=RANGES, can_manage=_can_manage_charts(), title=gettext("Quick chart"))


def _form_page(form: ChartForm, chart: TrendChart | None):
    """Render the chart editor.

    Args:
        form: The form.
        chart: The chart being edited (``None`` for a new chart).

    Returns:
        The rendered page.
    """
    tags = [{"id": dp.id, "label": dp.label, "plc": dp.plc.name if dp.plc else "", "unit": dp.unit,
             "interval": dp.log_interval, "is_bool": dp.is_bool} for dp in chart_tags()]
    return render_template("history/chart_form.html", form=form, chart=chart, tags=tags, palette=PALETTE,
                           axes=AXES, max_series=MAX_SERIES)


@bp.route("/charts/new", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_SCREENS)
def create_chart():
    """Create a chart (``?tags=1,2`` / ``colors=…`` pre-fill the series, e.g. from a quick chart).

    Returns:
        The editor, or a redirect to the new chart.
    """
    form = ChartForm()
    if request.method == "GET":
        ids = parse_tag_ids(request.args.get("tags", ""))
        colors = [c for c in request.args.get("colors", "").split(",")]
        allowed = {dp.id for dp in chart_tags()}
        series = []
        for n, i in enumerate(i for i in ids if i in allowed):
            color = colors[n] if n < len(colors) and re.match(r"^#[0-9a-fA-F]{6}$", colors[n] or "") else \
                PALETTE[n % len(PALETTE)]
            series.append({"tag_id": i, "color": color, "axis": "left"})
        form.series.data = json.dumps(series[:MAX_SERIES])
        if request.args.get("range") in RANGES:
            form.range_key.data = request.args["range"]
    if form.validate_on_submit():
        chart = TrendChart()
        form.populate_chart(chart)
        db.session.add(chart)
        db.session.commit()
        flash(gettext("Chart '{0}' saved.", chart.name), "success")
        return redirect(url_for("history.view_chart", chart_id=chart.id))
    return _form_page(form, None)


@bp.route("/charts/<int:chart_id>/edit", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_SCREENS)
def edit_chart(chart_id: int):
    """Edit a chart.

    Args:
        chart_id: Chart id.

    Returns:
        The editor, or a redirect to the chart.
    """
    chart = db.get_or_404(TrendChart, chart_id)
    form = ChartForm(chart=chart)
    if request.method == "GET":
        form.load_chart(chart)
    if form.validate_on_submit():
        form.populate_chart(chart)
        db.session.commit()
        flash(gettext("Chart '{0}' saved.", chart.name), "success")
        return redirect(url_for("history.view_chart", chart_id=chart.id))
    return _form_page(form, chart)


@bp.route("/charts/<int:chart_id>/delete", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def delete_chart(chart_id: int):
    """Delete a chart (the samples are kept).

    Args:
        chart_id: Chart id.

    Returns:
        Redirect to the chart list.
    """
    chart = db.get_or_404(TrendChart, chart_id)
    db.session.delete(chart)
    db.session.commit()
    flash(gettext("Chart '{0}' deleted.", chart.name), "success")
    return redirect(url_for("history.charts"))


# ---------------------------------------------------------------------------
# JSON API for the chart viewer
# ---------------------------------------------------------------------------
def _epoch_arg(name: str) -> datetime | None:
    """Read an epoch-milliseconds query argument.

    Args:
        name: Argument name.

    Returns:
        UTC datetime or ``None``.
    """
    try:
        ms = int(request.args.get(name, ""))
    except ValueError:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


@bp.route("/api/series")
@permission_required(Perm.VIEW_HISTORY)
def api_series():
    """Points of one or more tags for the chart viewer.

    Query arguments: ``tags`` (``1,2``), ``start`` / ``end`` (epoch ms;
    default: the last hour), ``points`` (most points per tag).

    Returns:
        JSON ``{"start", "end", "series": {tag id: {"points", "count", "reduced"}}}``.
    """
    ids = parse_tag_ids(request.args.get("tags", ""))
    end = _epoch_arg("end") or datetime.now(timezone.utc)
    start = _epoch_arg("start") or end - timedelta(hours=1)
    if start >= end:
        return jsonify(error=gettext("The start of the range must be before its end.")), 400
    try:
        points = int(request.args.get("points", DEFAULT_POINTS))
    except ValueError:
        points = DEFAULT_POINTS
    tags = DataPoint.query.filter(DataPoint.id.in_(ids)).all() if ids else []
    series = {str(dp.id): series_points(dp, start, end, points) for dp in tags}
    return jsonify(start=int(start.timestamp() * 1000), end=int(end.timestamp() * 1000), series=series)
