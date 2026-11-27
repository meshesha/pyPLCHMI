"""WSGI entry point.

Used by ``flask`` (via ``FLASK_APP=wsgi.py`` in ``.flaskenv``) and by
production servers, e.g.::

    waitress-serve --host 0.0.0.0 --port 8000 wsgi:app      (Windows/Linux)
    gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 wsgi:app      (Linux)

Use a single worker *process* (threads are fine): every process keeps its own
PLC connections, and S7 CPUs accept only a limited number of connections.
"""

import os

from app import create_app, start_background_workers
from app.extensions import db
from app.models import PLC, Dashboard, DataPoint, Permission, Role, User

app = create_app()

# Production servers: start alarms, history, automations and the Modbus publisher right away
# (not only on the first web request).  ``flask`` CLI commands such as ``flask db upgrade`` also
# load this file; they get no background threads (``flask run`` starts them on the first request).
if os.environ.get("FLASK_RUN_FROM_CLI") != "true":
    start_background_workers(app)


@app.shell_context_processor
def make_shell_context() -> dict:
    """Pre-import common objects into ``flask shell``.

    Returns:
        Mapping of names available in the interactive shell.
    """
    return {"db": db, "User": User, "Role": Role, "Permission": Permission,
            "PLC": PLC, "Dashboard": Dashboard, "DataPoint": DataPoint}
