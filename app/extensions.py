"""Flask extension singletons.

Extensions are instantiated here *without* an application and bound later in
:func:`app.create_app` via ``init_app``.  This is what makes the application
factory pattern work: modules can import ``db`` or ``bcrypt`` at import time
without needing an application object.
"""

from flask import request, session
from flask.sessions import SecureCookieSessionInterface
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

#: SQLAlchemy ORM integration (models inherit from ``db.Model``).
db = SQLAlchemy()
#: Alembic-based schema migrations (``flask db ...`` commands).
migrate = Migrate()
#: Password hashing with bcrypt.
bcrypt = Bcrypt()


class HMILoginManager(LoginManager):
    """Flask-Login manager whose "strong" session protection ignores sessions without a user.

    Flask-Login checks every request whose session is not empty.  A visitor's
    session (CSRF token, language, flash messages) has no login identifier,
    so the check "failed" on *every* anonymous request and rewrote the
    session cookie (and deleted the remember-me cookie).  When such a request
    was still running while the user logged in – e.g. the value poll of a
    public screen in another tab, slow because of a PLC timeout – its
    response replaced the fresh login cookie and the user was logged out on
    the next page load.  There is nothing to protect in a session without a
    logged-in user, so it is left untouched.
    """

    def _session_protection_failed(self):
        """Run Flask-Login's check only for sessions that carry a logged-in user.

        Returns:
            ``True`` if the session was invalidated (see Flask-Login).
        """
        if "_user_id" not in session:
            return False
        return super()._session_protection_failed()


class HMISessionInterface(SecureCookieSessionInterface):
    """Cookie sessions that are never re-sent by read-only API polls.

    Screens poll ``GET /api/...`` and ``/history/api/...`` every second.  These
    requests never change the login state on purpose, so their responses
    must not carry a session cookie: a poll that started before a login or
    logout and ends after it would otherwise put the old session back.
    """

    @staticmethod
    def read_only_request() -> bool:
        """Whether the current request is a read-only JSON poll (GET/HEAD to the APIs)."""
        return request.method in ("GET", "HEAD") and (request.blueprint == "api"
                                                      or request.endpoint == "history.api_series")

    def save_session(self, app, sess, response) -> None:
        """Save the session cookie, except for read-only API polls.

        Args:
            app: The application.
            sess: The session.
            response: The response.
        """
        if self.read_only_request():
            return
        super().save_session(app, sess, response)


#: Session-based user authentication.
login_manager = HMILoginManager()
#: CSRF protection for forms and for AJAX calls (``X-CSRFToken`` header).
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"
login_manager.session_protection = "strong"
