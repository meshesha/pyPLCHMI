"""Tag table views (admin only): list, add, edit and delete PLC tags."""

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import exists, func, select

from app.dashboards.routes import plc_protocol_map
from app.extensions import db
from app.models import PLC, Dashboard, DataPoint, ScreenTag
from app.tags import bp
from app.tags.forms import TagForm
from app.utils.decorators import admin_required
from app.tags.excel import export_tags, import_tags
from app.utils.tables import parse_state, table_page, table_query
from app.utils.xlsx import ImportFileError, send_xlsx
from app.i18n.catalog import gettext


#: First screen name of a tag (for sorting by screen).
_FIRST_SCREEN = (select(func.min(Dashboard.name)).join(ScreenTag, ScreenTag.dashboard_id == Dashboard.id)
                 .where(ScreenTag.datapoint_id == DataPoint.id).correlate(DataPoint).scalar_subquery())


def _screen_match(like: str):
    """SQL condition: the tag is on a screen whose name matches ``like``.

    Args:
        like: ``ILIKE`` pattern.

    Returns:
        An ``EXISTS`` expression.
    """
    return exists().where(ScreenTag.datapoint_id == DataPoint.id, ScreenTag.dashboard_id == Dashboard.id,
                          Dashboard.name.ilike(like))

TAG_SORT = {"label": DataPoint.label, "plc": PLC.name, "address": DataPoint.address_text,
            "type": DataPoint.data_type, "screen": _FIRST_SCREEN, "writable": DataPoint.writable}
TAG_SEARCH = (DataPoint.label, DataPoint.address_text, DataPoint.unit, DataPoint.data_type, PLC.name)


def _screen_filter(q, value: str):
    """Tags on a given screen (id) or on no screen (``none``).

    Args:
        q: Query.
        value: Filter value.

    Returns:
        Filtered query.
    """
    if value == "none":
        return q.filter(~exists().where(ScreenTag.datapoint_id == DataPoint.id))
    if value.isdigit():
        return q.filter(exists().where(ScreenTag.datapoint_id == DataPoint.id, ScreenTag.dashboard_id == int(value)))
    return q


TAG_FILTERS = {
    "plc": lambda q, v: q.filter(DataPoint.plc_id == int(v)) if v.isdigit() else q,
    "screen": _screen_filter,
    "writable": lambda q, v: q.filter(DataPoint.writable.is_(v == "yes")),
}


def tag_query(args):
    """The tag table query and table state for request arguments.

    The free-text search also matches screen names.

    Args:
        args: ``request.args``.

    Returns:
        ``(query, state)`` with the search already applied; pass them to
        :func:`~app.utils.tables.table_page` without search columns.
    """
    state = parse_state(args, TAG_SORT, "label", filter_names=("plc", "screen", "writable"))
    query = DataPoint.query.join(PLC, DataPoint.plc_id == PLC.id)
    if state.q:
        like = f"%{state.q}%"
        query = query.filter(db.or_(_screen_match(like), *[col.ilike(like) for col in TAG_SEARCH]))
    return query, state


def backfill_address_text() -> None:
    """Fill ``address_text`` for tags created before the tag table existed."""
    missing = DataPoint.query.filter(DataPoint.address_text == "").all()
    for dp in missing:
        dp.address_text = dp.address[:80]
    if missing:
        db.session.commit()


@bp.route("/")
@admin_required
def index():
    """All tags with search, filters (PLC, screen, writable), sorting and pages.

    Returns:
        The rendered tag table.
    """
    backfill_address_text()
    query, state = tag_query(request.args)
    pagination = table_page(query, state, TAG_SORT, (), TAG_FILTERS)
    plc_options = [(p.id, p.name) for p in PLC.query.order_by(PLC.name)]
    screen_options = [("none", gettext("— no screen —"))] + [(d.id, d.name) for d in Dashboard.query.order_by(Dashboard.name)]
    return render_template("tags/index.html", pagination=pagination, state=state,
                           plc_options=plc_options, screen_options=screen_options)


