"""Modbus TCP server: publishes tag values to other SCADA systems (optionally writable per tag).

Other SCADA / HMI systems (Siemens WinCC, ControlMaestro, Ignition, Kepware,
another PLC…) connect as Modbus TCP **clients** and read the values:

* registers – function 3 (*read holding registers*, ``4xxxx``) **or**
  function 4 (*read input registers*, ``3xxxx``): both return the same data;
* bits – function 1 (*read coils*, ``0xxxx``) **or** function 2 (*read
  discrete inputs*, ``1xxxx``): both return the same data.

Writes (functions 5 and 15 for bits, 6 and 16 for registers) are accepted only
for tags whose *Allow write* box is ticked **and** that are writable in
pyPLCHMI.  A request is checked as a whole before anything is written:

* an address that is not such a tag → exception 02 *Illegal data address*
  (also when a 32/64-bit value or a STRING is only partly written – the
  request must cover all of its registers);
* a value that cannot be converted (NaN, text into a number…) or that the tag
  refuses (out of range) → exception 03 *Illegal data value*;
* the PLC does not accept the write → exception 04 *Server device failure*.

Every write is written to the audit log (user ``Modbus TCP <client IP>``)
and goes through the same code as an operator's write
(:func:`~app.plc.service.write_datapoint`: reverse scaling, range checks).
Function 23 (read/write multiple registers) is refused with exception 01.
Unused addresses read as 0, so a client may read a whole block with gaps.

Parts:

* :func:`build_image` – turns the current tag values into a :class:`Image`
  (register and bit maps), keeping the last good value of tags that are
  temporarily bad (``bad_value = "hold"``);
* :class:`ExportHandler` – the pyModbusTCP data handler answering from the
  current image (unit id filter, statistics);
* :class:`ExportServer` – pyModbusTCP server that closes connections from
  clients outside the allow-list;
* :class:`ModbusPublisher` – background thread: every *interval* it reads the
  published tags, builds a new image and starts / restarts / stops the
  server when the settings change.  :meth:`ModbusPublisher.cycle` performs
  one step (used by the tests).
"""

from __future__ import annotations

import logging
import threading
from struct import error as struct_error
import time
from dataclasses import dataclass, field

from pyModbusTCP.server import (EXP_DATA_ADDRESS, EXP_DATA_VALUE, EXP_ILLEGAL_FUNCTION, EXP_NONE, DataHandler,
                                DeviceIdentification, ModbusServer)

from app.i18n.catalog import gettext
from app.modbus_server.config import ServerConfig
from app.modbus_server.encoding import decode, encode, is_bit, zero

log = logging.getLogger(__name__)

#: Modbus exception 0x0B – *gateway target device failed to respond* (wrong unit id).
EXP_GATEWAY_TARGET = 0x0B
#: Modbus exception 0x04 – *server device failure* (the PLC refused the write).
EXP_DEVICE_FAILURE = 0x04
#: Seconds between two attempts to open the port after a failure.
RETRY_DELAY = 10.0
#: Highest Modbus address.
MAX_ADDRESS = 65535


@dataclass(frozen=True)
class WriteTarget:
    """A published tag that SCADA clients may write (snapshot used by the server threads).

    Attributes:
        export_id: :class:`~app.models.modbus_export.ModbusExport` id.
        datapoint_id: Tag id.
        label: Tag name (for messages).
        area: ``reg`` or ``bit``.
        address: First register / the bit.
        count: Registers used (1 for a bit).
        mb_type: Modbus type.
        scale: Multiplier of the published value.
        string_length: Characters for ``STRING``.
    """

    export_id: int
    datapoint_id: int
    label: str
    area: str
    address: int
    count: int
    mb_type: str
    scale: float
    string_length: int


def write_targets(exports) -> dict[str, dict[int, WriteTarget]]:
    """Address → :class:`WriteTarget` for every published tag that may be written now.

    Every address of a multi-register value points to the same target.
    """
    out: dict[str, dict[int, WriteTarget]] = {"reg": {}, "bit": {}}
    for exp in exports:
        if not exp.write_ok:
            continue
        target = WriteTarget(exp.id, exp.datapoint_id, exp.datapoint.label, exp.area, exp.address, exp.count,
                             exp.mb_type, float(exp.scale or 1.0), int(exp.string_length or 0))
        for address in range(exp.address, exp.address + exp.count):
            out[exp.area][address] = target
    return out


