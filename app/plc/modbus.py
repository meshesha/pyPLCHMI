"""Modbus TCP / RTU support (PyModbus and pyModbusTCP).

Overview
--------
* :class:`ModbusAddress` / :func:`parse_modbus_address` – tag addresses such as
  ``HR100:REAL``, ``40001``, ``IR5``, ``C12``, ``DI3`` or ``HR10.3`` (bit 3).
* :func:`decode_registers` / :func:`encode_registers` – 16-bit register
  conversion with configurable **byte order** and **word order** (ABCD, CDAB,
  BADC, DCBA – devices differ for 32/64-bit values).
* :func:`plan_modbus_reads` – merges neighbouring tags into as few requests as
  possible (max 125 registers / 2000 bits per request, as the protocol allows).
* Client adapters with one common interface:

  - :class:`PyModbusAdapter`      – ``pymodbus`` (Modbus TCP and RTU/serial)
  - :class:`PyModbusTCPAdapter`   – ``pyModbusTCP`` (Modbus TCP only)
  - :class:`SimulatedModbusAdapter` – in-memory device for ``PLC_SIMULATION``

* :class:`ModbusConnection` – used by
  :class:`~app.plc.driver.PLCConnectionManager` exactly like an S7 connection,
  through ``read_tags`` / ``write_tag``.

Address numbers in ``HR``/``IR``/``CO``/``DI`` notation are the **protocol
(0-based) addresses** sent on the wire.  Classic 5/6-digit Modicon numbers are
1-based and converted: ``40001`` = ``HR0``, ``30010`` = ``IR9``,
``10001`` = ``DI0``, ``00001`` = ``CO0``.
"""

from __future__ import annotations

import inspect
import logging
import math
import re
import struct
import threading
import time
from dataclasses import dataclass, field

from app.plc.datatypes import DATA_TYPES
from app.plc.errors import ConnectionLostError, DeviceError
from app.i18n.catalog import gettext

log = logging.getLogger(__name__)

#: Modbus data areas: code -> (description, bit area?, writable?)
MODBUS_AREAS = {
    "HR": ("Holding registers (4x, read/write)", False, True),
    "IR": ("Input registers (3x, read only)", False, False),
    "CO": ("Coils (0x, read/write bits)", True, True),
    "DI": ("Discrete inputs (1x, read only bits)", True, False),
}

#: Data types supported on Modbus and the number of 16-bit registers they use.
MODBUS_TYPE_REGISTERS = {
    "BOOL": 1, "INT": 1, "UINT": 1, "WORD": 1,
    "DINT": 2, "UDINT": 2, "DWORD": 2, "REAL": 2,
    "LREAL": 4, "STRING": 0,  # STRING: ceil(n / 2)
}

_STRUCT = {"INT": "h", "UINT": "H", "WORD": "H", "DINT": "i", "UDINT": "I", "DWORD": "I", "REAL": "f", "LREAL": "d"}

#: Byte/word order presets shown in the PLC form: (byte_order, word_order).
ORDER_PRESETS = {
    "ABCD": ("big", "big"),
    "CDAB": ("big", "little"),
    "BADC": ("little", "big"),
    "DCBA": ("little", "little"),
}

MAX_REGISTERS_PER_READ = 125
MAX_BITS_PER_READ = 2000

_AREA_ALIASES = {"HR": "HR", "IR": "IR", "CO": "CO", "C": "CO", "COIL": "CO", "DI": "DI"}
_TYPE_SUFFIX = r"(?::(?P<type>[A-Z]+)(?:\[(?P<len>\d+)\])?)?"
_NAMED_RE = re.compile(r"^(?P<area>HR|IR|COIL|CO|C|DI)(?P<num>\d{1,5})(?:\.(?P<bit>\d{1,2}))?" + _TYPE_SUFFIX + r"$")
_MODICON_RE = re.compile(r"^(?P<prefix>[0134])(?P<num>\d{4,5})(?:\.(?P<bit>\d{1,2}))?" + _TYPE_SUFFIX + r"$")
_MODICON_AREA = {"0": "CO", "1": "DI", "3": "IR", "4": "HR"}


class ModbusAddressError(ValueError):
    """Raised for invalid or inconsistent Modbus addresses."""


