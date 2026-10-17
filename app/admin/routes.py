"""Administration views: users, roles/permissions and PLCs."""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin import bp
from app.admin.forms import PLCForm, RoleForm, SettingsForm, UserForm
from app.extensions import db
from app.history.logger import RETENTION_SETTING, retention_days
from app.models import PLC, Alarm, Dashboard, DataPoint, Perm, Permission, Role, User
from app.models.setting import PUBLIC_HOME_SCREEN, get_setting, public_home_screen, set_setting
from app.models.user import DEFAULT_ROLES
from app.plc.driver import PLCError, get_manager
from app.utils.decorators import admin_required, permission_required
from app.i18n.catalog import gettext

ADMIN_PERMS = (Perm.MANAGE_USERS, Perm.MANAGE_ROLES, Perm.MANAGE_PLCS)


@bp.route("/")
@login_required
def index():
    """Administration overview with counters and links.

    Returns:
        The rendered overview, or 403 if the user has no admin permission.
    """
    if not any(current_user.can(p) for p in ADMIN_PERMS):
        abort(403)
    return render_template(
        "admin/index.html",
        user_count=User.query.count(),
        role_count=Role.query.count(),
        plc_count=PLC.query.count(),
        public_home=public_home_screen(),
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def _other_active_admins(exclude_user_id: int) -> int:
    """Count active users other than ``exclude_user_id`` who can manage roles.

    Args:
        exclude_user_id: The user being changed or deleted.

    Returns:
        Number of remaining active administrators.
    """
    users = User.query.filter(User.id != exclude_user_id, User.active.is_(True)).all()
    return sum(1 for u in users if u.can(Perm.MANAGE_ROLES))


@bp.route("/users")
@permission_required(Perm.MANAGE_USERS)
def users():
    """List all user accounts.

    Returns:
        The rendered user list.
    """
    return render_template("admin/users.html", users=User.query.order_by(User.username).all())


@bp.route("/users/new", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_USERS)
def user_create():
    """Create a user account.

    Returns:
        The form, or a redirect to the user list after saving.
    """
    form = UserForm()
    if request.method == "GET":
        viewer = Role.query.filter_by(name="Viewer").first()
        if viewer:
            form.role_id.data = viewer.id
    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip(),
            email=(form.email.data or "").strip() or None,
            role_id=form.role_id.data,
            active=form.active.data,
            password=form.password.data,
            language=form.language.data or None,
        )
        form.apply_screen_access(user)
        db.session.add(user)
        db.session.commit()
        flash(gettext("User '{0}' created.", user.username), "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", form=form, user=None)


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_USERS)
def user_edit(user_id: int):
    """Edit a user account (role, status, optional new password).

    Prevents demoting or disabling the last active administrator.

    Args:
        user_id: Id of the user to edit.

    Returns:
        The form, or a redirect after saving.
    """
    user = db.get_or_404(User, user_id)
    form = UserForm(obj=user, user=user)
    if form.validate_on_submit():
        new_role = db.session.get(Role, form.role_id.data)
        loses_admin = user.can(Perm.MANAGE_ROLES) and (
            not form.active.data or new_role is None or not new_role.has_permission(Perm.MANAGE_ROLES)
        )
        if loses_admin and _other_active_admins(user.id) == 0:
            flash(gettext('You cannot demote or disable the last administrator.'), "danger")
            return render_template("admin/user_form.html", form=form, user=user)
        user.username = form.username.data.strip()
        user.email = (form.email.data or "").strip() or None
        user.role = new_role
        user.active = form.active.data
        user.language = form.language.data or None
        if form.password.data:
            user.set_password(form.password.data)
        form.apply_screen_access(user)
        db.session.commit()
        flash(gettext("User '{0}' updated.", user.username), "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", form=form, user=user)


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@permission_required(Perm.MANAGE_USERS)
def user_delete(user_id: int):
    """Delete a user account (not yourself, not the last administrator).

    Args:
        user_id: Id of the user to delete.

    Returns:
        Redirect to the user list.
    """
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash(gettext('You cannot delete your own account.'), "danger")
    elif user.can(Perm.MANAGE_ROLES) and _other_active_admins(user.id) == 0:
        flash(gettext('You cannot delete the last administrator.'), "danger")
    else:
        db.session.delete(user)
        db.session.commit()
        flash(gettext("User '{0}' deleted.", user.username), "success")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------------------
