"""Tag history blueprint: saved samples (table + Excel export) and trend charts.

The background :class:`~app.history.logger.HistoryLogger` saves the samples;
the pages are in :mod:`app.history.routes`.
"""

from flask import Blueprint

bp = Blueprint("history", __name__)

from app.history import routes  # noqa: E402,F401  (registers the view functions)