class ModbusConnectionLost(ConnectionLostError):
    """The device stopped answering / the link broke (connection-level error)."""


class ModbusDeviceError(DeviceError):
    """The device answered with a Modbus exception (e.g. illegal data address)."""


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
@dataclass
class ModbusAddress:
    """A resolved Modbus tag location.

    Attributes:
        area: ``HR``, ``IR``, ``CO`` or ``DI``.
        start: 0-based protocol address of the first register / bit.
        bit: Bit 0..15 inside a register (only for ``BOOL`` in HR/IR).
        data_type: Type name (see :data:`MODBUS_TYPE_REGISTERS`).
        string_length: Characters for ``STRING`` (2 per register).
        db_number: Always 0 (kept for interface compatibility with S7).
    """

    area: str
    start: int = 0
    bit: int = 0
    data_type: str = "UINT"
    string_length: int = 0
    db_number: int = 0

    #: Modbus addresses never refer to S7 timers/counters.
    is_timer_or_counter = False

    @property
    def is_bit_area(self) -> bool:
        """``True`` for coils and discrete inputs (1 bit per address)."""
        return MODBUS_AREAS[self.area][1]

    @property
    def writable_area(self) -> bool:
        """``True`` for holding registers and coils."""
        return MODBUS_AREAS[self.area][2]

    @property
    def size(self) -> int:
        """Number of registers (or bits for coil/discrete-input areas) occupied."""
        if self.is_bit_area:
            return 1
        if self.data_type == "STRING":
            return max(1, math.ceil(self.string_length / 2))
        return MODBUS_TYPE_REGISTERS[self.data_type]

    def validate(self) -> "ModbusAddress":
        """Check all fields for consistency.

        Returns:
            ``self``.

        Raises:
            ModbusAddressError: If anything is out of range or inconsistent.
        """
        if self.area not in MODBUS_AREAS:
            raise ModbusAddressError(gettext("Unknown Modbus area '{0}'", self.area))
        if self.data_type not in MODBUS_TYPE_REGISTERS:
            supported = ", ".join(t for t in MODBUS_TYPE_REGISTERS)
            raise ModbusAddressError(gettext('{0} is not supported on Modbus (use {1})', self.data_type, supported))
        if self.is_bit_area:
            if self.data_type != "BOOL":
                raise ModbusAddressError(gettext('Coils and discrete inputs are single bits – use BOOL'))
            if self.bit:
                raise ModbusAddressError(gettext('Coils and discrete inputs have no bit number'))
        elif self.data_type == "BOOL":
            if not 0 <= self.bit <= 15:
                raise ModbusAddressError(gettext('Register bit must be between 0 and 15'))
        else:
            self.bit = 0
        if self.data_type == "STRING":
            if not 1 <= self.string_length <= 250:
                raise ModbusAddressError(gettext('STRING length must be between 1 and 250 characters'))
        else:
            self.string_length = 0
        if not 0 <= self.start <= 65535 or self.start + self.size > 65536:
            raise ModbusAddressError(gettext('Address must be between 0 and 65535'))
        self.db_number = 0
        return self

    def format(self, with_type: bool = False) -> str:
        """Canonical text such as ``HR100``, ``HR10.3``, ``CO5``.

        Args:
            with_type: Append ``:TYPE`` (and ``[n]`` for strings).

        Returns:
            The address string (parseable by :func:`parse_modbus_address`).
        """
        text = f"{self.area}{self.start}"
        if self.data_type == "BOOL" and not self.is_bit_area:
            text += f".{self.bit}"
        if with_type:
            text += f":{self.data_type}"
            if self.data_type == "STRING":
                text += f"[{self.string_length}]"
        return text

    def __str__(self) -> str:
        """Return :meth:`format` with the type appended."""
        return self.format(with_type=True)


