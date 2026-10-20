"""Modbus TCP server: publishes tag values to other SCADA systems (read only).

* :mod:`app.modbus_server.encoding` – Modbus types, value order, addresses.
* :mod:`app.modbus_server.config` – server settings.
* :mod:`app.modbus_server.layout` – automatic addresses and overlap checks.
* :mod:`app.modbus_server.server` – the server and the background publisher.
* :mod:`app.modbus_server.routes` – the administration page (*Admin → Modbus TCP server*).
"""

from flask import Blueprint

bp = Blueprint("modbus_server", __name__)

from app.modbus_server import routes  # noqa: E402,F401  (registers the view functions)
