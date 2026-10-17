"""Dashboard views: run-time screen and configuration pages."""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.dashboards import bp
from app.dashboards.forms import DashboardForm, DataPointForm, enabled_plcs
from app.dashboards.icons import available as icons_available
from app.dashboards.icons import shapes_for_layout as icon_shapes_for_layout
from app.dashboards import svg_import
from app.dashboards.layout import BUTTON_ACTIONS, empty_layout, page_style
from app.dashboards.svg_import import import_svg
from app.extensions import db
from app.models import PLC, Alarm, AlarmEvent, Asset, Dashboard, DataPoint, Perm, User
from app.models.setting import PUBLIC_HOME_SCREEN, get_setting, set_setting
from app.plc.addressing import AREA_CHOICES
from app.plc.datatypes import DATA_TYPE_CHOICES
from app.utils.decorators import permission_required
from app.i18n.catalog import gettext


def plc_protocol_map() -> dict:
    """Map every PLC id to its protocol (used by the tag form's address help).

    Returns:
        Dict ``{"<plc id>": "s7" | "modbus_tcp" | "modbus_rtu"}``.
    """
    return {str(p.id): p.protocol or "s7" for p in PLC.query.all()}


def _get_dashboard(dashboard_id: int) -> Dashboard:
    """Load a dashboard by id or abort with 404.

    Args:
        dashboard_id: Primary key.

    Returns:
        The :class:`Dashboard`.
    """
    return db.get_or_404(Dashboard, dashboard_id)


def _get_point(dashboard: Dashboard, point_id: int) -> DataPoint:
    """Load a tag shown on ``dashboard`` or abort with 404.

    Args:
        dashboard: The screen.
        point_id: Tag id.

    Returns:
        The :class:`DataPoint`.
    """
    dp = db.get_or_404(DataPoint, point_id)
    if not dashboard.has_tag(dp):
        abort(404)
    return dp


# ---------------------------------------------------------------------------
# Run-time view
# ---------------------------------------------------------------------------
@bp.route("/<slug>")
def view(slug: str):
    """Display a screen; values are refreshed by JavaScript via the API.

    Visitors who are not logged in can open *public* screens while a public
    home screen is configured.  Anyone opening a screen they may not see gets
    the *Restricted Area* page (status 403) instead.

    Args:
        slug: URL slug of the screen.

    Returns:
        The rendered HMI screen, or the Restricted Area page.
    """
    dashboard = Dashboard.query.filter_by(slug=slug).first_or_404()
    if not current_user.can_view_dashboard(dashboard):
        return render_template("errors/restricted.html", screen=dashboard), 403
    # "Open Screen" from an alarm adds ?alarm=<event id>: show that alarm on top.
    alarm_event = None
    if current_user.is_authenticated and request.args.get("alarm", type=int):
        alarm_event = db.session.get(AlarmEvent, request.args.get("alarm", type=int))
    if dashboard.is_image:
        return render_template(
            "dashboards/mimic_view.html",
            dashboard=dashboard,
            runtime=_runtime_data(dashboard),
            page_style=page_style(dashboard.layout_data,
                                  lambda asset_id: url_for("assets.file", asset_id=asset_id)),
            can_edit=current_user.can(Perm.MANAGE_SCREENS),
            alarm_event=alarm_event,
            tag_menu=tag_menu_data(dashboard),
        )
    return render_template(
        "dashboards/view.html",
        dashboard=dashboard,
        can_edit=current_user.can(Perm.MANAGE_SCREENS),
        alarm_event=alarm_event,
        tag_menu=tag_menu_data(dashboard),
    )