# Roles & permissions
# ---------------------------------------------------------------------------
@bp.route("/roles", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_ROLES)
def roles():
    """Show and save the role × permission matrix.

    The form posts one checkbox per (role, permission) named
    ``perm-<role_id>`` with the permission code as value.  The *Admin* role
    always keeps ``manage_roles`` so administrators cannot lock themselves out.

    Returns:
        The matrix page, or a redirect after saving.
    """
    all_roles = Role.query.order_by(Role.id).all()
    if request.method == "POST":
        for role in all_roles:
            codes = set(request.form.getlist(f"perm-{role.id}"))
            if role.name == "Admin":
                codes.add(Perm.MANAGE_ROLES)
            role.set_permissions(codes)
        db.session.commit()
        flash(gettext('Permissions saved.'), "success")
        return redirect(url_for("admin.roles"))
    return render_template(
        "admin/roles.html",
        roles=all_roles,
        permissions=Permission.query.order_by(Permission.id).all(),
        role_form=RoleForm(),
        default_roles=DEFAULT_ROLES,
    )


@bp.route("/roles/new", methods=["POST"])
@permission_required(Perm.MANAGE_ROLES)
def role_create():
    """Create a custom role without permissions (assign them in the matrix).

    Returns:
        Redirect to the roles page.
    """
    form = RoleForm()
    if form.validate_on_submit():
        db.session.add(Role(name=form.name.data.strip(), description=form.description.data or ""))
        db.session.commit()
        flash(gettext("Role '{0}' created.", form.name.data), "success")
    else:
        for errors in form.errors.values():
            for err in errors:
                flash(err, "danger")
    return redirect(url_for("admin.roles"))


@bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@permission_required(Perm.MANAGE_ROLES)
def role_delete(role_id: int):
    """Delete a custom role that has no users. Default roles are protected.

    Args:
        role_id: Id of the role to delete.

    Returns:
        Redirect to the roles page.
    """
    role = db.get_or_404(Role, role_id)
    if role.name in DEFAULT_ROLES:
        flash(gettext('Default roles cannot be deleted.'), "danger")
    elif role.users.count():
        flash(gettext("Reassign this role's users before deleting it."), "danger")
    else:
        role.write_tags = []  # tags limited to this role lose it (SQLite does not cascade by itself)
        db.session.delete(role)
        db.session.commit()
        flash(gettext("Role '{0}' deleted.", role.name), "success")
    return redirect(url_for("admin.roles"))


# ---------------------------------------------------------------------------
# PLCs
# ---------------------------------------------------------------------------
@bp.route("/plcs")
@permission_required(Perm.MANAGE_PLCS)
def plcs():
    """List configured PLCs with their cached connection status.

    Returns:
        The rendered PLC list.
    """
    items = PLC.query.order_by(PLC.name).all()
    manager = get_manager()
    statuses = {p.id: manager.status(p) for p in items}
    return render_template("admin/plcs.html", plcs=items, statuses=statuses)


def _fill_plc(plc: PLC, form: PLCForm) -> None:
    """Copy validated form data into a PLC model and sync its communication alarm.

    Args:
        plc: Target model (added to the session).
        form: Validated :class:`PLCForm`.
    """
    old_name = plc.name
    form.populate_plc(plc)
    db.session.flush()
    # A dummy PLC cannot lose communication: it gets no (or a disabled) communication alarm.
    wanted = bool(form.comm_alarm.data) and not plc.is_dummy
    Alarm.sync_comm_alarm(plc, wanted, form.comm_severity.data, old_name=old_name)


@bp.route("/plcs/new", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_PLCS)
def plc_create():
    """Add a PLC connection.

    Returns:
        The form, or a redirect to the PLC list after saving.
    """
    form = PLCForm()
    if form.validate_on_submit():
        plc = PLC()
        db.session.add(plc)
        _fill_plc(plc, form)
        db.session.commit()
        flash(gettext("PLC '{0}' added.", plc.name), "success")
        return redirect(url_for("admin.plcs"))
    return render_template("admin/plc_form.html", form=form, plc=None)


