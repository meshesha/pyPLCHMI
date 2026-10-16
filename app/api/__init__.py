"""JSON API blueprint used by the dashboard JavaScript (live values, writes, PLC status)."""

from flask import Blueprint

bp = Blueprint("api", __name__)

from app.api import routes  # noqa: E402,F401  (registers the view functions)
