"""PLC / device connection model (Siemens S7, Modbus TCP/RTU or BACnet/IP)."""

from app.extensions import db
from app.models.user import utcnow


class PLC(db.Model):
    """A PLC or device: Siemens S7 (ISO-on-TCP, port 102) or Modbus TCP / RTU.

    For **Modbus** devices ``ip_address``/``tcp_port`` (TCP) or the serial
    settings (RTU) are used together with ``unit_id``, the byte/word order and
    the client library; ``rack``/``slot`` are ignored.

    For **BACnet/IP** devices ``ip_address``/``tcp_port`` (UDP, 47808) plus
    ``bacnet_device_id`` and the default write priority are used.

    For **S7**:

    The ``rack``/``slot`` pair identifies the CPU inside the station:

    * S7-300: rack 0, slot 2
    * S7-400: see the hardware configuration (usually rack 0, slot 2/3)
    * S7-1200 / S7-1500: rack 0, slot 1 (``PUT/GET`` must be enabled and the
      data blocks must have *optimized block access* switched off)
    * LOGO! 0BA8 and newer: rack 0, slot 1 (or use TSAP settings)
    """

    __tablename__ = "plcs"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False, index=True)
    ip_address = db.Column(db.String(64), nullable=False)
    rack = db.Column(db.Integer, nullable=False, default=0)
    slot = db.Column(db.Integer, nullable=False, default=1)
    tcp_port = db.Column(db.Integer, nullable=False, default=102)
    description = db.Column(db.String(255), nullable=False, default="")
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    #: Communication timeout (grace period) in seconds: the PLC is reported as
    #: disconnected – bad tags, red badge, communication alarm – only when
    #: communication has failed for at least this long.  Until then screens
    #: keep showing the last good values.  ``0`` = report at once.
    offline_delay = db.Column(db.Float, nullable=False, default=10.0, server_default="10.0")

    # --- protocol --------------------------------------------------------
    #: ``s7``, ``modbus_tcp`` or ``modbus_rtu`` (see :data:`app.plc.protocols.PROTOCOLS`).
    protocol = db.Column(db.String(20), nullable=False, default="s7", server_default="s7")

    # --- Modbus settings (ignored for S7) -----------------------------------
    #: Modbus TCP client library: ``pymodbus`` or ``pymodbustcp``.
    modbus_library = db.Column(db.String(20), nullable=False, default="pymodbus", server_default="pymodbus")
    #: Modbus unit / slave id.
    unit_id = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    #: Byte order inside a register (``big`` = high byte first).
    byte_order = db.Column(db.String(6), nullable=False, default="big", server_default="big")
    #: Register order for 32/64-bit values (``big`` = high word first).
    word_order = db.Column(db.String(6), nullable=False, default="big", server_default="big")
    #: Request timeout in seconds (every protocol; for S7 it is applied to the
    #: snap7 client, see :meth:`app.plc.driver.Snap7Connection._apply_timeouts`).
    timeout = db.Column(db.Float, nullable=False, default=3.0, server_default="3.0")
    #: Serial port for Modbus RTU (``COM3``, ``/dev/ttyUSB0``).
    serial_port = db.Column(db.String(64), nullable=False, default="", server_default="")
    baudrate = db.Column(db.Integer, nullable=False, default=9600, server_default="9600")
    parity = db.Column(db.String(1), nullable=False, default="N", server_default="N")
    stopbits = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    bytesize = db.Column(db.Integer, nullable=False, default=8, server_default="8")

    # --- BACnet settings (ignored for other protocols) ---------------------------
    #: BACnet device object instance of the remote device.
    bacnet_device_id = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    #: Default write priority 1..16 (8 = manual operator).
    bacnet_priority = db.Column(db.Integer, nullable=False, default=8, server_default="8")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    datapoints = db.relationship("DataPoint", back_populates="plc", cascade="all, delete-orphan", lazy="dynamic")
    #: Documents of the information library attached to this PLC (manuals, diagrams, photos).
    files = db.relationship("InfoFile", secondary="plc_files", back_populates="plcs", order_by="InfoFile.name")
    #: The communication-loss alarm(s) of this PLC (``Alarm.kind == "comm"``).
    #: Stored values of the general tags of a Dummy PLC (deleted with the PLC).
    dummy_values = db.relationship("DummyValue", cascade="all, delete-orphan", lazy="dynamic")
    comm_alarms = db.relationship("Alarm", back_populates="plc", cascade="all, delete-orphan", lazy="dynamic")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<PLC Line1 192.168.0.10>``."""
        return f"<PLC {self.name} {self.ip_address}>"

    @property
    def is_modbus(self) -> bool:
        """``True`` for Modbus TCP and Modbus RTU devices."""
        return (self.protocol or "s7").startswith("modbus")

    @property
    def is_bacnet(self) -> bool:
        """``True`` for BACnet/IP devices."""
        return self.protocol == "bacnet_ip"

    @property
    def is_dummy(self) -> bool:
        """``True`` for a Dummy PLC (no device; internal values)."""
        return self.protocol == "dummy"

    @property
    def protocol_label(self) -> str:
        """Short protocol name for lists, e.g. ``Modbus TCP (pyModbusTCP)``."""
        from app.plc.protocols import MODBUS_LIBRARIES, PROTOCOLS  # noqa: PLC0415

        label = PROTOCOLS.get(self.protocol or "s7", self.protocol)
        if self.protocol == "modbus_tcp":
            label += f" ({MODBUS_LIBRARIES.get(self.modbus_library, self.modbus_library)})"
        return label

    @property
    def endpoint(self) -> str:
        """Where the device is reached, e.g. ``10.0.0.5:502`` or ``COM3 9600 8N1``."""
        if self.protocol == "modbus_rtu":
            return f"{self.serial_port} {self.baudrate} {self.bytesize}{self.parity}{self.stopbits}"
        if self.protocol == "bacnet_ip":
            return f"{self.ip_address}:{self.tcp_port} dev {self.bacnet_device_id}"
        if self.protocol == "dummy":
            return "no device"
        return f"{self.ip_address}:{self.tcp_port}"

    @property
    def connection_signature(self) -> tuple:
        """Tuple of all parameters that define the connection.

        The connection manager compares this value with the one used to open
        an existing connection and reconnects when it changed (for example
        after an administrator edited the IP address or the Modbus settings).
        """
        base = (self.protocol or "s7", self.ip_address, int(self.tcp_port or 0))
        if self.is_dummy:
            return ("dummy",)
        if self.is_bacnet:
            return base + (self.bacnet_device_id, self.bacnet_priority, self.timeout)
        if not self.is_modbus:
            return base + (int(self.rack or 0), int(self.slot or 0), self.timeout)
        return base + (self.modbus_library, self.unit_id, self.byte_order, self.word_order, self.timeout,
                       self.serial_port, self.baudrate, self.parity, self.stopbits, self.bytesize)

    @property
    def comm_alarm(self):
        """The communication-loss alarm of this PLC, or ``None``."""
        return self.comm_alarms.first()

    def to_dict(self) -> dict:
        """Serialise the PLC for JSON responses.

        Returns:
            A dictionary with the public PLC fields.
        """
        return {
            "id": self.id,
            "name": self.name,
            "ip_address": self.ip_address,
            "rack": self.rack,
            "slot": self.slot,
            "tcp_port": self.tcp_port,
            "enabled": self.enabled,
            "description": self.description,
            "protocol": self.protocol,
            "endpoint": self.endpoint,
            "timeout": self.timeout,
            "offline_delay": self.offline_delay,
        }
