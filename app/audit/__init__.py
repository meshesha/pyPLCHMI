"""Audit log: recorder (automatic and explicit entries) and the page under *Admin → Audit log*."""

from flask import Blueprint

bp = Blueprint("audit", __name__)

from app.audit import routes  # noqa: E402,F401  (registers the view functions)