def parse_modbus_address(text: str, data_type: str | None = None, string_length: int | None = None) -> ModbusAddress:
    """Parse a Modbus address.

    Accepted forms (case-insensitive)::

        HR100  IR5  CO12 / C12  DI3        0-based protocol addresses
        HR10.3                              bit 3 of holding register 10 (BOOL)
        40001  30010  10001  00001          Modicon 5-digit (1-based)
        400001 300010                       Modicon 6-digit (1-based)
        HR100:REAL  40001:DINT  HR20:STRING[16]

    Args:
        text: Address text.
        data_type: Type used when the text has no ``:TYPE`` suffix.
        string_length: STRING length when not given as ``[n]``.

    Returns:
        A validated :class:`ModbusAddress`.

    Raises:
        ModbusAddressError: If the text is invalid.
    """
    if not text or not text.strip():
        raise ModbusAddressError(gettext('Address is empty'))
    raw = re.sub(r"\s+", "", text).upper()
    m = _NAMED_RE.match(raw)
    if m:
        area, start = _AREA_ALIASES[m["area"]], int(m["num"])
    else:
        m = _MODICON_RE.match(raw)
        if not m:
            raise ModbusAddressError(gettext("'{0}' is not a valid Modbus address (e.g. HR100, 40001, C5, DI2, IR10)", text))
        area = _MODICON_AREA[m["prefix"]]
        number = int(m["num"])
        if number < 1:
            raise ModbusAddressError(gettext('Modicon register numbers start at 1 (e.g. 40001)'))
        start = number - 1
    bit = int(m["bit"]) if m["bit"] is not None else None
    explicit = m["type"]
    dtype = explicit or (data_type.upper() if data_type else None)
    if MODBUS_AREAS[area][1] or bit is not None:
        # Bit addresses are always BOOL; only an explicit ":TYPE" suffix can contradict that.
        if explicit and explicit != "BOOL":
            raise ModbusAddressError(gettext('Bit addresses (coils, discrete inputs, HRx.y) must be BOOL'))
        dtype = "BOOL"
    if dtype is None:
        dtype = "UINT"
    if dtype == "BOOL" and not MODBUS_AREAS[area][1] and bit is None:
        raise ModbusAddressError(gettext('A BOOL in a register needs a bit number, e.g. {0}{1}.0', area, start))
    length = int(m["len"]) if m["len"] else int(string_length or 0)
    if dtype == "STRING" and not length:
        length = 20
    return ModbusAddress(area=area, start=start, bit=bit or 0, data_type=dtype, string_length=length).validate()


# ---------------------------------------------------------------------------
# Register encoding
# ---------------------------------------------------------------------------
def _reg_bytes(reg: int, byte_order: str) -> bytes:
    """Convert one 16-bit register to two bytes.

    Args:
        reg: Register value 0..65535.
        byte_order: ``"big"`` or ``"little"``.

    Returns:
        Two bytes.
    """
    return int(reg & 0xFFFF).to_bytes(2, "big" if byte_order == "big" else "little")


def decode_registers(regs, address: ModbusAddress, byte_order: str = "big", word_order: str = "big"):
    """Decode a value from registers (or bits) read from the device.

    Args:
        regs: Register values (ints) or bits (bools) exactly covering the tag.
        address: The tag address.
        byte_order: Byte order inside each register.
        word_order: Register order for 32/64-bit values.

    Returns:
        ``bool``, ``int``, ``float`` or ``str``.
    """
    dtype = address.data_type
    if address.is_bit_area:
        return bool(regs[0])
    if dtype == "BOOL":
        return bool((int(regs[0]) >> address.bit) & 1)
    if dtype == "STRING":
        data = b"".join(_reg_bytes(r, byte_order) for r in regs)[: address.string_length]
        return data.split(b"\x00", 1)[0].decode("latin-1").rstrip()
    words = list(regs) if word_order == "big" else list(regs)[::-1]
    data = b"".join(_reg_bytes(r, byte_order) for r in words)
    return struct.unpack(">" + _STRUCT[dtype], data)[0]


def encode_registers(value, address: ModbusAddress, byte_order: str = "big", word_order: str = "big") -> list[int]:
    """Encode a raw value into register values.

    ``BOOL`` values are handled by :meth:`ModbusConnection.write_tag` (coil
    write or read-modify-write of a register bit).

    Args:
        value: Raw value (already reverse-scaled).
        address: Target address.
        byte_order: Byte order inside each register.
        word_order: Register order for 32/64-bit values.

    Returns:
        List of 16-bit register values.

    Raises:
        ValueError: If the value does not fit the data type.
    """
    dtype = address.data_type
    if dtype == "STRING":
        text = str(value)
        if len(text) > address.string_length:
            raise ValueError(f"String longer than STRING[{address.string_length}]")
        data = text.encode("latin-1", "replace").ljust(address.size * 2, b"\x00")
        return [int.from_bytes(data[i:i + 2], "big" if byte_order == "big" else "little") for i in range(0, len(data), 2)]
    info = DATA_TYPES[dtype]
    if info.integer:
        value = int(value)
        if not info.min <= value <= info.max:
            raise ValueError(f"{value} out of range for {dtype} ({info.min}..{info.max})")
    else:
        value = float(value)
    data = struct.pack(">" + _STRUCT[dtype], value)
    regs = [int.from_bytes(data[i:i + 2], "big" if byte_order == "big" else "little") for i in range(0, len(data), 2)]
    return regs if word_order == "big" else regs[::-1]