def _save(form: TagForm, dp: DataPoint) -> None:
    """Copy the form into a tag, update the screens showing it, and commit.

    Newly ticked screens get the tag at the end; unticked screens lose it.

    Args:
        form: Validated :class:`TagForm`.
        dp: Target tag (added to the session).
    """
    form.populate_datapoint(dp)
    wanted = set(form.screens.data or [])
    for link in list(dp.screen_links):
        if link.dashboard_id not in wanted:
            link.dashboard.remove_tag(dp)
    current = {link.dashboard_id for link in dp.screen_links}
    for screen in Dashboard.query.filter(Dashboard.id.in_(wanted - current)).all() if wanted - current else []:
        screen.add_tag(dp)
    db.session.commit()


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def create():
    """Add a tag (optionally on screens).

    ``?copy=<tag id>`` pre-fills the form with the settings of that tag
    (the *Copy* button of the tag table); the copy is a new tag.

    Returns:
        The form, or a redirect to the tag table.
    """
    source = db.session.get(DataPoint, request.args.get("copy", type=int)) if request.args.get("copy") else None
    form = TagForm(current_plc_id=source.plc_id if source else None)
    if request.method == "GET" and source is not None:
        form.load_datapoint(source)
        form.label.data = f"{source.label} (copy)"[:100]
    if not form.plc_id.choices:
        flash(gettext('No enabled PLC – add a PLC or enable one first (Admin → PLCs).'), "warning")
        return redirect(url_for("tags.index"))
    if form.validate_on_submit():
        dp = DataPoint()
        db.session.add(dp)
        _save(form, dp)
        flash(gettext("Tag '{0}' ({1}) added.", dp.label, dp.address), "success")
        if request.form.get("add_another"):
            return redirect(url_for("tags.create"))
        return redirect(url_for("tags.index"))
    return render_template("tags/form.html", form=form, tag=None, source=source, plc_protocols=plc_protocol_map())


@bp.route("/<int:tag_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(tag_id: int):
    """Edit a tag.

    Args:
        tag_id: Tag id.

    Returns:
        The form, or a redirect to the tag table.
    """
    dp = db.get_or_404(DataPoint, tag_id)
    form = TagForm(current_plc_id=dp.plc_id, datapoint=dp)
    if request.method == "GET":
        form.load_datapoint(dp)
        form.screens.data = [link.dashboard_id for link in dp.screen_links]
    if form.validate_on_submit():
        _save(form, dp)
        flash(gettext("Tag '{0}' saved.", dp.label), "success")
        return redirect(url_for("tags.index"))
    return render_template("tags/form.html", form=form, tag=dp, source=None, plc_protocols=plc_protocol_map())


@bp.route("/<int:tag_id>/delete", methods=["POST"])
@admin_required
def delete(tag_id: int):
    """Delete a tag unless alarms use it.

    Args:
        tag_id: Tag id.

    Returns:
        Redirect back to the tag table (keeping filters).
    """
    dp = db.get_or_404(DataPoint, tag_id)
    used_by = [a.name for a in dp.alarms]
    if used_by:
        flash(gettext("'{0}' is used by alarm(s): {1}. Delete or change those alarms first.", dp.label, ', '.join(used_by)), "danger")
    else:
        db.session.delete(dp)
        db.session.commit()
        flash(gettext("Tag '{0}' deleted.", dp.label), "success")
    return redirect(request.referrer or url_for("tags.index"))


# ---------------------------------------------------------------------------
# Excel export / import
# ---------------------------------------------------------------------------
@bp.route("/export", methods=["GET", "POST"])
@admin_required
def export():
    """Download tags as an Excel file.

    * ``POST`` with ``ids`` (repeatable): the selected tags;
    * ``GET ?scope=all``: every tag;
    * ``GET`` with the table's search/filter/sort arguments: the matching tags.

    Returns:
        The .xlsx download.
    """
    if request.method == "POST":
        ids = [int(v) for v in request.form.getlist("ids") if str(v).isdigit()]
        tags = DataPoint.query.filter(DataPoint.id.in_(ids)).order_by(DataPoint.label).all() if ids else []
        scope = "selected"
    elif request.args.get("scope") == "all":
        tags, scope = DataPoint.query.order_by(DataPoint.label, DataPoint.id).all(), "all"
    else:
        query, state = tag_query(request.args)
        tags, scope = table_query(query, state, TAG_SORT, (), TAG_FILTERS).all(), "filtered"
    return send_xlsx(export_tags(tags), f"tags-{scope}")


@bp.route("/import", methods=["POST"])
@admin_required
def import_file():
    """Import tags from an uploaded Excel file (see :mod:`app.tags.excel`).

    Form fields: ``file`` and ``check_only``.

    Returns:
        The import report, or a redirect with an error message.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash(gettext('Choose an Excel file (.xlsx) to import.'), "warning")
        return redirect(url_for("tags.index"))
    try:
        result = import_tags(upload.stream, check_only=bool(request.form.get("check_only")))
    except ImportFileError as exc:
        flash(gettext('Import failed: {0}', exc), "danger")
        return redirect(url_for("tags.index"))
    return render_template("import_result.html", result=result, what="tag", title="Tag import",
                           back_url=url_for("tags.index"), filename=upload.filename)

