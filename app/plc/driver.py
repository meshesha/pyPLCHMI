"""PLC communication layer (Siemens S7 via ``python-snap7``; Modbus via :mod:`app.plc.modbus`).

Overview
--------
* :class:`Snap7Connection` wraps one ``snap7.client.Client`` (one TCP
  connection to one PLC).
* :class:`SimulatedConnection` has the same interface but keeps PLC memory in
  RAM and animates read-only values – used for development, demos and tests
  (``PLC_SIMULATION=1``).
* :class:`PLCConnectionManager` keeps one persistent connection per PLC,
  serialises access to it with a lock (snap7 clients are not thread safe),
  reconnects automatically, and batches many small tag reads into a few
  ``read_area`` calls (see :func:`plan_reads`).

Communication loss handling
---------------------------
Short interruptions are common (a switch, a VPN, a CPU that resets a
connection).  The manager therefore distinguishes three states per PLC:

* ``online`` – the last request succeeded;
* ``unstable`` – requests fail, but for less than the PLC's
  ``offline_delay`` seconds: screens keep showing the last good values
  (marked *stale*) and nothing is reported as disconnected yet;
* ``offline`` – requests have failed for ``offline_delay`` seconds or longer:
  tags turn bad, the PLC badge turns red and the communication alarm fires.

A connection that breaks is re-opened immediately (once, within the same
request); after failed connection attempts the manager waits 0.5, 1, 2, 4 …
seconds (at most ``PLC_RECONNECT_DELAY``) before trying again.

The manager is stored in ``app.extensions["plc_manager"]``; use
:func:`get_manager` to obtain it inside a request.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field

from flask import current_app

from app.plc.addressing import S7Address
from app.plc.datatypes import DATA_TYPES, decode, encode
from app.plc.errors import ConnectionLostError, DeviceError
from app.i18n.catalog import gettext

log = logging.getLogger(__name__)


class PLCError(Exception):
    """Raised when a PLC cannot be reached or an operation fails."""


@dataclass
class ReadResult:
    """Outcome of reading one tag.

    Attributes:
        value: Decoded raw value, or ``None`` on error.
        error: Error message, or ``None`` on success.
    """

    value: object = None
    error: str | None = None
    #: ``True`` when ``value`` is the last good value, returned while the PLC
    #: is temporarily unreachable (within its ``offline_delay``).
    stale: bool = False

    @property
    def ok(self) -> bool:
        """``True`` when the read succeeded."""
        return self.error is None


@dataclass
class ReadBlock:
    """A contiguous memory range fetched with one ``read_area`` call.

    Attributes:
        area: Area code (``DB``, ``M``, ``I``, ``Q``, ``T``, ``C``).
        db_number: Data block number (0 for other areas).
        start: First byte (or timer/counter number).
        size: Number of bytes (or elements for ``T``/``C``).
        members: ``(key, S7Address)`` pairs decoded from this block.
    """

    area: str
    db_number: int
    start: int
    size: int
    members: list = field(default_factory=list)


def plan_reads(addresses: dict, max_span: int = 200, max_gap: int = 32) -> list[ReadBlock]:
    """Group tag addresses into as few contiguous reads as possible.

    Tags in the same area/DB whose byte ranges are close together are merged
    into one block, as long as the block does not exceed ``max_span`` bytes
    and the hole between two tags is at most ``max_gap`` bytes.  Timers and
    counters are always read individually.

    Args:
        addresses: Mapping ``key -> S7Address``.
        max_span: Largest block in bytes.
        max_gap: Largest unused gap that is still bridged.

    Returns:
        A list of :class:`ReadBlock` objects covering every address.
    """
    blocks: list[ReadBlock] = []
    groups: dict[tuple, list] = {}
    for key, addr in addresses.items():
        if addr.is_timer_or_counter:
            blocks.append(ReadBlock(addr.area, 0, addr.start, 1, [(key, addr)]))
        else:
            groups.setdefault((addr.area, addr.db_number), []).append((key, addr))

    for (area, db_number), items in groups.items():
        items.sort(key=lambda kv: (kv[1].start, kv[1].size))
        current: ReadBlock | None = None
        for key, addr in items:
            end = addr.start + addr.size
            if current is not None:
                cur_end = current.start + current.size
                new_end = max(cur_end, end)
                if addr.start - cur_end <= max_gap and new_end - current.start <= max_span:
                    current.size = new_end - current.start
                    current.members.append((key, addr))
                    continue
            current = ReadBlock(area, db_number, addr.start, addr.size, [(key, addr)])
            blocks.append(current)
    return blocks


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
class Snap7Connection:
    """A real connection to a Siemens PLC through ``snap7.client.Client``."""

    def __init__(self, ip_address: str, rack: int, slot: int, tcp_port: int = 102, timeout: float | None = None):
        """Store connection parameters; the socket is opened by :meth:`connect`.

        Args:
            ip_address: PLC IP address or host name.
            rack: Rack number of the CPU.
            slot: Slot number of the CPU.
            tcp_port: ISO-on-TCP port, normally 102.
            timeout: Request timeout in seconds (``None`` = library default).
        """
        self.ip_address = ip_address
        self.rack = rack
        self.slot = slot
        self.tcp_port = tcp_port
        self.timeout = timeout
        self._client = None

    def _apply_timeouts(self, client, connected: bool) -> None:
        """Apply :attr:`timeout` to a snap7 client, whatever its version.

        * python-snap7 1.x/2.x (C library): ``set_param`` with the
          ``PingTimeout`` (connect), ``SendTimeout`` and ``RecvTimeout``
          parameters in milliseconds, before connecting.
        * python-snap7 3.x (pure Python): those parameters are accepted but
          not used; the socket timeout (5 s by default, also used for the
          connection attempt) is set on ``client.connection`` after connecting.

        Errors are ignored: a timeout that cannot be applied leaves the
        library default in place.

        Args:
            client: ``snap7.client.Client``.
            connected: ``False`` before ``connect()``, ``True`` after it.
        """
        if not self.timeout:
            return
        if not connected:
            try:
                from snap7.type import Parameter  # noqa: PLC0415
            except ImportError:  # pragma: no cover - python-snap7 < 2
                from snap7.types import Parameter  # noqa: PLC0415
            ms = int(self.timeout * 1000)
            for param in (Parameter.PingTimeout, Parameter.SendTimeout, Parameter.RecvTimeout):
                try:
                    client.set_param(param, ms)
                except Exception:  # noqa: BLE001
                    log.debug("snap7 set_param(%s) not supported", param, exc_info=True)
            return
        iso = getattr(client, "connection", None)
        sock = getattr(iso, "socket", None)
        if iso is not None and sock is not None:
            try:
                iso.timeout = float(self.timeout)
                sock.settimeout(float(self.timeout))
            except Exception:  # noqa: BLE001
                log.debug("could not set the snap7 socket timeout", exc_info=True)

    def connect(self) -> None:
        """Open the TCP connection and S7 session.

        Raises:
            PLCError: If ``python-snap7`` is missing or the PLC is unreachable.
        """
        try:
            import snap7  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise PLCError(gettext('python-snap7 is not installed')) from exc
        self.close()
        client = snap7.client.Client()
        self._apply_timeouts(client, connected=False)
        try:
            client.connect(self.ip_address, self.rack, self.slot, self.tcp_port)
            self._apply_timeouts(client, connected=True)
        except Exception as exc:  # snap7 raises RuntimeError / S7 errors
            try:
                client.destroy()
            except Exception:  # pragma: no cover
                pass
            raise PLCError(gettext('Cannot connect to {0}:{1} ({2})', self.ip_address, self.tcp_port, exc)) from exc
        self._client = client

    @property
    def connected(self) -> bool:
        """``True`` if the client reports an open connection."""
        try:
            return self._client is not None and bool(self._client.get_connected())
        except Exception:
            return False

    def close(self) -> None:
        """Disconnect and release the snap7 client (errors are ignored)."""
        if self._client is not None:
            try:
                self._client.disconnect()
                self._client.destroy()
            except Exception:  # pragma: no cover
                pass
            self._client = None

    def read(self, address_area, db_number: int, start: int, size: int) -> bytearray:
        """Read raw bytes with ``Client.read_area``.

        Args:
            address_area: ``snap7`` ``Area`` enum member.
            db_number: DB number (0 for other areas).
            start: Start byte, or timer/counter number.
            size: Number of bytes, or elements for timers/counters.

        Returns:
            The bytes returned by the PLC.
        """
        return bytearray(self._client.read_area(address_area, db_number, start, size))

    def write(self, address_area, db_number: int, start: int, data: bytearray) -> None:
        """Write raw bytes with ``Client.write_area``.

        Args:
            address_area: ``snap7`` ``Area`` enum member.
            db_number: DB number (0 for other areas).
            start: Start byte.
            data: Bytes to write.
        """
        self._client.write_area(address_area, db_number, start, data)

    def cpu_info(self) -> dict:
        """Query CPU identification and run state.

        Returns:
            Dictionary with ``module``, ``serial``, ``name`` and ``state``
            (fields that the CPU refuses to report are omitted).
        """
        info: dict = {}
        try:
            cpu = self._client.get_cpu_info()
            for key, attr in (("module", "ModuleTypeName"), ("serial", "SerialNumber"), ("name", "ASName")):
                value = getattr(cpu, attr, b"")
                info[key] = value.decode("latin-1", "replace").strip("\x00 ") if isinstance(value, bytes) else str(value)
        except Exception as exc:  # many S7-1200/1500 refuse SZL reads
            info["info_error"] = str(exc)
        try:
            info["state"] = str(self._client.get_cpu_state()).replace("S7CpuStatus", "")
        except Exception as exc:
            info["state_error"] = str(exc)
        return info


class SimulatedConnection:
    """In-memory stand-in for a PLC, used when ``PLC_SIMULATION`` is on.

    Memory is a sparse ``bytearray`` per (area, DB).  Values written through
    the HMI are kept; every other configured tag is *animated* (sine waves,
    toggling bits) by :meth:`animate` so dashboards look alive.
    """

    #: Memory shared by all simulated connections, keyed by PLC signature.
    _memories: dict = {}
    _mem_lock = threading.Lock()
    #: Hosts that currently behave as unreachable (see :meth:`set_offline`).
    _offline_hosts: set = set()

    def __init__(self, ip_address: str, rack: int, slot: int, tcp_port: int = 102):
        """Create a simulated connection.

        Args:
            ip_address: Used only as part of the memory key.
            rack: Used only as part of the memory key.
            slot: Used only as part of the memory key.
            tcp_port: Used only as part of the memory key.
        """
        self.key = (ip_address, rack, slot, tcp_port)
        self._connected = False
        with self._mem_lock:
            self.memory = self._memories.setdefault(self.key, {"areas": {}, "written": set()})

    @classmethod
    def reset(cls) -> None:
        """Erase the memory of every simulated PLC and all injected faults (tests)."""
        with cls._mem_lock:
            cls._memories.clear()
            cls._offline_hosts.clear()

    @classmethod
    def set_offline(cls, ip_address: str, offline: bool = True) -> None:
        """Simulate a communication interruption of one host.

        While a host is offline, connecting fails and reads on an open
        connection raise and close it, like a real broken link.

        Args:
            ip_address: Host to break or repair.
            offline: ``True`` to break, ``False`` to repair.
        """
        with cls._mem_lock:
            (cls._offline_hosts.add if offline else cls._offline_hosts.discard)(ip_address)

    def _check_link(self) -> None:
        """Raise like a broken connection when the host is simulated offline.

        Raises:
            ConnectionError: If the host was put offline with :meth:`set_offline`.
        """
        if self.key[0] in self._offline_hosts:
            self._connected = False
            raise ConnectionError(f"Connection reset by {self.key[0]} (simulated)")

    def connect(self) -> None:
        """Pretend to connect; addresses ending in ``.254`` fail on purpose.

        Raises:
            PLCError: For IP addresses ending in ``.254`` (lets you test the
                offline display in simulation mode).
        """
        if str(self.key[0]).endswith(".254") or self.key[0] in self._offline_hosts:
            raise PLCError(gettext('Cannot connect to {0} (simulated offline PLC)', self.key[0]))
        self._connected = True

    @property
    def connected(self) -> bool:
        """Simulated connection state."""
        return self._connected

    def close(self) -> None:
        """Mark the simulated connection as closed."""
        self._connected = False

    def _buffer(self, area: str, db_number: int) -> bytearray:
        """Return (creating on demand) the 64 KiB buffer of an area/DB.

        Args:
            area: Area code.
            db_number: DB number.

        Returns:
            The mutable memory buffer.
        """
        return self.memory["areas"].setdefault((area, db_number), bytearray(65536 + 256))

    def read(self, area: str, db_number: int, start: int, size: int) -> bytearray:
        """Read bytes from simulated memory.

        Args:
            area: Area code (``DB``, ``M``...).
            db_number: DB number.
            start: Start byte (timer/counter number for ``T``/``C``).
            size: Byte count (element count for ``T``/``C``).

        Returns:
            A copy of the requested bytes.
        """
        self._check_link()
        buf = self._buffer(area, db_number)
        if area in ("T", "C"):
            start, size = start * 2, size * 2
        return bytearray(buf[start:start + size])

    def write(self, area: str, db_number: int, start: int, data: bytearray) -> None:
        """Write bytes into simulated memory and stop animating them.

        Args:
            area: Area code.
            db_number: DB number.
            start: Start byte.
            data: Bytes to store.
        """
        self._check_link()
        buf = self._buffer(area, db_number)
        if area in ("T", "C"):
            start *= 2
        buf[start:start + len(data)] = data
        for i in range(len(data)):
            self.memory["written"].add((area, db_number, start + i))

    def animate(self, addresses) -> None:
        """Generate changing demo values for tags never written by a user.

        Args:
            addresses: Iterable of :class:`S7Address` about to be read.
        """
        now = time.time()
        for addr in addresses:
            if (addr.area, addr.db_number, addr.start) in self.memory["written"]:
                continue
            phase = (addr.start + addr.db_number * 7 + addr.bit) * 0.7
            buf = self._buffer(addr.area, addr.db_number)
            pos = addr.start * 2 if addr.is_timer_or_counter else addr.start
            info = DATA_TYPES[addr.data_type]
            try:
                if addr.data_type == "BOOL":
                    on = int(now / 3 + phase) % 2 == 1
                    buf[pos] = (buf[pos] | (1 << addr.bit)) if on else (buf[pos] & ~(1 << addr.bit) & 0xFF)
                elif info.numeric:
                    wave = math.sin(now / 5 + phase)
                    if info.integer:
                        lo, hi = max(info.min, 0), min(info.max, 1000)
                        value = int((lo + hi) / 2 + (hi - lo) / 2 * 0.8 * wave)
                    else:
                        value = 50 + 40 * wave
                    buf[pos:pos + info.size] = encode(value, addr.data_type)
                elif addr.data_type == "STRING":
                    text = time.strftime("SIM %H:%M:%S")[: addr.string_length]
                    buf[pos:pos + addr.size] = encode(text, "STRING", addr.string_length)
            except Exception:  # pragma: no cover - never break a read for the demo
                log.debug("simulation animate failed for %s", addr, exc_info=True)

    def cpu_info(self) -> dict:
        """Return fake CPU identification.

        Returns:
            Dictionary resembling :meth:`Snap7Connection.cpu_info`.
        """
        return {"module": "Simulated CPU", "serial": "SIM-0000", "name": "SIMULATOR", "state": "Run"}


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
@dataclass
class _Entry:
    """Book-keeping for one PLC inside :class:`PLCConnectionManager`.

    Attributes:
        lock: Serialises all access to the connection.
        conn: The open connection object, or ``None``.
        signature: ``PLC.connection_signature`` the connection was opened with.
        last_error: Most recent communication error (``None`` while healthy).
        last_failure: ``time.monotonic()`` of the last failed connection attempt.
        failures: Consecutive failed connection attempts (drives the back-off).
        last_success: Wall-clock time (``time.time()``) of the last good request.
        down_since: ``time.monotonic()`` of the first failure of the current
            interruption, ``None`` while communication works.
        cache: Last good raw value per tag (``(key, repr(address)) -> value``),
            returned as *stale* values during the grace period.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    conn: object = None
    signature: tuple | None = None
    last_error: str | None = None
    last_failure: float = 0.0
    failures: int = 0
    last_success: float = 0.0
    down_since: float | None = None
    cache: dict = field(default_factory=dict)


