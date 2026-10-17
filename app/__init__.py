"""pyPLCHMI – a web HMI for Siemens S7 PLCs.

This module contains the **application factory** :func:`create_app`.  Calling
it builds a fully configured Flask application; tests call it with
``"testing"`` to get an isolated instance with an in-memory database.
"""

import logging
import os

from flask import Flask, redirect, render_template, request

from app.extensions import HMISessionInterface, bcrypt, csrf, db, login_manager, migrate
from config import config


def create_app(config_name: str | None = None) -> Flask:
    """Create and configure a Flask application instance.

    Steps: load the configuration class, initialise extensions, register
    blueprints, error handlers, template helpers, CLI commands, and create
    the shared PLC connection manager.

    Args:
        config_name: ``"development"``, ``"testing"`` or ``"production"``.
            Defaults to the ``FLASK_CONFIG`` environment variable, then
            ``"default"`` (development).

    Returns:
        The configured :class:`flask.Flask` application.
    """
    config_name = config_name or os.environ.get("FLASK_CONFIG", "default")
    cfg = config[config_name]

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(cfg)
    cfg.init_app(app)
    os.makedirs(app.instance_path, exist_ok=True)

    _init_extensions(app)
    _register_blueprints(app)
    _register_legacy_redirects(app)
    _register_error_handlers(app)
    _register_template_helpers(app)
    _init_plc_manager(app)

    _init_alarm_scanner(app)
    _init_history_logger(app)
    _init_automation_engine(app)

    from app.cli import register_cli  # noqa: PLC0415

    register_cli(app)

    if not app.debug and not app.testing:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return app


def _init_extensions(app: Flask) -> None:
    """Bind the extension singletons from :mod:`app.extensions` to ``app``.

    Args:
        app: The application being created.
    """
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)  # batch mode = SQLite friendly ALTERs
    bcrypt.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    app.session_interface = HMISessionInterface()

    from app.audit.recorder import install as install_audit  # noqa: PLC0415

    install_audit()

    from app.models import AnonymousUser, User  # noqa: PLC0415

    login_manager.anonymous_user = AnonymousUser

    @login_manager.user_loader
    def load_user(user_id: str):
        """Flask-Login callback: load a user from the id stored in the session.

        Args:
            user_id: The user id as a string.

        Returns:
            The :class:`User` or ``None`` if it no longer exists.
        """
        return db.session.get(User, int(user_id))


