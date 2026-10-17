"""Automations blueprint: "when … and if … then do …" rules, like Home Assistant.

* :mod:`app.automations.schema` – building blocks and validation;
* :mod:`app.automations.engine` – trigger evaluation thread and runs;
* :mod:`app.automations.documents` – editor/YAML documents, import, export;
* :mod:`app.automations.routes` – the pages (``/automations``).
"""

from flask import Blueprint

bp = Blueprint("automations", __name__)

from app.automations import routes  # noqa: E402,F401  (registers the view functions)