def tag_menu_data(dashboard: Dashboard) -> dict:
    """Data of the right-click menu of the screen's values (*Show graph* / *Details*).

    Args:
        dashboard: The screen.

    Returns:
        JSON-ready dict: ``can_history`` (``view_history`` permission), per-tag
        label and history flag, and the URLs used by ``hmi-tagmenu.js``.
    """
    from app.history.data import tags_with_history  # noqa: PLC0415

    points = list(dashboard.datapoints)
    can_history = current_user.can(Perm.VIEW_HISTORY)
    history = tags_with_history(dp.id for dp in points) if can_history else set()
    return {
        "can_history": can_history,
        "points": {str(dp.id): {"label": dp.label, "unit": dp.unit, "history": dp.id in history,
                                "is_bool": dp.is_bool, "decimals": dp.decimals, "true_text": dp.true_text,
                                "false_text": dp.false_text, "interval": dp.log_interval}
                   for dp in points},
        "urls": {
            "details": url_for("api.datapoint_details", point_id=0),
            "series": url_for("history.api_series"),
            "trends": url_for("history.quick_chart"),
            "chartjs": [url_for("static", filename="vendor/chartjs/chart.umd.min.js"),
                        url_for("static", filename="vendor/chartjs/chartjs-adapter-date-fns.bundle.min.js")],
        },
    }


def _runtime_data(dashboard: Dashboard) -> dict:
    """Collect everything the graphic runtime/designer JavaScript needs.

    Args:
        dashboard: An ``image`` dashboard.

    Returns:
        JSON-serialisable dict with the layout, data point metadata, the
        shapes of the icons used, URLs and the user's write permission.
    """
    layout = dashboard.layout_data
    return {
        "dashboard": {"id": dashboard.id, "name": dashboard.name, "slug": dashboard.slug,
                      "refresh_ms": dashboard.refresh_ms, "description": dashboard.description},
        "layout": layout,
        "points": {str(dp.id): {**dp.to_dict(), "may_write": dp.may_write(current_user)}
                   for dp in dashboard.datapoints},
        "icons": icon_shapes_for_layout(layout),
        "can_write": current_user.can(Perm.WRITE_VALUES),
        "urls": {
            "values": url_for("api.dashboard_values", dashboard_id=dashboard.id),
            "write": url_for("api.datapoint_write", point_id=0),
            "asset": url_for("assets.file", asset_id=0),
            "go": url_for("dashboards.go", dashboard_id=0),
        },
    }


@bp.route("/go/<int:dashboard_id>")
def go(dashboard_id: int):
    """Navigate to a dashboard by id (used by links on graphic screens).

    Links store ids rather than slugs so they survive renaming.  ``?embed=1``
    (screens shown in the full-screen frame) is passed on.

    Args:
        dashboard_id: Target dashboard id.

    Returns:
        Redirect to the dashboard's view URL (access is checked there).
    """
    dashboard = db.get_or_404(Dashboard, dashboard_id)
    # Keep the full-screen frame mode (?embed=1) when moving between screens.
    extra = {"embed": "1"} if request.args.get("embed") == "1" else {}
    return redirect(url_for("dashboards.view", slug=dashboard.slug, **extra))


@bp.route("/<int:dashboard_id>/designer")
@permission_required(Perm.MANAGE_SCREENS)
def designer(dashboard_id: int):
    """Open the drag-and-drop SVG designer of an Image dashboard.

    Args:
        dashboard_id: Dashboard id.

    Returns:
        The designer page, or a redirect for grid dashboards.
    """
    dashboard = _get_dashboard(dashboard_id)
    if not dashboard.is_image:
        flash(gettext('The designer is only available for Plane screens (change the type in the screen settings).'), "warning")
        return redirect(url_for("dashboards.edit", dashboard_id=dashboard.id))
    data = _runtime_data(dashboard)
    data["can_write"] = True  # buttons are always clickable in the editor's live preview
    for point in data["points"].values():  # (the API still checks every write)
        point["may_write"] = point["writable"]
    data["plcs"] = [{"id": p.id, "name": p.name, "ip": p.endpoint, "protocol": p.protocol} for p in enabled_plcs()]
    data["assets"] = [a.to_dict() for a in Asset.query.order_by(Asset.category, Asset.name)]
    data["dashboards"] = [{"id": d.id, "name": d.name} for d in Dashboard.query.order_by(Dashboard.name)]
    data["data_types"] = DATA_TYPE_CHOICES
    data["areas"] = AREA_CHOICES
    data["button_actions"] = list(BUTTON_ACTIONS)
    data["icons_available"] = icons_available()
    data["urls"].update({
        "layout": url_for("api.dashboard_layout", dashboard_id=dashboard.id),
        "create_point": url_for("api.datapoint_create", dashboard_id=dashboard.id),
        "add_tag": url_for("api.dashboard_tag_add", dashboard_id=dashboard.id),
        "icons": url_for("api.icon_search"),
        "icon_shape": url_for("api.icon_shape", style="__style__", name="__name__"),
        "upload": url_for("assets.upload"),
        "view": url_for("dashboards.view", slug=dashboard.slug),
        "edit": url_for("dashboards.edit", dashboard_id=dashboard.id),
        "library": url_for("assets.index"),
        "point_edit": url_for("dashboards.point_edit", dashboard_id=dashboard.id, point_id=0),
    })
    return render_template("dashboards/designer.html", dashboard=dashboard, designer=data)


