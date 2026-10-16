"""Audit log page (*Admin → Audit log*, permission ``view_audit``) and its Excel export."""

from __future__ import annotations

from flask import render_template, request

from app.audit import bp
from app.audit.recorder import OBJECT_TYPES
from app.extensions import db
from app.models import AuditLog, Perm
from app.models.audit import ACTIONS
from app.utils.decorators import permission_required
from app.utils.tables import parse_state, table_page, table_query
from app.utils.timezones import format_local, parse_local, user_zone
from app.utils.xlsx import build_workbook, send_xlsx

SORT_COLUMNS = {"time": AuditLog.ts, "user": AuditLog.username, "action": AuditLog.action,
                "type": AuditLog.object_type, "object": AuditLog.object_label}
SEARCH_COLUMNS = (AuditLog.username, AuditLog.object_label, AuditLog.changes_json, AuditLog.ip)
FILTERS = ("user", "action", "type", "from", "to")


def _filters() -> dict:
    """Filter functions of the audit table (``user``, ``action``, ``type``, ``from``, ``to``).

    Returns:
        ``name -> function(query, value)``.
    """
    zone = user_zone()

    def from_filter(q, v):
        """Entries at or after a local date/time."""
        start = parse_local(v, zone)
        return q.filter(AuditLog.ts >= start) if start else q

    def to_filter(q, v):
        """Entries at or before a local date/time (a date alone means the whole day)."""
        end = parse_local(v, zone)
        if end is None:
            return q
        if len(v.strip()) == 10:  # YYYY-MM-DD → end of that day
            from datetime import timedelta  # noqa: PLC0415

            return q.filter(AuditLog.ts < end + timedelta(days=1))
        return q.filter(AuditLog.ts <= end)

    return {"user": lambda q, v: q.filter(AuditLog.username == v),
            "action": lambda q, v: q.filter(AuditLog.action == v),
            "type": lambda q, v: q.filter(AuditLog.object_type == v),
            "from": from_filter, "to": to_filter}


@bp.route("/")
@permission_required(Perm.VIEW_AUDIT)
def index():
    """The audit log with search, filters, sorting and pagination.

    Returns:
        The rendered page.
    """
    state = parse_state(request.args, SORT_COLUMNS, "time", "desc", filter_names=FILTERS, default_per_page=50)
    pagination = table_page(AuditLog.query, state, SORT_COLUMNS, SEARCH_COLUMNS, _filters())
    users = [row[0] for row in db.session.query(AuditLog.username).distinct().order_by(AuditLog.username) if row[0]]
    zone = user_zone()
    return render_template("audit/index.html", pagination=pagination, state=state,
                           user_options=[(u, u) for u in users], action_options=list(ACTIONS.items()),
                           type_options=[(t, t) for t in OBJECT_TYPES], format_local=format_local, zone=zone)


@bp.route("/export.xlsx")
@permission_required(Perm.VIEW_AUDIT)
def export():
    """Download the filtered audit log as an Excel file (at most 100,000 rows).

    Returns:
        The .xlsx download.
    """
    state = parse_state(request.args, SORT_COLUMNS, "time", "desc", filter_names=FILTERS)
    query = table_query(AuditLog.query, state, SORT_COLUMNS, SEARCH_COLUMNS, _filters()).limit(100_000)
    zone = user_zone()
    columns = [("Date & time", 20), ("User", 14), ("IP", 15), ("Action", 14), ("Object type", 14),
               ("Object", 28), ("Field", 18), ("Before", 40), ("After", 40)]

    def rows():
        """One row per changed field (one row for actions without fields)."""
        for entry in query.yield_per(1000):
            base = [format_local(entry.ts, zone), entry.username, entry.ip, entry.action_label, entry.object_type,
                    entry.object_label]
            changes = entry.changes or [{"field": "", "before": None, "after": None}]
            for change in changes:
                yield base + [change.get("field", ""), _cell(change.get("before")), _cell(change.get("after"))]

    help_rows = [("Date & time", f"Local time ({getattr(zone, 'key', zone)})."),
                 ("Field / Before / After", "One row per changed value; passwords are never shown.")]
    return send_xlsx(build_workbook("Audit log", columns, rows(), help_rows), "audit-log")


def _cell(value):
    """Excel cell value of a before/after value.

    Args:
        value: Stored value.

    Returns:
        Number, bool or text.
    """
    return value if value is None or isinstance(value, (bool, int, float)) else str(value)
