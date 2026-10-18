"""Administration blueprint: users, roles/permissions and PLC connections."""

from flask import Blueprint

bp = Blueprint("admin", __name__)

from app.admin import routes, web_devices  # noqa: E402,F401  (registers the view functions)