# ---------------------------------------------------------------------------
# Read planning
# ---------------------------------------------------------------------------
@dataclass
class ModbusBlock:
    """One Modbus read request covering several tags."""

    area: str
    start: int
    count: int
    members: list = field(default_factory=list)


def plan_modbus_reads(addresses: dict, max_gap: int = 16) -> list[ModbusBlock]:
    """Group tags into as few Modbus read requests as possible.

    Args:
        addresses: Mapping ``key -> ModbusAddress``.
        max_gap: Largest number of unused registers/bits bridged between tags.

    Returns:
        List of :class:`ModbusBlock` (each within the protocol limits).
    """
    groups: dict = {}
    for key, addr in addresses.items():
        groups.setdefault(addr.area, []).append((key, addr))
    blocks = []
    for area, items in groups.items():
        limit = MAX_BITS_PER_READ if MODBUS_AREAS[area][1] else MAX_REGISTERS_PER_READ
        gap = max_gap * (16 if MODBUS_AREAS[area][1] else 1)
        items.sort(key=lambda kv: (kv[1].start, kv[1].size))
        current = None
        for key, addr in items:
            end = addr.start + addr.size
            if current is not None:
                cur_end = current.start + current.count
                new_end = max(cur_end, end)
                if addr.start - cur_end <= gap and new_end - current.start <= limit:
                    current.count = new_end - current.start
                    current.members.append((key, addr))
                    continue
            current = ModbusBlock(area, addr.start, addr.size, [(key, addr)])
            blocks.append(current)
    return blocks


# ---------------------------------------------------------------------------
# Client adapters
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModbusSettings:
    """Plain copy of a PLC's Modbus settings (safe to keep across requests)."""

    protocol: str = "modbus_tcp"
    library: str = "pymodbus"
    host: str = ""
    port: int = 502
    unit_id: int = 1
    byte_order: str = "big"
    word_order: str = "big"
    timeout: float = 3.0
    serial_port: str = ""
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8

    @classmethod
    def from_plc(cls, plc) -> "ModbusSettings":
        """Build settings from a :class:`~app.models.plc.PLC` (or similar object).

        Args:
            plc: Object with the PLC's Modbus attributes.

        Returns:
            A frozen settings object.
        """
        g = lambda name, default: getattr(plc, name, default) if getattr(plc, name, None) not in (None, "") else default  # noqa: E731
        return cls(
            protocol=g("protocol", "modbus_tcp"), library=g("modbus_library", "pymodbus"),
            host=g("ip_address", ""), port=int(g("tcp_port", 502)), unit_id=int(g("unit_id", 1)),
            byte_order=g("byte_order", "big"), word_order=g("word_order", "big"), timeout=float(g("timeout", 3.0)),
            serial_port=g("serial_port", ""), baudrate=int(g("baudrate", 9600)), parity=g("parity", "N"),
            stopbits=int(g("stopbits", 1)), bytesize=int(g("bytesize", 8)),
        )

    @property
    def description(self) -> str:
        """Human readable endpoint, e.g. ``192.168.0.5:502 unit 1``."""
        if self.protocol == "modbus_rtu":
            return f"{self.serial_port} {self.baudrate} {self.bytesize}{self.parity}{self.stopbits} unit {self.unit_id}"
        return f"{self.host}:{self.port} unit {self.unit_id}"


