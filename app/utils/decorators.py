"""View decorators for permission checks."""

from functools import wraps

from flask import abort, jsonify, request
from flask_login import current_user

from app.models import Perm
from app.i18n.catalog import gettext


def permission_required(code: str):
    """Restrict a view to users holding a permission.

    Anonymous users are sent to the login page; logged-in users without the
    permission receive *403 Forbidden* (JSON for ``/api`` routes).

    Args:
        code: Permission code, one of the :class:`~app.models.Perm` constants.

    Returns:
        A decorator for Flask view functions.

    Example::

        @bp.route("/plcs")
        @permission_required(Perm.MANAGE_PLCS)
        def plcs(): ...
    """

    def decorator(view):
        """Wrap ``view`` with the permission check.

        Args:
            view: The view function.

        Returns:
            The wrapped view.
        """

        @wraps(view)
        def wrapped(*args, **kwargs):
            """Run the check, then the original view.

            Args:
                *args: Positional view arguments.
                **kwargs: URL parameters.

            Returns:
                The original view's response, or an error response.
            """
            if not current_user.is_authenticated:
                from app.extensions import login_manager  # noqa: PLC0415

                if request.blueprint == "api":
                    return jsonify(error=gettext('authentication required')), 401
                return login_manager.unauthorized()
            if not current_user.can(code):
                if request.blueprint == "api":
                    return jsonify(error=gettext('forbidden')), 403
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def admin_required(view):
    """Shortcut for ``@permission_required(Perm.MANAGE_ROLES)``.

    Args:
        view: The view function.

    Returns:
        The wrapped view.
    """
    return permission_required(Perm.MANAGE_ROLES)(view)
