"""Main blueprint: home page listing the dashboards the user may open."""

from flask import Blueprint

bp = Blueprint("main", __name__)

from app.main import routes  # noqa: E402,F401  (registers the view functions)