class PyModbusAdapter:
    """Modbus client based on **pymodbus** (TCP or RTU over a serial port)."""

    library_name = "pymodbus"

    def __init__(self, settings: ModbusSettings):
        """Store the settings; the client is created by :meth:`connect`.

        Args:
            settings: Connection settings.
        """
        self.settings = settings
        self._client = None
        self._unit_kw = "device_id"

    def connect(self) -> None:
        """Open the TCP connection or serial port.

        Raises:
            ModbusConnectionLost: If the device/port cannot be opened.
        """
        try:
            from pymodbus.client import ModbusSerialClient, ModbusTcpClient  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise ModbusConnectionLost("pymodbus is not installed (pip install pymodbus)") from exc
        s = self.settings
        if s.protocol == "modbus_rtu":
            client = ModbusSerialClient(port=s.serial_port, baudrate=s.baudrate, parity=s.parity,
                                        stopbits=s.stopbits, bytesize=s.bytesize, timeout=s.timeout, retries=1)
        else:
            client = ModbusTcpClient(s.host, port=s.port, timeout=s.timeout, retries=1)
        # pymodbus renamed the unit argument over time: unit (2.x) -> slave (3.0-3.8) -> device_id (3.9+).
        params = inspect.signature(client.read_holding_registers).parameters
        self._unit_kw = next((k for k in ("device_id", "slave", "unit") if k in params), "device_id")
        try:
            ok = client.connect()
        except Exception as exc:  # noqa: BLE001
            ok, err = False, exc
        else:
            err = None
        if not ok:
            client.close()
            raise ModbusConnectionLost(f"Cannot connect to {s.description}" + (f" ({err})" if err else ""))
        self._client = client

    @property
    def connected(self) -> bool:
        """``True`` while the socket / serial port is open."""
        return self._client is not None and bool(getattr(self._client, "connected", True))

    def close(self) -> None:
        """Close the client (errors ignored)."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover
                pass
            self._client = None

    def _call(self, func, *args, **kwargs):
        """Run a pymodbus request and translate errors.

        Args:
            func: Bound client method.
            *args: Positional arguments.
            **kwargs: Keyword arguments (the unit id is added).

        Returns:
            The pymodbus response.

        Raises:
            ModbusDeviceError: For Modbus exception responses.
            ModbusConnectionLost: For timeouts and broken connections.
        """
        kwargs[self._unit_kw] = self.settings.unit_id
        try:
            resp = func(*args, **kwargs)
        except Exception as exc:  # ConnectionException, ModbusIOException, socket errors
            raise ModbusConnectionLost(str(exc)) from exc
        if resp is None:
            raise ModbusConnectionLost("No response from device")
        if resp.isError():
            if hasattr(resp, "exception_code"):
                raise ModbusDeviceError(f"Modbus exception {resp.exception_code} ({_EXC_TEXT.get(resp.exception_code, 'error')})")
            raise ModbusConnectionLost(str(resp))
        return resp

    def read(self, area: str, start: int, count: int) -> list:
        """Read registers or bits.

        Args:
            area: ``HR``, ``IR``, ``CO`` or ``DI``.
            start: First address.
            count: Number of registers/bits.

        Returns:
            List of ints (registers) or bools (bits).
        """
        c = self._client
        func = {"HR": c.read_holding_registers, "IR": c.read_input_registers,
                "CO": c.read_coils, "DI": c.read_discrete_inputs}[area]
        resp = self._call(func, start, count=count)
        return list(resp.bits[:count]) if MODBUS_AREAS[area][1] else list(resp.registers[:count])

    def write_coil(self, address: int, value: bool) -> None:
        """Write one coil (FC05).

        Args:
            address: Coil address.
            value: New state.
        """
        self._call(self._client.write_coil, address, bool(value))

    def write_registers(self, address: int, values: list[int]) -> None:
        """Write registers (FC06 for one, FC16 for several).

        Args:
            address: First register.
            values: Register values.
        """
        if len(values) == 1:
            self._call(self._client.write_register, address, int(values[0]))
        else:
            self._call(self._client.write_registers, address, [int(v) for v in values])


class PyModbusTCPAdapter:
    """Modbus TCP client based on **pyModbusTCP** (lightweight, TCP only)."""

    library_name = "pyModbusTCP"

    def __init__(self, settings: ModbusSettings):
        """Store the settings; the client is created by :meth:`connect`.

        Args:
            settings: Connection settings.
        """
        self.settings = settings
        self._client = None

    def connect(self) -> None:
        """Open the TCP connection.

        Raises:
            ModbusConnectionLost: If the device cannot be reached.
        """
        try:
            from pyModbusTCP.client import ModbusClient  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise ModbusConnectionLost("pyModbusTCP is not installed (pip install pyModbusTCP)") from exc
        s = self.settings
        client = ModbusClient(host=s.host, port=s.port, unit_id=s.unit_id, timeout=s.timeout, auto_open=False)
        if not client.open():
            raise ModbusConnectionLost(f"Cannot connect to {s.description} ({client.last_error_as_txt})")
        self._client = client

    @property
    def connected(self) -> bool:
        """``True`` while the socket is open."""
        return self._client is not None and bool(self._client.is_open)

    def close(self) -> None:
        """Close the socket (errors ignored)."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover
                pass
            self._client = None

    def _check(self, result):
        """Translate a pyModbusTCP result (``None``/``False`` on error).

        Args:
            result: Return value of a client call.

        Returns:
            ``result`` when successful.

        Raises:
            ModbusDeviceError: For Modbus exception responses.
            ModbusConnectionLost: For all other errors.
        """
        if result is None or result is False:
            from pyModbusTCP.constants import MB_EXCEPT_ERR  # noqa: PLC0415

            if self._client.last_error == MB_EXCEPT_ERR:
                raise ModbusDeviceError(f"Modbus exception {self._client.last_except} ({self._client.last_except_as_txt})")
            raise ModbusConnectionLost(self._client.last_error_as_txt or "No response from device")
        return result

    def read(self, area: str, start: int, count: int) -> list:
        """Read registers or bits.

        Args:
            area: ``HR``, ``IR``, ``CO`` or ``DI``.
            start: First address.
            count: Number of registers/bits.

        Returns:
            List of ints or bools.
        """
        c = self._client
        func = {"HR": c.read_holding_registers, "IR": c.read_input_registers,
                "CO": c.read_coils, "DI": c.read_discrete_inputs}[area]
        return list(self._check(func(start, count)))

    def write_coil(self, address: int, value: bool) -> None:
        """Write one coil (FC05).

        Args:
            address: Coil address.
            value: New state.
        """
        self._check(self._client.write_single_coil(address, bool(value)))

    def write_registers(self, address: int, values: list[int]) -> None:
        """Write registers (FC06 for one, FC16 for several).

        Args:
            address: First register.
            values: Register values.
        """
        if len(values) == 1:
            self._check(self._client.write_single_register(address, int(values[0])))
        else:
            self._check(self._client.write_multiple_registers(address, [int(v) for v in values]))