class _CommFailure(Exception):
    """Internal: a request failed because of communication (not a bad address).

    Attributes:
        lost: ``True`` when an open connection broke (worth an immediate
            reconnect), ``False`` when connecting failed or is in back-off.
    """

    def __init__(self, message: str, lost: bool):
        """Create the failure.

        Args:
            message: Error text shown to the user.
            lost: See the class attributes.
        """
        super().__init__(message)
        self.lost = lost


#: Back-off after the first failed connection attempt (doubles each time).
FIRST_RETRY_DELAY = 0.5


def offline_delay_of(plc) -> float:
    """The PLC's grace period in seconds (``PLC.offline_delay``, default 0).

    Args:
        plc: PLC model (or any object).

    Returns:
        Seconds (never negative).
    """
    try:
        return max(0.0, float(getattr(plc, "offline_delay", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


class PLCConnectionManager:
    """Pool of persistent PLC connections shared by all requests."""

    def __init__(self, simulation: bool = False, reconnect_delay: float = 5.0,
                 max_span: int = 200, max_gap: int = 32, bacnet_local: dict | None = None):
        """Create a manager.

        Args:
            simulation: Use :class:`SimulatedConnection` instead of snap7.
            reconnect_delay: Longest wait (seconds) between connection attempts
                while a PLC stays unreachable (prevents every page refresh from
                blocking on a TCP timeout).  The first retries come sooner
                (0.5, 1, 2, 4 … seconds).
            max_span: See :func:`plan_reads`.
            max_gap: See :func:`plan_reads`.
            bacnet_local: Local BACnet stack settings ``{"ip", "port", "device_id"}``.
        """
        self.simulation = simulation
        self.bacnet_local = bacnet_local or {}
        self.reconnect_delay = reconnect_delay
        self.max_span = max_span
        self.max_gap = max_gap
        self._entries: dict[int, _Entry] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_app(cls, app) -> "PLCConnectionManager":
        """Create a manager configured from ``app.config``.

        Args:
            app: The Flask application.

        Returns:
            A new manager instance.
        """
        cfg = app.config
        return cls(
            simulation=cfg.get("PLC_SIMULATION", False),
            reconnect_delay=cfg.get("PLC_RECONNECT_DELAY", 5.0),
            max_span=cfg.get("PLC_MAX_READ_SPAN", 200),
            max_gap=cfg.get("PLC_MAX_READ_GAP", 32),
            bacnet_local={"ip": cfg.get("BACNET_LOCAL_IP"), "port": cfg.get("BACNET_LOCAL_PORT", 47808),
                          "device_id": cfg.get("BACNET_DEVICE_ID", 3056)},
        )

    # -- internals ----------------------------------------------------------
    def _entry(self, plc_id: int) -> _Entry:
        """Get or create the book-keeping entry for a PLC.

        Args:
            plc_id: Database id of the PLC.

        Returns:
            The :class:`_Entry` for that PLC.
        """
        with self._lock:
            return self._entries.setdefault(plc_id, _Entry())

    def _new_connection(self, plc):
        """Instantiate the connection class for a PLC (real or simulated).

        Siemens S7 PLCs get a :class:`Snap7Connection` (or
        :class:`SimulatedConnection`); Modbus devices get a
        :class:`~app.plc.modbus.ModbusConnection` with the pymodbus,
        pyModbusTCP or simulated adapter; BACnet devices get a
        :class:`~app.plc.bacnet.BACnetConnection` (BAC0 or simulated); web
        devices get a :class:`~app.plc.web.WebConnection`.

        Args:
            plc: A :class:`~app.models.plc.PLC` (or any object with
                ``ip_address``, ``rack``, ``slot`` and ``tcp_port``).

        Returns:
            An unconnected connection object.
        """
        protocol = getattr(plc, "protocol", None) or "s7"
        if protocol.startswith("modbus"):
            from app.plc.modbus import ModbusConnection  # noqa: PLC0415

            return ModbusConnection.for_plc(plc, simulation=self.simulation)
        if protocol == "bacnet_ip":
            from app.plc.bacnet import BACnetConnection  # noqa: PLC0415

            return BACnetConnection.for_plc(plc, simulation=self.simulation, local=self.bacnet_local)
        if protocol == "dummy":
            from app.plc.dummy import DummyConnection  # noqa: PLC0415

            return DummyConnection.for_plc(plc)
        if protocol in ("http", "websocket"):   # web devices are always real (also in simulation mode)
            from app.plc.web import WebConnection  # noqa: PLC0415

            return WebConnection.for_plc(plc)
        if self.simulation:
            return SimulatedConnection(plc.ip_address, int(plc.rack), int(plc.slot), int(plc.tcp_port))
        return Snap7Connection(plc.ip_address, int(plc.rack), int(plc.slot), int(plc.tcp_port),
                               timeout=getattr(plc, "timeout", None))

    def _area(self, addr: S7Address):
        """Translate an address area for the active connection type.

        Args:
            addr: The address.

        Returns:
            The area code string in simulation, otherwise the snap7 ``Area``.
        """
        return addr.area if self.simulation else addr.snap7_area()

    def _retry_wait(self, entry: _Entry) -> float:
        """Seconds to wait after ``entry.failures`` failed connection attempts.

        0.5 s after the first failure, then doubling, capped at
        ``reconnect_delay`` – so a short interruption is noticed to be over
        quickly while an offline PLC is not hammered.

        Args:
            entry: The PLC's book-keeping entry.

        Returns:
            Seconds.
        """
        if entry.failures <= 0:
            return 0.0
        return min(self.reconnect_delay, FIRST_RETRY_DELAY * 2 ** (entry.failures - 1))

    def _ensure_connected(self, entry: _Entry, plc) -> None:
        """Make sure ``entry.conn`` is an open connection for ``plc``.

        Must be called with ``entry.lock`` held.

        Args:
            entry: The PLC's book-keeping entry.
            plc: The PLC model.

        Raises:
            PLCError: If the PLC is disabled, in its reconnect back-off
                period, or the connection attempt fails.
        """
        if not plc.enabled:
            raise PLCError(gettext("PLC '{0}' is disabled", plc.name))
        signature = plc.connection_signature
        if entry.signature is not None and entry.signature != signature:
            # Settings changed (maybe another device): forget the old link and values.
            if entry.conn is not None:
                entry.conn.close()
            entry.conn, entry.signature = None, None
            entry.cache.clear()
            entry.failures = 0
        if entry.conn is not None and entry.conn.connected:
            return
        if entry.failures and time.monotonic() < entry.last_failure + self._retry_wait(entry):
            raise PLCError(entry.last_error or "PLC offline")
        conn = self._new_connection(plc)
        try:
            conn.connect()
        except PLCError as exc:
            entry.last_error, entry.last_failure = str(exc), time.monotonic()
            entry.failures += 1
            raise
        entry.conn, entry.signature = conn, signature
        entry.failures, entry.last_failure = 0, 0.0
        log.info("Connected to PLC %s (%s)", plc.name, plc.ip_address)

    def _drop(self, entry: _Entry, error: Exception) -> None:
        """Close a broken connection.

        No back-off is applied: the next request reconnects immediately,
        because most broken connections can be re-opened at once.

        Args:
            entry: The PLC's book-keeping entry (lock held).
            error: The exception that broke the connection.
        """
        if entry.conn is not None:
            entry.conn.close()
        entry.conn = None
        entry.last_error = str(error)

    def _mark_up(self, entry: _Entry, plc) -> None:
        """Record a successful request (ends an interruption).

        Args:
            entry: The PLC's book-keeping entry (lock held).
            plc: The PLC model.
        """
        if entry.down_since is not None:
            log.warning("PLC %s: communication restored after %.1f s", plc.name, time.monotonic() - entry.down_since)
        entry.down_since = None
        entry.last_error = None
        entry.last_success = time.time()

    def _mark_down(self, entry: _Entry, plc, message: str) -> None:
        """Record a communication failure (starts or continues an interruption).

        Args:
            entry: The PLC's book-keeping entry (lock held).
            plc: The PLC model.
            message: The error text.
        """
        if entry.down_since is None:
            entry.down_since = time.monotonic()
            log.warning("PLC %s: communication error: %s", plc.name, message)
        entry.last_error = message

    @staticmethod
    def _cache_key(key, addr) -> tuple:
        """Key of a tag in the last-good-value cache.

        Args:
            key: Caller's key (data point id).
            addr: The tag address.

        Returns:
            ``(key, repr(addr))`` – a changed address never returns an old value.
        """
        return key, repr(addr)

    def _read_locked(self, entry: _Entry, plc, addresses: dict) -> dict:
        """Connect if needed and read all tags (``entry.lock`` held).

        Args:
            entry: The PLC's book-keeping entry.
            plc: The PLC model.
            addresses: ``key -> address``.

        Returns:
            ``key -> ReadResult`` (individual tags may carry an error).

        Raises:
            _CommFailure: If the PLC cannot be reached or the connection broke.
        """
        try:
            self._ensure_connected(entry, plc)
        except PLCError as exc:
            raise _CommFailure(str(exc), lost=False) from exc

        if hasattr(entry.conn, "animate"):
            entry.conn.animate(addresses.values())

        if hasattr(entry.conn, "read_tags"):  # Modbus, BACnet and other tag-based protocols
            try:
                return entry.conn.read_tags(addresses, max_gap=max(1, self.max_gap // 2))
            except ConnectionLostError as exc:
                self._drop(entry, exc)
                raise _CommFailure(f"Connection lost: {exc}", lost=True) from exc

        results: dict = {}
        for block in plan_reads(addresses, self.max_span, self.max_gap):
            try:
                data = self._read_block(entry, block)
                for key, addr in block.members:
                    offset = 0 if addr.is_timer_or_counter else addr.start - block.start
                    results[key] = ReadResult(value=decode(data, offset, addr.data_type, addr.bit))
            except Exception as exc:  # noqa: BLE001
                if not entry.conn.connected:
                    self._drop(entry, exc)
                    raise _CommFailure(f"Connection lost: {exc}", lost=True) from exc
                # Address-level error: retry members one by one so a single
                # bad tag does not hide its neighbours.
                for key, addr in block.members:
                    results[key] = self._read_single(entry, addr)
        return results

    def _fallback(self, entry: _Entry, plc, addresses: dict, message: str) -> dict:
        """Results while the PLC cannot be read.

        Within the PLC's ``offline_delay`` every tag with a known last value
        returns it as a *stale* good value; otherwise (or later) every tag
        gets the error.

        Args:
            entry: The PLC's book-keeping entry (lock held).
            plc: The PLC model.
            addresses: ``key -> address``.
            message: Error text.

        Returns:
            ``key -> ReadResult``.
        """
        in_grace = entry.down_since is not None and time.monotonic() - entry.down_since < offline_delay_of(plc)
        results = {}
        for key, addr in addresses.items():
            ck = self._cache_key(key, addr)
            if in_grace and ck in entry.cache:
                results[key] = ReadResult(value=entry.cache[ck], stale=True)
            else:
                results[key] = ReadResult(error=message)
        return results

    def _read_block(self, entry: _Entry, block: ReadBlock) -> bytearray:
        """Execute one :class:`ReadBlock` on the open connection.

        Args:
            entry: The PLC's book-keeping entry (lock held).
            block: The block to read.

        Returns:
            Raw bytes for the block.
        """
        addr0 = block.members[0][1]
        return entry.conn.read(self._area(addr0), block.db_number, block.start, block.size)

    # -- public API ---------------------------------------------------------
    def read(self, plc, addresses: dict) -> dict:
        """Read many tags from one PLC.

        A connection that breaks during the request is re-opened and the read
        repeated once.  If the PLC still cannot be read, tags return their
        last good value (``stale=True``) until the PLC's ``offline_delay`` has
        passed, then the error.

        Args:
            plc: The PLC model.
            addresses: Mapping ``key -> S7Address``; keys are arbitrary
                (data point ids are used by the dashboards).

        Returns:
            Mapping ``key -> ReadResult``.  On connection failure every tag
            gets the same error; a bad individual address only fails itself.
        """
        if not addresses:
            return {}
        entry = self._entry(plc.id)
        with entry.lock:
            try:
                try:
                    results = self._read_locked(entry, plc, addresses)
                except _CommFailure as exc:
                    if not exc.lost:
                        raise
                    log.info("PLC %s: %s – reconnecting", plc.name, exc)
                    results = self._read_locked(entry, plc, addresses)
            except _CommFailure as exc:
                self._mark_down(entry, plc, str(exc))
                return self._fallback(entry, plc, addresses, str(exc))
            self._mark_up(entry, plc)
            for key, res in results.items():
                if res.ok and key in addresses:
                    entry.cache[self._cache_key(key, addresses[key])] = res.value
        return results

    def probe(self, plc) -> bool:
        """Check that a PLC can be reached (used for PLCs without tags).

        Opens the connection if needed and updates the communication state.

        Args:
            plc: The PLC model.

        Returns:
            ``True`` if the connection is open.
        """
        entry = self._entry(plc.id)
        with entry.lock:
            try:
                self._ensure_connected(entry, plc)
            except PLCError as exc:
                self._mark_down(entry, plc, str(exc))
                return False
            self._mark_up(entry, plc)
            return True

    def _read_single(self, entry: _Entry, addr: S7Address) -> ReadResult:
        """Read one tag on its own (fallback after a failed block read).

        Args:
            entry: The PLC's book-keeping entry (lock held).
            addr: Address to read.

        Returns:
            The :class:`ReadResult` for that tag.
        """
        try:
            size = 1 if addr.is_timer_or_counter else addr.size
            data = entry.conn.read(self._area(addr), addr.db_number, addr.start, size)
            return ReadResult(value=decode(data, 0, addr.data_type, addr.bit))
        except Exception as exc:  # noqa: BLE001
            return ReadResult(error=str(exc))

    def read_one(self, plc, address: S7Address) -> ReadResult:
        """Convenience wrapper to read a single tag.

        Args:
            plc: The PLC model.
            address: Address to read.

        Returns:
            The :class:`ReadResult`.
        """
        return self.read(plc, {"_": address})["_"]

    def write(self, plc, address: S7Address, raw_value) -> None:
        """Write one value to a PLC.

        ``BOOL`` values use read-modify-write of the containing byte (under
        the PLC lock) so neighbouring bits are preserved.  Note that the PLC
        program itself could change another bit of that byte between the read
        and the write; for critical bits use a dedicated byte.

        Args:
            plc: The PLC model.
            address: Target address.
            raw_value: Value in raw PLC units (see ``DataPoint.to_raw``).

        Raises:
            PLCError: If the connection or the write fails.
            ValueError: If the value cannot be encoded for the data type.
        """
        entry = self._entry(plc.id)
        if (getattr(plc, "protocol", None) or "s7") != "s7":  # Modbus, BACnet: tag-based drivers
            self._write_tag(entry, plc, address, raw_value)
            return
        if address.is_timer_or_counter:
            raise PLCError(gettext('Writing timers/counters is not supported'))
        if address.data_type != "BOOL":
            payload = encode(raw_value, address.data_type, address.string_length)
        with entry.lock:
            try:
                self._ensure_connected(entry, plc)
            except PLCError as exc:
                self._mark_down(entry, plc, str(exc))
                raise
            area = self._area(address)
            try:
                if address.data_type == "BOOL":
                    current = entry.conn.read(area, address.db_number, address.start, 1)
                    byte = current[0]
                    byte = (byte | (1 << address.bit)) if raw_value else (byte & ~(1 << address.bit) & 0xFF)
                    payload = bytearray([byte])
                entry.conn.write(area, address.db_number, address.start, payload)
            except Exception as exc:  # noqa: BLE001
                if not entry.conn.connected:
                    self._drop(entry, exc)
                    self._mark_down(entry, plc, f"Connection lost: {exc}")
                raise PLCError(gettext('Write failed: {0}', exc)) from exc

    def _write_tag(self, entry: _Entry, plc, address, raw_value) -> None:
        """Write through a connection offering ``write_tag`` (Modbus, BACnet).

        Args:
            entry: The PLC's book-keeping entry.
            plc: The PLC model.
            address: Target address.
            raw_value: Raw value.

        Raises:
            ValueError: For read-only areas or values that do not fit.
            PLCError: If the device rejects the write or stops answering.
        """
        with entry.lock:
            try:
                self._ensure_connected(entry, plc)
            except PLCError as exc:
                self._mark_down(entry, plc, str(exc))
                raise
            try:
                entry.conn.write_tag(address, raw_value)
            except DeviceError as exc:
                raise PLCError(gettext('Write rejected by device: {0}', exc)) from exc
            except ConnectionLostError as exc:
                self._drop(entry, exc)
                self._mark_down(entry, plc, f"Connection lost: {exc}")
                raise PLCError(gettext('Write failed: {0}', exc)) from exc

    def status(self, plc) -> dict:
        """Report the cached communication state of a PLC (no network I/O).

        ``state`` is one of:

        * ``online`` – the last request succeeded;
        * ``unstable`` – requests fail, but for less than ``offline_delay``
          seconds (still reported as ``connected``; values are stale);
        * ``offline`` – requests have failed for ``offline_delay`` seconds or more;
        * ``unknown`` – not contacted yet;
        * ``disabled`` – the PLC is disabled.

        Args:
            plc: The PLC model.

        Returns:
            Dictionary with ``state``, ``connected`` (``online`` or
            ``unstable``), ``error``, ``last_success`` (epoch seconds),
            ``down_for`` (seconds since the interruption began, or ``None``)
            and ``offline_delay``.
        """
        entry = self._entry(plc.id)
        delay = offline_delay_of(plc)
        down_for = None if entry.down_since is None else round(time.monotonic() - entry.down_since, 1)
        if not getattr(plc, "enabled", True):
            state = "disabled"
        elif down_for is not None:
            state = "unstable" if down_for < delay else "offline"
        elif entry.last_success or (entry.conn is not None and entry.conn.connected):
            state = "online"
        else:
            state = "unknown"
        return {
            "state": state,
            "connected": state in ("online", "unstable"),
            "error": entry.last_error,
            "last_success": entry.last_success or None,
            "down_for": down_for,
            "offline_delay": delay,
        }

    def test_connection(self, plc) -> dict:
        """Open a fresh, temporary connection and query CPU information.

        Unlike :meth:`read`, this ignores the reconnect back-off so that an
        administrator can test settings immediately.

        Args:
            plc: The PLC model (``enabled`` is ignored).

        Returns:
            ``{"ok": True, ...cpu info...}`` or ``{"ok": False, "error": msg}``.
        """
        conn = self._new_connection(plc)
        try:
            conn.connect()
            info = conn.cpu_info()
            info["ok"] = True
            return info
        except PLCError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()

    def bacnet_browse(self, plc, limit: int = 500) -> list[dict]:
        """List the objects of a BACnet device (objectList with names and values).

        Args:
            plc: A BACnet PLC.
            limit: Maximum number of objects.

        Returns:
            Rows from :meth:`~app.plc.bacnet.BACnetConnection.browse`.

        Raises:
            PLCError: If the device does not answer.
        """
        from app.plc.bacnet import BACnetConnection  # noqa: PLC0415

        conn = BACnetConnection.for_plc(plc, simulation=self.simulation, local=self.bacnet_local)
        try:
            return conn.browse(limit)
        except (ConnectionLostError, DeviceError) as exc:
            raise PLCError(str(exc)) from exc

    def bacnet_who_is(self, address: str | None = None, timeout: float = 3.0) -> list[dict]:
        """Discover BACnet devices (Who-Is broadcast or directed to ``address``).

        Args:
            address: Optional device address.
            timeout: Seconds to wait for I-Am answers.

        Returns:
            List of ``{"device_id", "address"}``.

        Raises:
            PLCError: If the local BACnet stack cannot start.
        """
        from app.plc.bacnet import BAC0Adapter, BACnetSettings, SimulatedBACnetAdapter  # noqa: PLC0415

        settings = BACnetSettings.from_plc(None, self.bacnet_local)
        adapter = SimulatedBACnetAdapter(settings) if self.simulation else BAC0Adapter(settings)
        try:
            return adapter.who_is(address, timeout)
        except ConnectionLostError as exc:
            raise PLCError(str(exc)) from exc

    def disconnect(self, plc_id: int) -> None:
        """Close and forget the connection of one PLC.

        Args:
            plc_id: Database id of the PLC.
        """
        with self._lock:
            entry = self._entries.pop(plc_id, None)
        if entry is not None:
            with entry.lock:
                if entry.conn is not None:
                    entry.conn.close()

    def disconnect_all(self) -> None:
        """Close every open connection (called on shutdown and in tests)."""
        with self._lock:
            ids = list(self._entries)
        for plc_id in ids:
            self.disconnect(plc_id)


def get_manager() -> PLCConnectionManager:
    """Return the connection manager of the current application.

    Returns:
        ``current_app.extensions["plc_manager"]``.
    """
    return current_app.extensions["plc_manager"]