# ---------------------------------------------------------------------------
# Dashboard CRUD
# ---------------------------------------------------------------------------
@bp.route("/new", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_SCREENS)
def create():
    """Create a dashboard and continue to its editor.

    Returns:
        The form, or a redirect to the editor after saving.
    """
    form = DashboardForm()
    if form.validate_on_submit():
        dashboard = Dashboard(
            name=form.name.data.strip(),
            slug=Dashboard.unique_slug(form.name.data),
            kind=form.kind.data,
            description=form.description.data or "",
            is_public=form.is_public.data,
            refresh_ms=form.refresh_ms.data,
            created_by=current_user,
        )
        if dashboard.kind == "image":
            dashboard.set_layout(empty_layout())
        db.session.add(dashboard)
        db.session.commit()
        if dashboard.is_image:
            flash(gettext("Screen '{0}' created. Draw it in the designer.", dashboard.name), "success")
            return redirect(url_for("dashboards.designer", dashboard_id=dashboard.id))
        flash(gettext("Screen '{0}' created. Now add data points.", dashboard.name), "success")
        return redirect(url_for("dashboards.edit", dashboard_id=dashboard.id))
    return render_template("dashboards/form.html", form=form, dashboard=None)


@bp.route("/<int:dashboard_id>/edit", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_SCREENS)
def edit(dashboard_id: int):
    """Edit dashboard settings and list its data points.

    Args:
        dashboard_id: Dashboard id.

    Returns:
        The editor page, or a redirect after saving the settings.
    """
    dashboard = _get_dashboard(dashboard_id)
    form = DashboardForm(obj=dashboard)
    if form.validate_on_submit():
        dashboard.name = form.name.data.strip()
        dashboard.slug = Dashboard.unique_slug(dashboard.name, exclude_id=dashboard.id)
        dashboard.kind = form.kind.data
        dashboard.description = form.description.data or ""
        dashboard.is_public = form.is_public.data
        dashboard.refresh_ms = form.refresh_ms.data
        db.session.commit()
        if not dashboard.is_public and get_setting(PUBLIC_HOME_SCREEN) == str(dashboard.id):
            flash(gettext('This is the public home screen but it is no longer public – visitors now see the login page until you make it public again or choose another one in System settings.'), "warning")
        flash(gettext('Screen saved.'), "success")
        return redirect(url_for("dashboards.edit", dashboard_id=dashboard.id))
    return render_template("dashboards/edit.html", form=form, dashboard=dashboard, plc_count=len(enabled_plcs()))