class SimulatedModbusAdapter:
    """In-memory Modbus device used when ``PLC_SIMULATION`` is on.

    Memory is shared per endpoint so several requests see the same values.
    Hosts ending in ``.254`` simulate an unreachable device.
    """

    library_name = "simulator"
    _memories: dict = {}
    _lock = threading.Lock()

    def __init__(self, settings: ModbusSettings):
        """Attach to (or create) the simulated device memory.

        Args:
            settings: Connection settings (used as the memory key).
        """
        self.settings = settings
        key = (settings.protocol, settings.host or settings.serial_port, settings.port, settings.unit_id)
        with self._lock:
            self.memory = self._memories.setdefault(key, {"HR": {}, "IR": {}, "CO": {}, "DI": {}, "written": set()})
        self._open = False

    @classmethod
    def reset(cls) -> None:
        """Forget all simulated devices (used by tests)."""
        with cls._lock:
            cls._memories.clear()

    def connect(self) -> None:
        """Pretend to connect.

        Raises:
            ModbusConnectionLost: For hosts ending in ``.254``.
        """
        if str(self.settings.host).endswith(".254"):
            raise ModbusConnectionLost(f"Cannot connect to {self.settings.description} (simulated offline device)")
        self._open = True

    @property
    def connected(self) -> bool:
        """Simulated connection state."""
        return self._open

    def close(self) -> None:
        """Mark the simulated connection as closed."""
        self._open = False

    def read(self, area: str, start: int, count: int) -> list:
        """Read simulated registers or bits.

        Args:
            area: Area code.
            start: First address.
            count: Number of items.

        Returns:
            List of values (0 / False where never written).
        """
        mem = self.memory[area]
        if MODBUS_AREAS[area][1]:
            return [bool(mem.get(start + i, False)) for i in range(count)]
        return [int(mem.get(start + i, 0)) for i in range(count)]

    def write_coil(self, address: int, value: bool) -> None:
        """Store a coil value and stop animating it.

        Args:
            address: Coil address.
            value: New state.
        """
        self.memory["CO"][address] = bool(value)
        self.memory["written"].add(("CO", address))

    def write_registers(self, address: int, values: list[int]) -> None:
        """Store register values and stop animating them.

        Args:
            address: First register.
            values: Register values.
        """
        for i, v in enumerate(values):
            self.memory["HR"][address + i] = int(v) & 0xFFFF
            self.memory["written"].add(("HR", address + i))

    def poke(self, area: str, address: int, values) -> None:
        """Set values in any area without marking them as written (used by the animation).

        Args:
            area: Area code.
            address: First address.
            values: Values to store.
        """
        for i, v in enumerate(values):
            self.memory[area][address + i] = v


