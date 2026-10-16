"""Protocol-independent error types for tag-based drivers (Modbus, BACnet).

The connection manager only needs to know two kinds of failure:

* :class:`ConnectionLostError` – the device/link does not answer; the manager
  drops the connection and applies the reconnect back-off.
* :class:`DeviceError` – the device answered with an error (illegal address,
  unknown object, write denied); only that tag fails, the connection stays.
"""


class ConnectionLostError(Exception):
    """The device or link stopped answering."""


class DeviceError(Exception):
    """The device rejected a request but is reachable."""
