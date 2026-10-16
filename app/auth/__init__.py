"""Authentication blueprint: login, logout and the user's own profile."""

from flask import Blueprint

bp = Blueprint("auth", __name__)

from app.auth import routes  # noqa: E402,F401  (registers the view functions)
