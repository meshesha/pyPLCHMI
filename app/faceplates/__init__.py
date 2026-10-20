"""Faceplates: one template screen for many identical devices, opened as pop-up windows.

* :mod:`app.faceplates.binding` – parameters, instance tags, scanning, creating tags.
* :mod:`app.faceplates.routes` – administration pages (*Admin → Faceplates*) and the
  run-time API used by ``static/js/hmi-faceplate.js``.
"""

from flask import Blueprint

bp = Blueprint("faceplates", __name__)

from app.faceplates import routes  # noqa: E402,F401  (registers the view functions)