def plan_writes(targets: dict[int, WriteTarget], address: int, data: list, area: str, order: str):
    """Check a write request and decode the values (nothing is written here).

    Args:
        targets: Address → target of the area (see :func:`write_targets`).
        address: First address of the request.
        data: Written bits (``bool``) or registers.
        area: ``bit`` or ``reg``.
        order: Value order.

    Returns:
        ``(exception code, [(target, value), …])`` – ``EXP_NONE`` and the
        writes, or an exception code and ``[]``.
    """
    writes, position = [], address
    end = address + len(data)
    while position < end:
        target = targets.get(position)
        if target is None or target.address != position or target.address + target.count > end:
            return EXP_DATA_ADDRESS, []           # not writable, or only part of a multi-register value
        chunk = data[position - address: position - address + target.count]
        try:
            value = decode(chunk[0] if area == "bit" else chunk, target.mb_type, order, target.scale,
                           target.string_length)
        except (ValueError, struct_error):
            return EXP_DATA_VALUE, []
        writes.append((target, value))
        position += target.count
    return EXP_NONE, writes


@dataclass
class Image:
    """The values currently published.

    Attributes:
        regs: Register address → value (0..65535).
        bits: Bit address → value.
        updated: ``time.time()`` of the update.
        good: Number of tags with a valid value.
        bad: Number of tags without a valid value.
    """

    regs: dict = field(default_factory=dict)
    bits: dict = field(default_factory=dict)
    updated: float = 0.0
    good: int = 0
    bad: int = 0


def build_image(cfg: ServerConfig, exports, values: dict, held: dict, heartbeat: int = 0) -> Image:
    """Encode the current tag values.

    Args:
        cfg: Server settings (value order, bad-value policy, heartbeat, quality bits).
        exports: :class:`~app.models.modbus_export.ModbusExport` rows (any order).
        values: ``datapoint id -> {"value", "quality", …}`` from
            :func:`~app.plc.service.read_datapoints`.
        held: Last good encoded value per export id (updated in place).
        heartbeat: Counter written to the heartbeat register.

    Returns:
        The new :class:`Image`.
    """
    image = Image(updated=time.time())
    for index, exp in enumerate(sorted(exports, key=lambda e: (e.area, e.address))):
        info = values.get(exp.datapoint_id) or {}
        encoded, ok = None, info.get("quality") == "good" and info.get("value") is not None
        if ok:
            try:
                encoded = encode(info["value"], exp.mb_type, cfg.order, exp.scale, exp.string_length)
                held[exp.id] = encoded
            except (ValueError, TypeError, OverflowError):
                ok = False
        if not ok:
            encoded = held.get(exp.id) if cfg.bad_value == "hold" else None
            if encoded is None:
                encoded = zero(exp.mb_type, exp.string_length)
        image.good += ok
        image.bad += not ok
        if is_bit(exp.mb_type):
            image.bits[exp.address] = bool(encoded)
        else:
            for offset, word in enumerate(encoded):
                image.regs[exp.address + offset] = word
        if cfg.quality_base is not None and cfg.quality_base + index <= MAX_ADDRESS:
            image.bits[cfg.quality_base + index] = ok
    if cfg.heartbeat is not None:
        image.regs[cfg.heartbeat] = heartbeat & 0xFFFF
    return image


class ExportHandler(DataHandler):
    """Answers read requests from the current :class:`Image`; refuses writes."""

    def __init__(self, publisher: "ModbusPublisher"):
        """Create the handler.

        Args:
            publisher: Owner (holds the image, the settings and the statistics).
        """
        super().__init__()
        self.publisher = publisher

    def _unit_ok(self, srv_info) -> bool:
        """Check the unit id of the request (0 in the settings = every unit id)."""
        unit = self.publisher.config.unit_id
        return not unit or srv_info.recv_frame.mbap.unit_id == unit

    def _read(self, space: str, address: int, count: int, srv_info):
        """Common part of the four read functions."""
        self.publisher.count_request(srv_info.client.address)
        if not self._unit_ok(srv_info):
            return DataHandler.Return(exp_code=EXP_GATEWAY_TARGET)
        if address + count - 1 > MAX_ADDRESS:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)
        image = self.publisher.image
        data = getattr(image, space)
        default = False if space == "bits" else 0
        return DataHandler.Return(exp_code=EXP_NONE, data=[data.get(a, default) for a in range(address, address + count)])

    def read_coils(self, address, count, srv_info):
        """Function 1: bits."""
        return self._read("bits", address, count, srv_info)

    def read_d_inputs(self, address, count, srv_info):
        """Function 2: bits (same data as coils)."""
        return self._read("bits", address, count, srv_info)

    def read_h_regs(self, address, count, srv_info):
        """Function 3: registers."""
        return self._read("regs", address, count, srv_info)

    def read_i_regs(self, address, count, srv_info):
        """Function 4: registers (same data as holding registers)."""
        return self._read("regs", address, count, srv_info)

    def _write(self, area: str, address: int, data: list, srv_info):
        """Common part of the write functions: check everything, then write through pyPLCHMI."""
        client = srv_info.client.address
        if not self._unit_ok(srv_info):
            self.publisher.count_request(client, write="refused")
            return DataHandler.Return(exp_code=EXP_GATEWAY_TARGET)
        if srv_info.recv_frame.pdu.func_code == 0x17:     # read/write multiple registers: not supported
            self.publisher.count_request(client, write="refused")
            return DataHandler.Return(exp_code=EXP_ILLEGAL_FUNCTION)
        code, writes = plan_writes(self.publisher.targets[area], address, list(data), area,
                                   self.publisher.config.order)
        if code != EXP_NONE:
            self.publisher.count_request(client, write="refused")
            return DataHandler.Return(exp_code=code)
        code = self.publisher.apply_writes(writes, client)
        self.publisher.count_request(client, write="done" if code == EXP_NONE else "failed")
        return DataHandler.Return(exp_code=code)

    def write_coils(self, address, bits_l, srv_info):
        """Functions 5 and 15: write BIT tags whose *Allow write* is ticked."""
        return self._write("bit", address, bits_l, srv_info)

    def write_h_regs(self, address, words_l, srv_info):
        """Functions 6 and 16: write register tags whose *Allow write* is ticked."""
        return self._write("reg", address, words_l, srv_info)