@bp.route("/import", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def import_svg_files():
    """Create Image screens from uploaded SVG files (Screens page → *Import SVG*).

    Files exported by the designer are restored completely; other drawings
    become the background of a new screen (see :mod:`app.dashboards.svg_import`).
    Each file is imported on its own: a bad file does not stop the others.

    Returns:
        Redirect to the designer of the new screen (one file) or to the list.
    """
    files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        flash(gettext('Choose one or more SVG files to import.'), "warning")
        return redirect(url_for("main.screens"))
    imported = []
    for upload in files:
        try:
            report = import_svg(upload.read(svg_import.MAX_SVG_BYTES + 1), upload.filename, current_user)
            db.session.commit()
        except svg_import.SvgImportError as exc:
            db.session.rollback()
            flash(gettext('{0}: {1}', upload.filename, exc), "danger")
            continue
        except Exception:  # noqa: BLE001 - never leave half an import behind
            db.session.rollback()
            raise
        imported.append(report.screen)
        for level, text in report.notes:
            flash(gettext('{0}: {1}', upload.filename, text), level)
    if len(imported) == 1:
        return redirect(url_for("dashboards.designer", dashboard_id=imported[0].id))
    return redirect(url_for("main.screens"))


@bp.route("/<int:dashboard_id>/delete", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def delete(dashboard_id: int):
    """Delete a screen.  Its tags stay in the tag table (only the links are removed).

    Args:
        dashboard_id: Dashboard id.

    Returns:
        Redirect to the home page.
    """
    dashboard = _get_dashboard(dashboard_id)
    # SQLite does not enforce ON DELETE SET NULL, so clear references explicitly.
    User.query.filter_by(home_screen_id=dashboard.id).update({"home_screen_id": None})
    Alarm.query.filter_by(screen_id=dashboard.id).update({"screen_id": None})
    AlarmEvent.query.filter_by(screen_id=dashboard.id).update({"screen_id": None})
    if dashboard.tag_links:
        flash(gettext('Its {0} tag(s) remain in the tag table.', len(dashboard.tag_links)), "info")
    if get_setting(PUBLIC_HOME_SCREEN) == str(dashboard.id):
        set_setting(PUBLIC_HOME_SCREEN, None)
        flash(gettext('This was the public home screen – public access is now off (System settings).'), "warning")
    db.session.delete(dashboard)
    db.session.commit()
    flash(gettext("Screen '{0}' deleted.", dashboard.name), "success")
    return redirect(url_for("main.screens"))


# ---------------------------------------------------------------------------
# Data point CRUD
# ---------------------------------------------------------------------------
@bp.route("/<int:dashboard_id>/points/new", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_SCREENS)
def point_create(dashboard_id: int):
    """Create a new tag (added to the tag table) and show it on this screen.

    Args:
        dashboard_id: Dashboard id.

    Returns:
        The form, or a redirect to the editor after saving.
    """
    dashboard = _get_dashboard(dashboard_id)
    form = DataPointForm()
    if not form.plc_id.choices:
        flash(gettext('No enabled PLC – add a PLC or enable one first (Admin → PLCs).'), "warning")
        return redirect(url_for("dashboards.edit", dashboard_id=dashboard.id))
    if request.method == "GET":
        form.position.data = max((lk.position for lk in dashboard.tag_links), default=-1) + 1
    if form.validate_on_submit():
        dp = DataPoint()
        form.populate_datapoint(dp)
        db.session.add(dp)
        dashboard.add_tag(dp, form.position.data)
        db.session.commit()
        flash(gettext("Tag '{0}' ({1}) added to the tag table and to this screen.", dp.label, dp.address), "success")
        if request.form.get("add_another"):
            return redirect(url_for("dashboards.point_create", dashboard_id=dashboard.id))
        return redirect(url_for("dashboards.edit", dashboard_id=dashboard.id))
    return render_template("dashboards/point_form.html", form=form, dashboard=dashboard, point=None,
                           plc_protocols=plc_protocol_map())


@bp.route("/<int:dashboard_id>/points/add", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def point_add_existing(dashboard_id: int):
    """Show existing tags from the tag table on this screen.

    Form fields: ``datapoint_id`` (repeatable).

    Args:
        dashboard_id: Dashboard id.

    Returns:
        Redirect to the editor.
    """
    dashboard = _get_dashboard(dashboard_id)
    ids = [int(v) for v in request.form.getlist("datapoint_id") if str(v).isdigit()]
    added = []
    for dp in DataPoint.query.filter(DataPoint.id.in_(ids)).order_by(DataPoint.label).all() if ids else []:
        if not dashboard.has_tag(dp):
            dashboard.add_tag(dp)
            added.append(dp.label)
    db.session.commit()
    if added:
        flash(gettext('Added to this screen: {0}.', ', '.join(added)), "success")
    else:
        flash(gettext('No tag added (none selected, or already on this screen).'), "info")
    return redirect(url_for("dashboards.edit", dashboard_id=dashboard.id))


@bp.route("/<int:dashboard_id>/points/<int:point_id>/edit", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_SCREENS)
def point_edit(dashboard_id: int, point_id: int):
    """Edit a tag shown on this screen (changes apply wherever the tag is used).

    Args:
        dashboard_id: Dashboard id.
        point_id: Data point id.

    Returns:
        The form, or a redirect to the editor after saving.
    """
    dashboard = _get_dashboard(dashboard_id)
    dp = _get_point(dashboard, point_id)
    link = dashboard.link_for(dp)
    form = DataPointForm(current_plc_id=dp.plc_id, datapoint=dp)
    if request.method == "GET":
        form.load_datapoint(dp)
        form.position.data = link.position
    if form.validate_on_submit():
        form.populate_datapoint(dp)
        link.position = form.position.data or 0
        db.session.commit()
        flash(gettext("Tag '{0}' saved.", dp.label), "success")
        return redirect(url_for("dashboards.edit", dashboard_id=dashboard.id))
    return render_template("dashboards/point_form.html", form=form, dashboard=dashboard, point=dp,
                           plc_protocols=plc_protocol_map())


@bp.route("/<int:dashboard_id>/points/<int:point_id>/delete", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def point_delete(dashboard_id: int, point_id: int):
    """Remove a tag from this screen; the tag stays in the tag table.

    Args:
        dashboard_id: Dashboard id.
        point_id: Data point id.

    Returns:
        Redirect to the editor.
    """
    dashboard = _get_dashboard(dashboard_id)
    dp = _get_point(dashboard, point_id)
    dashboard.remove_tag(dp)
    db.session.commit()
    flash(gettext("'{0}' removed from this screen (it is still in the tag table).", dp.label), "success")
    return redirect(url_for("dashboards.edit", dashboard_id=dashboard.id))


@bp.route("/<int:dashboard_id>/points/<int:point_id>/duplicate", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def point_duplicate(dashboard_id: int, point_id: int):
    """Copy a tag (new tag in the tag table, shown on this screen) and open the copy.

    Args:
        dashboard_id: Dashboard id.
        point_id: Data point id to copy.

    Returns:
        Redirect to the edit form of the new copy.
    """
    dashboard = _get_dashboard(dashboard_id)
    src = _get_point(dashboard, point_id)
    copy = src.clone()
    db.session.add(copy)
    dashboard.add_tag(copy)
    db.session.commit()
    return redirect(url_for("dashboards.point_edit", dashboard_id=dashboard.id, point_id=copy.id))


@bp.route("/<int:dashboard_id>/points/<int:point_id>/move/<direction>", methods=["POST"])
@permission_required(Perm.MANAGE_SCREENS)
def point_move(dashboard_id: int, point_id: int, direction: str):
    """Move a data point one place up or down in the screen order.

    Args:
        dashboard_id: Dashboard id.
        point_id: Data point id.
        direction: ``"up"`` or ``"down"``.

    Returns:
        Redirect to the editor.
    """
    dashboard = _get_dashboard(dashboard_id)
    if direction not in ("up", "down"):
        abort(400)
    links = list(dashboard.tag_links)
    link = dashboard.link_for(_get_point(dashboard, point_id))
    idx = links.index(link)
    swap = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap < len(links):
        links[idx], links[swap] = links[swap], links[idx]
        for pos, lk in enumerate(links):
            lk.position = pos
        db.session.commit()
    return redirect(url_for("dashboards.edit", dashboard_id=dashboard.id))