def _register_blueprints(app: Flask) -> None:
    """Attach every blueprint (feature module) to the application.

    Args:
        app: The application being created.
    """
    from app.admin import bp as admin_bp  # noqa: PLC0415
    from app.api import bp as api_bp  # noqa: PLC0415
    from app.alarms import bp as alarms_bp  # noqa: PLC0415
    from app.assets import bp as assets_bp  # noqa: PLC0415
    from app.tags import bp as tags_bp  # noqa: PLC0415
    from app.auth import bp as auth_bp  # noqa: PLC0415
    from app.dashboards import bp as dashboards_bp  # noqa: PLC0415
    from app.main import bp as main_bp  # noqa: PLC0415
    from app.i18n.routes import bp as i18n_bp  # noqa: PLC0415
    from app.history import bp as history_bp  # noqa: PLC0415
    from app.documents import bp as documents_bp  # noqa: PLC0415
    from app.audit import bp as audit_bp  # noqa: PLC0415
    from app.ui_templates import bp as ui_templates_bp  # noqa: PLC0415
    from app.automations import bp as automations_bp  # noqa: PLC0415

    app.register_blueprint(main_bp)
    app.register_blueprint(i18n_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    # Screens (grid or image).  Internally the model/blueprint keep the older
    # name "dashboard"; all URLs and UI text say "screen".
    app.register_blueprint(dashboards_bp, url_prefix="/screens")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(assets_bp, url_prefix="/assets")
    app.register_blueprint(tags_bp, url_prefix="/tags")
    app.register_blueprint(alarms_bp, url_prefix="/alarms")
    app.register_blueprint(history_bp, url_prefix="/history")
    app.register_blueprint(documents_bp, url_prefix="/library")
    app.register_blueprint(audit_bp, url_prefix="/admin/audit")
    app.register_blueprint(ui_templates_bp)
    app.register_blueprint(automations_bp, url_prefix="/automations")


def _register_legacy_redirects(app: Flask) -> None:
    """Keep old bookmarks working after "dashboards" was renamed to "screens".

    ``/dashboards/<anything>`` permanently redirects to ``/screens/<anything>``.

    Args:
        app: The application being created.
    """

    def legacy_dashboards(rest: str = ""):
        """Redirect an old /dashboards/... URL to /screens/....

        Args:
            rest: The remainder of the old path.

        Returns:
            A 301 redirect.
        """
        target = "/screens/" + rest
        if request.query_string:
            target += "?" + request.query_string.decode("latin-1")
        return redirect(target, code=301)

    app.add_url_rule("/dashboards/", "legacy_dashboards", legacy_dashboards)
    app.add_url_rule("/dashboards/<path:rest>", "legacy_dashboards", legacy_dashboards)


def _register_error_handlers(app: Flask) -> None:
    """Install HTML error pages for 403, 404 and 500.

    API routes return JSON errors themselves (see :mod:`app.api`).

    Args:
        app: The application being created.
    """

    @app.errorhandler(403)
    def forbidden(error):
        """Render the *access denied* page.

        Args:
            error: The HTTP exception.

        Returns:
            Response tuple ``(html, 403)``.
        """
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        """Render the *page not found* page.

        Args:
            error: The HTTP exception.

        Returns:
            Response tuple ``(html, 404)``.
        """
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        """Roll back the DB session and render the *server error* page.

        Args:
            error: The original exception.

        Returns:
            Response tuple ``(html, 500)``.
        """
        db.session.rollback()
        return render_template("errors/500.html"), 500


def _register_template_helpers(app: Flask) -> None:
    """Expose ``Perm`` and global settings to every Jinja template.

    Args:
        app: The application being created.
    """
    from app.models import Perm  # noqa: PLC0415

    @app.context_processor
    def inject_globals() -> dict:
        """Template context processor.

        Returns:
            Variables available in all templates: ``Perm``,
            ``plc_simulation``, ``embed``, ``ui_theme`` (colour theme:
            ``dark``, ``light`` or ``auto`` – the user's choice, visitors get
            ``dark``; a UI template with a fixed colour mode overrides it),
            ``ui`` (the active :class:`~app.ui_templates.registry.UITemplate`)
            and ``ui_preview`` (the template an administrator previews, or ``None``).
        """
        from flask_login import current_user  # noqa: PLC0415

        from app.models.user import THEMES  # noqa: PLC0415
        from app.ui_templates.registry import active_template, previewing  # noqa: PLC0415

        theme = getattr(current_user, "theme", None) if current_user.is_authenticated else None
        from flask import has_request_context, request  # noqa: PLC0415

        ui = active_template()
        theme = theme if theme in THEMES else "dark"
        if ui.color_mode in ("light", "dark"):
            theme = ui.color_mode
        return {"Perm": Perm, "plc_simulation": app.config.get("PLC_SIMULATION", False),
                "ui_theme": theme, "ui": ui, "ui_preview": previewing(),
                # ?embed=1: the screen alone (no top bar), shown in the full-screen frame
                "embed": has_request_context() and request.args.get("embed") == "1"}

    from app.models.alarm import iso  # noqa: PLC0415
    from app.utils.tables import PAGE_SIZES  # noqa: PLC0415

    app.jinja_env.globals["iso_time"] = iso
    app.jinja_env.globals["page_sizes"] = PAGE_SIZES

    from app.i18n.catalog import available_languages, current_language, default_language, gettext  # noqa: PLC0415

    # Translation: {{ _("Screens") }}, {{ _("Tag '{0}' saved.", name) }}
    app.jinja_env.globals["_"] = gettext
    app.jinja_env.globals["current_language"] = current_language
    app.jinja_env.globals["available_languages"] = available_languages
    app.jinja_env.globals["default_language"] = default_language

    @app.template_global()
    def ui_nav() -> list:
        """Pages for the sidebar layout of the UI templates, grouped.

        Returns:
            ``[(workspace, group label, [NavItem])]`` (see :mod:`app.ui_templates.nav`).
        """
        from flask_login import current_user  # noqa: PLC0415

        from app.ui_templates.nav import grouped, nav_items  # noqa: PLC0415
        from app.ui_templates.registry import active_template  # noqa: PLC0415

        return grouped(nav_items(current_user, active_template().feature("home") == "overview"))

    @app.template_global()
    def nav_screens(limit: int = 40) -> list:
        """Screens the current user may open (sidebar list), ordered by name.

        Args:
            limit: Most screens returned.

        Returns:
            List of :class:`Dashboard`.
        """
        from flask_login import current_user  # noqa: PLC0415

        from app.models import Dashboard  # noqa: PLC0415

        return [d for d in Dashboard.query.order_by(Dashboard.name) if current_user.can_view_dashboard(d)][:limit]

    @app.template_global()
    def nav_public_screens() -> list:
        """Public screens for the visitor navigation menu (empty if public access is off).

        Returns:
            List of public :class:`Dashboard` objects ordered by name.
        """
        from app.models import Dashboard  # noqa: PLC0415
        from app.models.setting import public_access_enabled  # noqa: PLC0415

        if not public_access_enabled():
            return []
        return Dashboard.query.filter_by(is_public=True).order_by(Dashboard.name).all()


def _init_plc_manager(app: Flask) -> None:
    """Create the process-wide PLC connection manager.

    Args:
        app: The application being created.
    """
    from app.plc.driver import PLCConnectionManager  # noqa: PLC0415

    app.extensions["plc_manager"] = PLCConnectionManager.from_app(app)


def _init_alarm_scanner(app: Flask) -> None:
    """Start the background alarm scanner on the first web request.

    Starting lazily (instead of at import time) keeps CLI commands such as
    ``flask db upgrade`` free of background threads.  Disabled with
    ``ALARM_SCANNER=0`` (tests use on-demand scans instead).

    Args:
        app: The application being created.
    """
    if not app.config.get("ALARM_SCANNER", True):
        return

    @app.before_request
    def start_alarm_scanner():
        """Start the scanner thread once (first request of this process)."""
        from app.alarms.engine import AlarmScanner  # noqa: PLC0415

        AlarmScanner.start(app)


def _init_automation_engine(app: Flask) -> None:
    """Start the automation engine thread on the first web request.

    Disabled with ``AUTOMATIONS=0`` (tests call :func:`app.automations.engine.tick` directly).

    Args:
        app: The application being created.
    """
    if not app.config.get("AUTOMATIONS", True):
        return

    @app.before_request
    def start_automation_engine():
        """Start the engine thread once (and again if it has stopped)."""
        from app.automations.engine import AutomationEngine  # noqa: PLC0415

        AutomationEngine.start(app)


def _init_history_logger(app: Flask) -> None:
    """Start the background history logger (tag samples) on the first web request.

    Disabled with ``HISTORY_LOGGER=0`` (tests call
    :func:`app.history.logger.sample_due_tags` directly).

    Args:
        app: The application being created.
    """
    if not app.config.get("HISTORY_LOGGER", True):
        return

    @app.before_request
    def start_history_logger():
        """Start the logger thread once (and again if it has stopped)."""
        from app.history.logger import HistoryLogger  # noqa: PLC0415

        HistoryLogger.start(app)
