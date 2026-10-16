"""Alarms blueprint: alarm definitions (admin), event history (all users) and the alarm engine."""

from flask import Blueprint

bp = Blueprint("alarms", __name__)

from app.alarms import routes  # noqa: E402,F401  (registers the view functions)
