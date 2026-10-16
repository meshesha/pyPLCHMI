"""Tags blueprint: the global PLC tag table (admin only)."""

from flask import Blueprint

bp = Blueprint("tags", __name__)

from app.tags import routes  # noqa: E402,F401  (registers the view functions)
