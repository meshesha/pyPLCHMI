"""Information library blueprint: documents (images, PDFs) attached to PLCs / devices.

Pages and JSON API are in :mod:`app.documents.routes`, file handling (type
check, SHA-1, duplicate detection) in :mod:`app.documents.storage`.
"""

from flask import Blueprint

bp = Blueprint("documents", __name__)

from app.documents import routes  # noqa: E402,F401  (registers the view functions)
