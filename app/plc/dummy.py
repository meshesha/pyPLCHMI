"""DUMMY PLC: a device-less PLC type for internal tags.

A *Dummy* PLC needs no IP address and its tags need no address – the HMI
gives each tag an internal slot number (shown as ``D1``, ``D2``…).  Values
start at 0 / OFF / empty and can be written from screens like any writable
tag.  There are two kinds of dummy tags (``DataPoint.dummy_scope``):

* **general** – one value for everybody: it is stored in the database
  (:class:`~app.models.dummy_value.DummyValue`), so every browser and every
  computer shows the same value, also after a server restart;
* **local** – every browser window has its own value (kept in the
  browser's ``sessionStorage`` by ``hmi-local.js``); the server never reads
  or writes it.

Typical uses: designing screens before the PLC exists, operator inputs
that no PLC needs, or placeholders.  A dummy tag can later be moved to a real
PLC by editing it (choose the PLC and enter the real address).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass

from app.plc.datatypes import DATA_TYPES
from app.i18n.catalog import gettext

#: Data types available for dummy tags.
DUMMY_TYPES = ("BOOL", "BYTE", "USINT", "SINT", "CHAR", "WORD", "UINT", "INT", "DWORD", "UDINT", "DINT",
               "REAL", "LREAL", "STRING")

log = logging.getLogger(__name__)

_ADDR_RE = re.compile(r"^D(\d{1,9})(?::([A-Z]+))?$", re.IGNORECASE)


class DummyAddressError(ValueError):
    """Raised for an unsupported dummy tag (e.g. a data type it cannot hold)."""


@dataclass
class DummyAddress:
    """The internal slot of a dummy tag.

    Attributes:
        start: Slot number, unique per dummy PLC (assigned automatically).
        data_type: Value type (see :data:`DUMMY_TYPES`).
        string_length: Maximum length for ``STRING``.
        area: Always ``"D"``.
        db_number: Always 0 (interface compatibility).
        bit: Always 0 (interface compatibility).
    """

    start: int = 0
    data_type: str = "INT"
    string_length: int = 0
    area: str = "D"
    db_number: int = 0
    bit: int = 0

    #: Dummy tags are never S7 timers/counters.
    is_timer_or_counter = False
    #: Every dummy tag may be written.
    writable_area = True

    @property
    def size(self) -> int:
        """Always 1 (one value)."""
        return 1

    def validate(self) -> "DummyAddress":
        """Check the data type.

        Returns:
            ``self``.

        Raises:
            DummyAddressError: For a type a dummy tag cannot hold.
        """
        if self.data_type not in DUMMY_TYPES:
            raise DummyAddressError(gettext('{0} is not available for dummy tags (use {1})', self.data_type, ', '.join(DUMMY_TYPES)))
        if self.data_type != "STRING":
            self.string_length = 0
        self.area, self.db_number, self.bit = "D", 0, 0
        return self

    def format(self, with_type: bool = False) -> str:
        """Internal name such as ``D3`` (``D3:REAL`` with the type).

        Args:
            with_type: Append ``:TYPE``.

        Returns:
            Text.
        """
        return f"D{self.start}" + (f":{self.data_type}" if with_type else "")

    def __str__(self) -> str:
        """Same as :meth:`format`."""
        return self.format()


def parse_dummy_address(text: str | None, data_type: str | None = None, string_length: int | None = None) -> DummyAddress:
    """Build a dummy address; the text is optional (the slot is assigned on save).

    Args:
        text: Empty, or ``D<n>`` / ``D<n>:TYPE``.
        data_type: Type from the form's drop-down.
        string_length: STRING length from the form.

    Returns:
        A validated :class:`DummyAddress` (``start`` 0 when not given).

    Raises:
        DummyAddressError: If the text or type is invalid.
    """
    start, dtype = 0, (data_type or "INT").upper()
    raw = (text or "").strip()
    if raw:
        match = _ADDR_RE.match(raw)
        if not match:
            raise DummyAddressError(gettext('Dummy tags need no address – leave it empty (or use D<number>)'))
        start = int(match.group(1))
        if match.group(2):
            dtype = match.group(2).upper()
    length = int(string_length or 20) if dtype == "STRING" else 0
    return DummyAddress(start=start, data_type=dtype, string_length=length).validate()


def default_value(data_type: str):
    """Initial value of a dummy tag.

    Args:
        data_type: Type name.

    Returns:
        ``False``, ``0``, ``0.0`` or ``""``.
    """
    if data_type == "BOOL":
        return False
    if data_type in ("STRING", "CHAR"):
        return ""
    return 0.0 if data_type in ("REAL", "LREAL") else 0


def coerce_value(value, address: DummyAddress):
    """Convert a raw value for storage, with the range checks of the data type.

    Args:
        value: Raw value from ``DataPoint.to_raw``.
        address: Target slot.

    Returns:
        The value to store.

    Raises:
        ValueError: If it does not fit the data type.
    """
    dtype = address.data_type
    if dtype == "BOOL":
        return bool(value)
    if dtype == "STRING":
        return str(value)[: address.string_length or 254]
    if dtype == "CHAR":
        return str(value)[:1]
    info = DATA_TYPES[dtype]
    if info.integer:
        number = int(round(float(value)))
        if info.min is not None and not info.min <= number <= info.max:
            raise ValueError(f"{number} out of range for {dtype} ({info.min}..{info.max})")
        return number
    return float(value)


def _app_db():
    """Return the SQLAlchemy object when running inside an application context.

    Returns:
        ``db`` or ``None`` (outside a context, e.g. in a plain script).
    """
    from flask import has_app_context  # noqa: PLC0415

    if not has_app_context():
        return None
    from app.extensions import db  # noqa: PLC0415

    return db


class DummyConnection:
    """Connection object of a dummy PLC.

    Values of general dummy tags are read from and written to the database
    table ``dummy_values`` (one row per PLC slot), so all browsers, all server
    processes and server restarts see the same value.  A copy is kept in
    process memory and used only when no database is available (no
    application context, or a database error while reading).
    """

    _memory: dict = {}
    _lock = threading.Lock()

    def __init__(self, plc_id: int):
        """Create the connection.

        Args:
            plc_id: Id of the dummy PLC (separates the values of several dummy PLCs).
        """
        self.plc_id = plc_id
        self._connected = False

    @classmethod
    def for_plc(cls, plc) -> "DummyConnection":
        """Create the connection for a PLC model.

        Args:
            plc: The dummy PLC.

        Returns:
            A :class:`DummyConnection`.
        """
        return cls(plc.id)

    @classmethod
    def reset(cls) -> None:
        """Forget the in-memory copies (used by tests; stored values stay in the database)."""
        with cls._lock:
            cls._memory.clear()

    def connect(self) -> None:
        """Always succeeds."""
        self._connected = True

    @property
    def connected(self) -> bool:
        """Connection state."""
        return self._connected

    def close(self) -> None:
        """Mark the connection as closed (values are kept)."""
        self._connected = False

    def cpu_info(self) -> dict:
        """Description shown by *Test connection*.

        Returns:
            Dict like the S7 CPU info.
        """
        return {"module": "Dummy PLC (no device)", "name": "internal values (stored in the database)",
                "state": "Run"}

    def _load(self, slots) -> dict | None:
        """Read stored values from the database.

        Args:
            slots: Slot numbers to read.

        Returns:
            ``slot -> (data_type, value)``, or ``None`` when the database
            cannot be used (the memory copy is used instead).
        """
        db = _app_db()
        if db is None or not slots:
            return None if db is None else {}
        from app.models.dummy_value import DummyValue  # noqa: PLC0415

        try:
            rows = db.session.execute(
                db.select(DummyValue.slot, DummyValue.data_type, DummyValue.value)
                .where(DummyValue.plc_id == self.plc_id, DummyValue.slot.in_(list(slots)))).all()
        except Exception as exc:  # noqa: BLE001 - e.g. table missing before "flask db upgrade"
            log.warning("Dummy PLC %s: stored values not readable (%s)", self.plc_id, exc)
            return None
        result = {}
        for slot, dtype, raw in rows:
            try:
                result[slot] = (dtype, json.loads(raw))
            except ValueError:
                continue
        return result

    def read_tags(self, addresses: dict, max_gap: int = 0) -> dict:
        """Return the stored values (the type's default for slots never written).

        Args:
            addresses: ``key -> DummyAddress``.
            max_gap: Unused (interface compatibility).

        Returns:
            ``key -> ReadResult``.
        """
        from app.plc.driver import ReadResult  # noqa: PLC0415

        stored = self._load({addr.start for addr in addresses.values()})
        results = {}
        with self._lock:
            for key, addr in addresses.items():
                if stored is None:
                    value = self._memory.get((self.plc_id, addr.start, addr.data_type), default_value(addr.data_type))
                else:
                    dtype, value = stored.get(addr.start, (None, None))
                    if dtype is None:  # never written
                        value = default_value(addr.data_type)
                    else:
                        # Also when the tag's data type was changed (e.g. INT -> UINT) the stored value
                        # is kept if it fits the new type; otherwise the tag starts again at its default.
                        try:
                            value = coerce_value(value, addr)
                        except (TypeError, ValueError, OverflowError):
                            value = default_value(addr.data_type)
                    self._memory[(self.plc_id, addr.start, addr.data_type)] = value
                results[key] = ReadResult(value=value)
        return results

    def write_tag(self, address: DummyAddress, raw_value) -> None:
        """Store a value in the database (and in the memory copy).

        Args:
            address: Target slot.
            raw_value: Raw value.

        Raises:
            ValueError: If the value does not fit the data type.
            DeviceError: If the database refuses the value.
        """
        value = coerce_value(raw_value, address)
        db = _app_db()
        if db is not None:
            from app.models.dummy_value import DummyValue  # noqa: PLC0415
            from app.plc.errors import DeviceError  # noqa: PLC0415

            try:
                row = db.session.execute(db.select(DummyValue).where(
                    DummyValue.plc_id == self.plc_id, DummyValue.slot == address.start)).scalar_one_or_none()
                if row is None:
                    row = DummyValue(plc_id=self.plc_id, slot=address.start)
                    db.session.add(row)
                row.data_type = address.data_type
                row.value = json.dumps(value)
                db.session.commit()
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                raise DeviceError(f"value not stored: {exc}") from exc
        with self._lock:
            self._memory[(self.plc_id, address.start, address.data_type)] = value


def next_slot(plc_id: int) -> int:
    """The next free slot number of a dummy PLC.

    A value still stored for that slot (left by a deleted tag) is forgotten,
    so the new tag starts at its default value.

    Args:
        plc_id: Dummy PLC id.

    Returns:
        ``max(start) + 1`` over the PLC's tags (1 for the first tag).
    """
    from app.extensions import db  # noqa: PLC0415
    from app.models import DataPoint, DummyValue  # noqa: PLC0415

    highest = db.session.query(db.func.max(DataPoint.start)).filter(DataPoint.plc_id == plc_id).scalar()
    slot = int(highest or 0) + 1
    db.session.execute(db.delete(DummyValue).where(DummyValue.plc_id == plc_id, DummyValue.slot >= slot))
    return slot
