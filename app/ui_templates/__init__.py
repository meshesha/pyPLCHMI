"""UI templates blueprint: switchable looks of the user interface.

* ``/ui-templates/<id>/<file>`` serves the static files of a template;
* ``/admin/ui-templates`` lets administrators activate, preview, upload,
  download and delete templates (see :mod:`app.ui_templates.registry`).
"""

from flask import Blueprint

bp = Blueprint("ui_templates", __name__)

from app.ui_templates import routes  # noqa: E402,F401  (registers the view functions)