_EXC_TEXT = {1: "illegal function", 2: "illegal data address", 3: "illegal data value", 4: "device failure",
             5: "acknowledge", 6: "device busy", 10: "gateway path unavailable", 11: "gateway target failed to respond"}


# ---------------------------------------------------------------------------
# Connection used by the PLCConnectionManager
# ---------------------------------------------------------------------------
class ModbusConnection:
    """A Modbus device connection with tag-level read/write.

    It offers the same lifecycle interface as the S7 connections
    (``connect``, ``connected``, ``close``, ``cpu_info``, ``animate``) plus
    ``read_tags`` / ``write_tag``, which the connection manager uses for
    every non-S7 protocol.
    """

    def __init__(self, settings: ModbusSettings, adapter):
        """Create the connection.

        Args:
            settings: Connection settings.
            adapter: Client adapter instance (pymodbus, pyModbusTCP or simulator).
        """
        self.settings = settings
        self.adapter = adapter

    @classmethod
    def for_plc(cls, plc, simulation: bool = False) -> "ModbusConnection":
        """Build the right adapter for a PLC's protocol/library settings.

        Args:
            plc: The PLC model (or an object with the same attributes).
            simulation: Use :class:`SimulatedModbusAdapter`.

        Returns:
            An unconnected :class:`ModbusConnection`.
        """
        settings = ModbusSettings.from_plc(plc)
        if simulation:
            adapter_cls = SimulatedModbusAdapter
        elif settings.protocol == "modbus_tcp" and settings.library == "pymodbustcp":
            adapter_cls = PyModbusTCPAdapter
        else:
            adapter_cls = PyModbusAdapter
        return cls(settings, adapter_cls(settings))

    # -- lifecycle -----------------------------------------------------------
    def connect(self) -> None:
        """Open the connection.

        Raises:
            PLCError: If the device cannot be reached.
        """
        from app.plc.driver import PLCError  # noqa: PLC0415

        try:
            self.adapter.connect()
        except ModbusConnectionLost as exc:
            raise PLCError(str(exc)) from exc

    @property
    def connected(self) -> bool:
        """``True`` while the transport is open."""
        return self.adapter.connected

    def close(self) -> None:
        """Close the transport."""
        self.adapter.close()

    def cpu_info(self) -> dict:
        """Probe the device by reading holding register 0.

        A Modbus *exception* answer (e.g. illegal address) still proves that
        the device is alive; only a missing answer counts as failure.

        Returns:
            Dict with ``module``, ``name`` and ``state``.

        Raises:
            PLCError: If the device does not answer.
        """
        from app.plc.driver import PLCError  # noqa: PLC0415

        proto = "Modbus RTU" if self.settings.protocol == "modbus_rtu" else "Modbus TCP"
        info = {"module": f"{proto} via {self.adapter.library_name}", "name": self.settings.description}
        try:
            self.adapter.read("HR", 0, 1)
            info["state"] = "responding"
        except ModbusDeviceError as exc:
            info["state"] = f"responding ({exc})"
        except ModbusConnectionLost as exc:
            raise PLCError(gettext('No answer from {0}: {1}', self.settings.description, exc)) from exc
        return info

    # -- reading ---------------------------------------------------------------
    def _decode(self, values, addr: ModbusAddress):
        """Decode one tag from its slice of values.

        Args:
            values: Registers/bits covering exactly the tag.
            addr: Tag address.

        Returns:
            The decoded value.
        """
        return decode_registers(values, addr, self.settings.byte_order, self.settings.word_order)

    def read_tags(self, addresses: dict, max_gap: int = 16) -> dict:
        """Read many tags with as few requests as possible.

        Args:
            addresses: Mapping ``key -> ModbusAddress``.
            max_gap: See :func:`plan_modbus_reads`.

        Returns:
            Mapping ``key -> ReadResult``.

        Raises:
            ModbusConnectionLost: If the device stops answering (the manager
                then drops and later re-opens the connection).
        """
        from app.plc.driver import ReadResult  # noqa: PLC0415

        results = {}
        for block in plan_modbus_reads(addresses, max_gap):
            try:
                values = self.adapter.read(block.area, block.start, block.count)
                for key, addr in block.members:
                    off = addr.start - block.start
                    results[key] = ReadResult(value=self._decode(values[off:off + addr.size], addr))
            except ModbusDeviceError:
                # One bad address in the block – read members one by one.
                for key, addr in block.members:
                    try:
                        values = self.adapter.read(addr.area, addr.start, addr.size)
                        results[key] = ReadResult(value=self._decode(values, addr))
                    except ModbusDeviceError as exc:
                        results[key] = ReadResult(error=str(exc))
        return results

    # -- writing ---------------------------------------------------------------
    def write_tag(self, addr: ModbusAddress, raw_value) -> None:
        """Write one tag.

        * Coils: FC05.
        * BOOL in a holding register: read-modify-write of that register.
        * Everything else: encoded and written with FC06 / FC16.

        Args:
            addr: Tag address.
            raw_value: Raw value.

        Raises:
            ValueError: For read-only areas or values that do not fit.
            ModbusDeviceError: If the device rejects the write.
            ModbusConnectionLost: If the device stops answering.
        """
        if not addr.writable_area:
            raise ValueError(f"{addr.area} ({MODBUS_AREAS[addr.area][0]}) is read-only")
        if addr.area == "CO":
            self.adapter.write_coil(addr.start, bool(raw_value))
            return
        if addr.data_type == "BOOL":
            reg = int(self.adapter.read("HR", addr.start, 1)[0])
            reg = (reg | (1 << addr.bit)) if raw_value else (reg & ~(1 << addr.bit) & 0xFFFF)
            self.adapter.write_registers(addr.start, [reg])
            return
        regs = encode_registers(raw_value, addr, self.settings.byte_order, self.settings.word_order)
        self.adapter.write_registers(addr.start, regs)

    # -- simulation ---------------------------------------------------------
    def animate(self, addresses) -> None:
        """Generate changing demo values (simulator only; no-op otherwise).

        Args:
            addresses: Iterable of :class:`ModbusAddress` about to be read.
        """
        adapter = self.adapter
        if not isinstance(adapter, SimulatedModbusAdapter):
            return
        now = time.time()
        for addr in addresses:
            if (addr.area, addr.start) in adapter.memory["written"]:
                continue
            phase = addr.start * 0.7 + addr.bit
            try:
                if addr.data_type == "BOOL":
                    on = int(now / 3 + phase) % 2 == 1
                    if addr.is_bit_area:
                        adapter.poke(addr.area, addr.start, [on])
                    else:
                        reg = int(adapter.memory[addr.area].get(addr.start, 0))
                        reg = (reg | (1 << addr.bit)) if on else (reg & ~(1 << addr.bit))
                        adapter.poke(addr.area, addr.start, [reg])
                elif addr.data_type == "STRING":
                    adapter.poke(addr.area, addr.start, encode_registers(
                        time.strftime("SIM %H:%M:%S")[: addr.string_length], addr,
                        self.settings.byte_order, self.settings.word_order))
                else:
                    info = DATA_TYPES[addr.data_type]
                    wave = math.sin(now / 5 + phase)
                    value = int(500 + 400 * wave) if info.integer else 50 + 40 * wave
                    adapter.poke(addr.area, addr.start, encode_registers(
                        value, addr, self.settings.byte_order, self.settings.word_order))
            except Exception:  # pragma: no cover - never break a read for the demo
                log.debug("modbus animate failed for %s", addr, exc_info=True)
