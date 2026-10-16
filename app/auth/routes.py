"""Authentication views."""

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.audit import recorder as audit
from app.auth import bp
from app.auth.forms import ChangePasswordForm, LoginForm
from app.extensions import db
from app.models import User
from app.models.user import THEMES
from app.main.routes import landing_url
from app.utils.security import is_safe_redirect
from app.i18n.catalog import gettext


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Show the login form and authenticate the user.

    On success the user is redirected to the ``next`` URL (if it is on this
    site) or to the home page.  The same generic error is shown for unknown
    users and wrong passwords so usernames cannot be probed.

    Returns:
        The rendered login page or a redirect.
    """
    if current_user.is_authenticated:
        return redirect(landing_url())
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip()).first()
        if user is not None and user.check_password(form.password.data):
            if not user.is_active:
                flash(gettext('This account is disabled.'), "danger")
                return render_template("auth/login.html", form=form), 403
            login_user(user, remember=form.remember.data)
            user.touch_login()
            db.session.commit()
            audit.record("login", "User", user.id, user.username)
            target = request.args.get("next")
            # Priority: requested page (?next=) → personal home screen → screen list.
            return redirect(target if is_safe_redirect(target) else landing_url())
        audit.record("login_failed", "User", user.id if user else None, form.username.data.strip()[:64],
                     username=form.username.data.strip())
        flash(gettext('Invalid username or password.'), "danger")
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """Log the current user out (POST only, protected by CSRF).

    Afterwards the visitor lands on the public home screen configured in
    *System settings*; without one, on the login page.

    Returns:
        Redirect to the public home screen or the login page.
    """
    audit.record("logout", "User", current_user.id, current_user.username)
    logout_user()
    session.pop("lang", None)  # the next visitor gets the default language
    flash(gettext('You have been logged out.'), "info")
    return redirect(landing_url())


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Settings page: account details, colour theme, language and the change-password form.

    Returns:
        The rendered profile page or a redirect after a password change.
    """
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append(gettext('Current password is incorrect.'))
        else:
            current_user.set_password(form.new_password.data)
            db.session.commit()
            flash(gettext('Password changed.'), "success")
            return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html", form=form, themes=THEMES)


@bp.route("/theme", methods=["POST"])
@login_required
def set_theme():
    """Save the user's colour theme (Settings page).

    Form field ``theme``: ``dark``, ``light`` or ``auto``.

    Returns:
        Redirect back to the Settings page.
    """
    theme = request.form.get("theme", "")
    if theme not in THEMES:
        flash(gettext("Unknown colour theme."), "danger")
        return redirect(url_for("auth.profile")), 302
    current_user.theme = theme
    db.session.commit()
    flash(gettext("Colour theme saved."), "success")
    return redirect(url_for("auth.profile"))