class ExportServer(ModbusServer):
    """pyModbusTCP server that refuses clients outside the allow-list."""

    def __init__(self, publisher: "ModbusPublisher", host: str, port: int):
        """Create (not start) the server.

        Args:
            publisher: Owner (settings and statistics).
            host: Address to listen on.
            port: TCP port.
        """
        ident = DeviceIdentification(vendor_name=b"pyPLCHMI", product_code=b"pyPLCHMI-MBS",
                                     major_minor_revision=b"1.0", product_name=b"pyPLCHMI Modbus TCP server")
        super().__init__(host=host, port=port, no_block=True, ipv6=":" in host, data_hdl=ExportHandler(publisher),
                         device_id=ident)
        base = ModbusServer.ModbusService

        class Service(base):
            """Connection handler: checks the client address, counts connections."""

            def handle(self):
                """Serve one client connection (closed at once when the client is not allowed)."""
                address = self.client_address[0]
                if not publisher.config.client_allowed(address):
                    publisher.count_refused(address)
                    return
                publisher.connected(address, +1)
                try:
                    base.handle(self)
                finally:
                    publisher.connected(address, -1)

        self.ModbusService = Service


class ModbusPublisher:
    """Keeps the published values up to date and the server running (one per process)."""

    instance: "ModbusPublisher | None" = None
    _class_lock = threading.Lock()

    def __init__(self):
        """Create an idle publisher (no server, no thread)."""
        self.config = ServerConfig()
        self.image = Image()
        self.server: ExportServer | None = None
        self.endpoint: tuple | None = None
        self.error: str | None = None
        self.last_attempt = 0.0
        self.started_at: float | None = None
        self.heartbeat = 0
        self._held: dict = {}
        #: Last tag values read: datapoint id → ``{"display", "quality"}`` (for the admin page).
        self.values: dict = {}
        self._lock = threading.Lock()
        self.stats = {"requests": 0, "writes_done": 0, "writes_refused": 0, "writes_failed": 0, "refused": 0,
                      "clients": {}, "connections": 0}
        #: Writable tags by area and address (see :func:`write_targets`); replaced every cycle.
        self.targets: dict = {"reg": {}, "bit": {}}
        #: The Flask application (set by :meth:`cycle`; needed to write tags from the server threads).
        self.app = None
        #: Last writes: ``[{"time", "client", "tag", "value", "result"}]`` (newest first, for the admin page).
        self.recent_writes: list = []
        self._write_lock = threading.Lock()
        self.thread: threading.Thread | None = None

    # -- statistics (called from server threads) --------------------------------
    def count_request(self, address: str, write: str | None = None) -> None:
        """Count one request of a client.

        Args:
            address: Client IP.
            write: ``None`` for reads, else ``done``, ``refused`` or ``failed``.
        """
        with self._lock:
            self.stats["requests"] += 1
            if write:
                self.stats[f"writes_{write}"] += 1
            client = self.stats["clients"].setdefault(address, {"requests": 0, "connections": 0, "last": 0.0})
            client["requests"] += 1
            client["last"] = time.time()

    def count_refused(self, address: str) -> None:
        """Count a connection refused by the allow-list."""
        with self._lock:
            self.stats["refused"] += 1
        log.warning("Modbus server: connection from %s refused (not in the allowed clients)", address)

    def connected(self, address: str, delta: int) -> None:
        """Track open connections per client."""
        with self._lock:
            client = self.stats["clients"].setdefault(address, {"requests": 0, "connections": 0, "last": 0.0})
            client["connections"] = max(0, client["connections"] + delta)
            client["last"] = time.time()
            self.stats["connections"] = sum(c["connections"] for c in self.stats["clients"].values())

    # -- writes (called from server threads) -----------------------------------------
    def apply_writes(self, writes: list, client: str) -> int:
        """Write checked values to their tags (one request), audit them and update the image.

        Args:
            writes: ``[(WriteTarget, value), …]`` from :func:`plan_writes`.
            client: Client IP (for the audit log).

        Returns:
            ``EXP_NONE``, ``EXP_DATA_VALUE`` (value refused by the tag) or
            ``EXP_DEVICE_FAILURE`` (the PLC refused / cannot be reached).
        """
        from app.audit import recorder as audit  # noqa: PLC0415
        from app.extensions import db  # noqa: PLC0415
        from app.models import DataPoint, ModbusExport  # noqa: PLC0415
        from app.plc.driver import PLCError  # noqa: PLC0415
        from app.plc.service import read_datapoints, write_datapoint  # noqa: PLC0415

        if self.app is None:
            return EXP_DEVICE_FAILURE
        user = f"Modbus TCP {client}"
        with self._write_lock, self.app.app_context():
            try:
                for target, value in writes:
                    dp = db.session.get(DataPoint, target.datapoint_id)
                    exp = db.session.get(ModbusExport, target.export_id)
                    if dp is None or exp is None or not exp.write_ok:
                        return EXP_DATA_ADDRESS               # changed since the last cycle
                    before = None
                    try:
                        before = read_datapoints([dp])[0].get(dp.id, {}).get("value")
                    except Exception:  # noqa: BLE001 - only for the audit log
                        pass
                    try:
                        result = write_datapoint(dp, value)
                    except ValueError as exc:
                        self._note_write(client, dp.label, value, str(exc))
                        return EXP_DATA_VALUE
                    except PLCError as exc:
                        self._note_write(client, dp.label, value, str(exc))
                        return EXP_DEVICE_FAILURE
                    after = result.get("value", value)
                    audit.record("write", "Value", dp.id, f"{dp.label} ({dp.plc.name if dp.plc else ''} {dp.address})",
                                 [{"field": "value", "before": before, "after": after}], username=user)
                    self._note_write(client, dp.label, after, None)
                    try:   # show the new value at once (the next cycle confirms it)
                        new = encode(after, target.mb_type, self.config.order, target.scale, target.string_length)
                        if target.area == "bit":
                            self.image.bits[target.address] = bool(new)
                        else:
                            for offset, word in enumerate(new):
                                self.image.regs[target.address + offset] = word
                    except (ValueError, TypeError, OverflowError):
                        pass
                return EXP_NONE
            except Exception:  # noqa: BLE001
                log.exception("Modbus server: write failed")
                db.session.rollback()
                return EXP_DEVICE_FAILURE
            finally:
                db.session.remove()

    def _note_write(self, client: str, tag: str, value, error: str | None) -> None:
        """Remember a write for the admin page (the last 20)."""
        with self._lock:
            self.recent_writes.insert(0, {"time": time.time(), "client": client, "tag": tag,
                                          "value": value if isinstance(value, (bool, int, float, str)) else str(value),
                                          "error": error})
            del self.recent_writes[20:]
        if error:
            log.warning("Modbus server: write of %s from %s failed: %s", tag, client, error)

    # -- server lifecycle -------------------------------------------------------
    @property
    def running(self) -> bool:
        """``True`` while the server listens."""
        return self.server is not None and self.server.is_run

    def stop_server(self) -> None:
        """Stop listening (open connections end within a second)."""
        server, self.server, self.endpoint, self.started_at = self.server, None, None, None
        if server is not None:
            try:
                server.stop()
            except Exception:  # noqa: BLE001
                log.exception("Modbus server: stop failed")

    def _ensure_server(self) -> None:
        """Start the server, or restart it when the address or port changed."""
        endpoint = (self.config.host, int(self.config.port))
        if self.running and self.endpoint == endpoint:
            return
        if self.server is not None:
            self.stop_server()
        if self.error and time.monotonic() - self.last_attempt < RETRY_DELAY:
            return
        self.last_attempt = time.monotonic()
        server = ExportServer(self, *endpoint)
        try:
            server.start()
        except Exception as exc:  # noqa: BLE001 - pyModbusTCP raises NetworkError(OSError)
            self.error = port_error(exc, endpoint[1])
            log.warning("Modbus server: cannot listen on %s:%s: %s", endpoint[0], endpoint[1], exc)
            return
        self.server, self.endpoint, self.error = server, endpoint, None
        self.started_at = time.time()
        log.info("Modbus TCP server listening on %s:%s", *endpoint)

    # -- one cycle -------------------------------------------------------------
    def cycle(self) -> None:
        """Read the settings and the published tags, update the image, start/stop the server.

        Must run inside an application context.
        """
        from flask import current_app  # noqa: PLC0415

        from app.models import ModbusExport  # noqa: PLC0415
        from app.plc.service import read_datapoints  # noqa: PLC0415

        self.app = current_app._get_current_object()
        cfg = ServerConfig.load()
        if cfg.endpoint_key() != self.config.endpoint_key():
            self.error = None            # new address/port: try at once
        self.config = cfg
        if not cfg.enabled:
            self.stop_server()
            self.error = None
            return
        exports = ModbusExport.query.all()
        points = [e.datapoint for e in exports if not e.datapoint.is_local]
        values, _plcs = read_datapoints(points) if points else ({}, {})
        known = {e.id for e in exports}
        self._held = {k: v for k, v in self._held.items() if k in known}
        self.heartbeat = (self.heartbeat + 1) & 0xFFFF
        self.image = build_image(cfg, exports, values, self._held, self.heartbeat)
        self.targets = write_targets(exports)
        self.values = {k: {"display": v.get("display"), "quality": v.get("quality")} for k, v in values.items()}
        self._ensure_server()

    def status(self) -> dict:
        """State for the admin page."""
        with self._lock:
            clients = [{"address": a, **c} for a, c in sorted(self.stats["clients"].items())]
            writes = list(self.recent_writes)
            stats = {k: v for k, v in self.stats.items() if k != "clients"}
        state = "disabled" if not self.config.enabled else ("running" if self.running else
                                                            ("error" if self.error else "starting"))
        return {"state": state, "error": self.error, "host": self.config.host, "port": self.config.port,
                "started_at": self.started_at, "updated": self.image.updated or None, "good": self.image.good,
                "bad": self.image.bad, "heartbeat": self.heartbeat if self.config.heartbeat is not None else None,
                "clients": clients, "recent_writes": writes, **stats}

    # -- background thread -----------------------------------------------------------
    @classmethod
    def get(cls) -> "ModbusPublisher":
        """The process-wide publisher."""
        with cls._class_lock:
            if cls.instance is None:
                cls.instance = cls()
            return cls.instance

    @classmethod
    def start(cls, app) -> bool:
        """Start the background thread (or restart it if it died).

        Returns:
            ``True`` if this call started a thread.
        """
        pub = cls.get()
        if pub.thread is not None and pub.thread.is_alive():
            return False
        with cls._class_lock:
            if pub.thread is not None and pub.thread.is_alive():
                return False
            pub.thread = threading.Thread(target=pub._run, args=(app,), name="modbus-server", daemon=True)
            pub.thread.start()
        return True

    def _run(self, app) -> None:
        """Thread body: one :meth:`cycle` per interval, never raising."""
        from app.audit.recorder import paused as audit_paused  # noqa: PLC0415
        from app.extensions import db  # noqa: PLC0415

        while True:
            started = time.monotonic()
            try:
                with app.app_context():
                    try:
                        with audit_paused():
                            self.cycle()
                    except Exception:  # noqa: BLE001
                        log.exception("Modbus server cycle failed")
                        db.session.rollback()
                    finally:
                        db.session.remove()
            except Exception:  # noqa: BLE001
                log.exception("Modbus server thread error")
            interval = float(self.config.interval or 1.0) if self.config.enabled else 2.0
            time.sleep(max(0.1, interval - (time.monotonic() - started)))


def port_error(exc: Exception, port: int) -> str:
    """A clear message for a port that cannot be opened."""
    text = str(exc)
    low = text.lower()
    if "permission" in low or "access" in low or "13" in low.split():
        return gettext("Permission denied for port {0}. On Linux, ports below 1024 need administrator rights – "
                       "use e.g. 5020, or allow it with setcap. ({1})", port, text)
    if "in use" in low or "98" in low.split() or "10048" in low:
        return gettext("Port {0} is already used by another program (another Modbus server or a second "
                       "pyPLCHMI process). ({1})", port, text)
    return gettext("Cannot listen on port {0}: {1}", port, text)