@bp.route("/plcs/<int:plc_id>/edit", methods=["GET", "POST"])
@permission_required(Perm.MANAGE_PLCS)
def plc_edit(plc_id: int):
    """Edit a PLC connection; the live connection is reset.

    Args:
        plc_id: Id of the PLC.

    Returns:
        The form, or a redirect after saving.
    """
    plc = db.get_or_404(PLC, plc_id)
    form = PLCForm(obj=plc, plc=plc)
    if form.validate_on_submit():
        _fill_plc(plc, form)
        db.session.commit()
        get_manager().disconnect(plc.id)
        flash(gettext("PLC '{0}' updated.", plc.name), "success")
        return redirect(url_for("admin.plcs"))
    return render_template("admin/plc_form.html", form=form, plc=plc)


@bp.route("/plcs/<int:plc_id>/delete", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def plc_delete(plc_id: int):
    """Delete a PLC together with every data point that uses it.

    Args:
        plc_id: Id of the PLC.

    Returns:
        Redirect to the PLC list.
    """
    plc = db.get_or_404(PLC, plc_id)
    get_manager().disconnect(plc.id)
    affected = plc.comm_alarms.all() + Alarm.query.join(DataPoint, Alarm.datapoint_id == DataPoint.id).filter(
        DataPoint.plc_id == plc.id).all()
    Alarm.release_events(affected, current_user)
    db.session.delete(plc)
    db.session.commit()
    flash(gettext("PLC '{0}' and its data points were deleted.", plc.name), "success")
    return redirect(url_for("admin.plcs"))


@bp.route("/plcs/<int:plc_id>/test", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def plc_test(plc_id: int):
    """Try to connect to a PLC and report the CPU information.

    Args:
        plc_id: Id of the PLC.

    Returns:
        Redirect to the PLC list with a success or error message.
    """
    plc = db.get_or_404(PLC, plc_id)
    result = get_manager().test_connection(plc)
    if result.get("ok"):
        details = ", ".join(f"{k}: {v}" for k, v in result.items() if k in ("module", "name", "state") and v)
        flash(gettext("Connected to '{0}'. {1}", plc.name, details), "success")
    else:
        flash(gettext("Connection to '{0}' failed: {1}", plc.name, result.get('error')), "danger")
    return redirect(url_for("admin.plcs"))


# ---------------------------------------------------------------------------
# System settings
# ---------------------------------------------------------------------------
@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    """Edit system-wide settings (public home screen, tag history retention).

    Returns:
        The settings page, or a redirect after saving.
    """
    form = SettingsForm()
    if request.method == "GET":
        current = public_home_screen()
        form.public_home_screen_id.data = current.id if current else 0
        form.history_retention_days.data = retention_days()
    if form.validate_on_submit():
        set_setting(PUBLIC_HOME_SCREEN, form.public_home_screen_id.data or None)
        if form.history_retention_days.data is not None:
            set_setting(RETENTION_SETTING, int(form.history_retention_days.data))
        db.session.commit()
        flash(gettext('Settings saved.'), "success")
        return redirect(url_for("admin.settings"))
    stored = get_setting(PUBLIC_HOME_SCREEN)
    broken = stored is not None and public_home_screen() is None
    public_count = Dashboard.query.filter_by(is_public=True).count()
    return render_template("admin/settings.html", form=form, broken=broken, public_count=public_count)


@bp.route("/plcs/<int:plc_id>/browse")
@permission_required(Perm.MANAGE_PLCS)
def plc_browse(plc_id: int):
    """List the objects of a BACnet device (from its objectList).

    Args:
        plc_id: Id of a BACnet PLC.

    Returns:
        The object browser page (with an error message if the device does not answer).
    """
    plc = db.get_or_404(PLC, plc_id)
    if not plc.is_bacnet:
        flash(gettext('Object browsing is available for BACnet devices only.'), "warning")
        return redirect(url_for("admin.plcs"))
    rows, error = [], None
    try:
        rows = get_manager().bacnet_browse(plc)
    except PLCError as exc:
        error = str(exc)
    return render_template("admin/plc_browse.html", plc=plc, rows=rows, error=error)
