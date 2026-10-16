"""Parsing and formatting of Siemens S7 absolute addresses.

Supported syntax (case-insensitive, international or German mnemonics)::

    DB1.DBX0.0      bit 0 of byte 0 in DB1              -> BOOL
    DB1.DBB2        byte 2 in DB1                       -> BYTE
    DB1.DBW4        word starting at byte 4             -> WORD
    DB1.DBD6        double word starting at byte 6      -> DWORD
    M10.5 / MX10.5  memory (merker) bit                 -> BOOL
    MB10 MW10 MD10  memory byte / word / double word
    I0.0  IB0 IW64  process inputs  (German: E0.0, EB0, EW64)
    Q0.1  QB0 QW2   process outputs (German: A0.1, AB0, AW2)
    T5              timer 5    (S7-300/400)             -> WORD
    C3  / Z3        counter 3  (S7-300/400)             -> WORD

An explicit data type may follow after a colon, which overrides the default
derived from the size letter::

    DB1.DBD4:REAL
    MW20:INT
    DB2.DBB10:STRING[20]
    DB3.DBB0:LREAL

The size letter, if present, must be consistent with the data type
(e.g. ``DBW`` cannot hold a ``REAL``).
"""

import re
from dataclasses import dataclass

from app.plc.datatypes import DATA_TYPES, byte_size
from app.i18n.catalog import gettext

#: Areas understood by the application, with their display names.
AREAS = {
    "DB": "Data block (DB)",
    "M": "Memory / Merker (M)",
    "I": "Process inputs (I / E)",
    "Q": "Process outputs (Q / A)",
    "T": "Timers (T)",
    "C": "Counters (C / Z)",
}

#: ``(value, label)`` pairs for WTForms ``SelectField`` choices.
AREA_CHOICES = list(AREAS.items())

_AREA_ALIASES = {"DB": "DB", "M": "M", "I": "I", "E": "I", "Q": "Q", "A": "Q", "T": "T", "C": "C", "Z": "C"}
_SIZE_DEFAULT_TYPE = {"X": "BOOL", "B": "BYTE", "W": "WORD", "D": "DWORD"}
_SIZE_BYTES = {"B": 1, "W": 2, "D": 4}

_TYPE_SUFFIX = r"(?::(?P<type>[A-Z]+)(?:\[(?P<len>\d+)\])?)?"
_DB_RE = re.compile(
    r"^DB(?P<db>\d+)\.DB(?P<size>[XBWD])(?P<start>\d+)(?:\.(?P<bit>[0-7]))?" + _TYPE_SUFFIX + r"$"
)
_PI_RE = re.compile(
    r"^(?P<area>[MIEQA])(?P<size>[XBWD])?(?P<start>\d+)(?:\.(?P<bit>[0-7]))?" + _TYPE_SUFFIX + r"$"
)
_TC_RE = re.compile(r"^(?P<area>[TCZ])(?P<start>\d+)" + _TYPE_SUFFIX + r"$")


class AddressError(ValueError):
    """Raised when an address string cannot be parsed or is inconsistent."""


@dataclass
class S7Address:
    """A fully resolved S7 memory location.

    Attributes:
        area: One of ``DB``, ``M``, ``I``, ``Q``, ``T``, ``C``.
        db_number: Data block number (``0`` for non-DB areas).
        start: Byte offset (or timer/counter number for ``T``/``C``).
        bit: Bit number 0..7 for ``BOOL``, otherwise ``0``.
        data_type: S7 type name, key of :data:`~app.plc.datatypes.DATA_TYPES`.
        string_length: Maximum length for ``STRING``; ``0`` otherwise.
    """

    area: str
    db_number: int = 0
    start: int = 0
    bit: int = 0
    data_type: str = "BYTE"
    string_length: int = 0

    @property
    def size(self) -> int:
        """Number of bytes to read for this address.

        Timers and counters always occupy 2 bytes each.
        """
        if self.area in ("T", "C"):
            return 2
        return byte_size(self.data_type, self.string_length)

    @property
    def is_timer_or_counter(self) -> bool:
        """``True`` for the ``T`` and ``C`` areas (element-addressed)."""
        return self.area in ("T", "C")

    def validate(self) -> "S7Address":
        """Check that all fields are consistent.

        Returns:
            ``self`` to allow chaining.

        Raises:
            AddressError: If any field is out of range or inconsistent.
        """
        if self.area not in AREAS:
            raise AddressError(gettext("Unknown area '{0}'", self.area))
        if self.data_type not in DATA_TYPES:
            raise AddressError(gettext("Unknown data type '{0}'", self.data_type))
        if self.area == "DB" and not 1 <= self.db_number <= 65535:
            raise AddressError(gettext('DB number must be between 1 and 65535'))
        if self.area != "DB":
            self.db_number = 0
        if not 0 <= self.start <= 65535:
            raise AddressError(gettext('Start address must be between 0 and 65535'))
        if not 0 <= self.bit <= 7:
            raise AddressError(gettext('Bit number must be between 0 and 7'))
        if self.data_type != "BOOL":
            self.bit = 0
        if self.data_type == "STRING":
            if not 1 <= self.string_length <= 254:
                raise AddressError(gettext('STRING length must be between 1 and 254'))
        else:
            self.string_length = 0
        if self.area in ("T", "C") and self.data_type not in ("WORD", "INT", "UINT"):
            raise AddressError(gettext('Timers and counters must use WORD, INT or UINT'))
        return self

    def format(self, with_type: bool = False) -> str:
        """Return the canonical Siemens address string.

        Args:
            with_type: Append ``:TYPE`` (and ``[n]`` for strings), producing a
                string that :func:`parse_address` maps back to the same value.

        Returns:
            For example ``DB1.DBX0.3``, ``MW20`` or ``DB2.DBD4:REAL``.
        """
        if self.area in ("T", "C"):
            text = f"{self.area}{self.start}"
        else:
            if self.data_type == "BOOL":
                letter = "X"
            else:
                letter = {1: "B", 2: "W", 4: "D"}.get(DATA_TYPES[self.data_type].size, "B")
            if self.area == "DB":
                text = f"DB{self.db_number}.DB{letter}{self.start}"
            else:
                text = f"{self.area}{'' if letter == 'X' else letter}{self.start}"
            if self.data_type == "BOOL":
                text += f".{self.bit}"
        if with_type:
            text += f":{self.data_type}"
            if self.data_type == "STRING":
                text += f"[{self.string_length}]"
        return text

    def __str__(self) -> str:
        """Return :meth:`format` with the data type appended."""
        return self.format(with_type=True)

    def snap7_area(self):
        """Map the area to the ``snap7`` ``Area`` enum.

        Returns:
            ``Area.DB``, ``Area.MK``, ``Area.PE``, ``Area.PA``, ``Area.TM`` or
            ``Area.CT``.
        """
        try:
            from snap7.type import Area  # python-snap7 >= 2.0  # noqa: PLC0415
        except ImportError:  # pragma: no cover - python-snap7 1.x
            from snap7.types import Areas as Area  # noqa: PLC0415
        return {
            "DB": Area.DB,
            "M": Area.MK,
            "I": Area.PE,
            "Q": Area.PA,
            "T": Area.TM,
            "C": Area.CT,
        }[self.area]


def _check_size_letter(letter: str | None, data_type: str) -> None:
    """Verify that a size letter (X/B/W/D) fits the chosen data type.

    Args:
        letter: Size letter from the address, or ``None`` if absent.
        data_type: S7 type name.

    Raises:
        AddressError: If they are incompatible, e.g. ``DBW`` with ``REAL``.
    """
    if letter is None:
        return
    if letter == "X":
        if data_type != "BOOL":
            raise AddressError(gettext('Bit addresses (X / x.y) can only be BOOL'))
        return
    if data_type == "BOOL":
        raise AddressError(gettext('BOOL needs a bit address such as DB1.DBX0.0 or M10.5'))
    size = DATA_TYPES[data_type].size
    if size in (1, 2, 4) and _SIZE_BYTES[letter] != size:
        raise AddressError(gettext("{0} is {1} byte(s) but the address size letter '{2}' is {3}", data_type, size, letter, _SIZE_BYTES[letter]))


def parse_address(text: str, data_type: str | None = None, string_length: int | None = None) -> S7Address:
    """Parse an S7 address string.

    Args:
        text: Address such as ``"DB1.DBD4"``, ``"M10.5"`` or ``"IW64:INT"``.
        data_type: Optional type that overrides the default derived from the
            size letter (a ``:TYPE`` suffix inside ``text`` wins over this).
        string_length: Length for ``STRING`` when not given as ``[n]``.

    Returns:
        A validated :class:`S7Address`.

    Raises:
        AddressError: If the text is not a valid/consistent address.
    """
    if not text or not text.strip():
        raise AddressError(gettext('Address is empty'))
    raw = re.sub(r"\s+", "", text).upper()

    m = _DB_RE.match(raw)
    if m:
        area, size, db = "DB", m["size"], int(m["db"])
    else:
        m = _PI_RE.match(raw)
        if m:
            area, db = _AREA_ALIASES[m["area"]], 0
            size = m["size"] or ("X" if m["bit"] is not None else None)
            if size is None:
                raise AddressError(gettext("'{0}' needs a size letter (B, W, D) or a bit number (.0-.7)", text))
        else:
            m = _TC_RE.match(raw)
            if not m:
                raise AddressError(gettext("'{0}' is not a valid S7 address", text))
            area, db, size = _AREA_ALIASES[m["area"]], 0, None

    bit_text = m.groupdict().get("bit")
    if size == "X" and bit_text is None:
        raise AddressError(gettext('Bit addresses need a bit number, e.g. DBX0.0'))
    if size in ("B", "W", "D") and bit_text is not None:
        raise AddressError(gettext('Only bit (X) addresses may have a bit number'))

    dtype = m["type"] or (data_type.upper() if data_type else None)
    if dtype is None:
        dtype = "WORD" if area in ("T", "C") else _SIZE_DEFAULT_TYPE[size]
    if dtype not in DATA_TYPES:
        raise AddressError(gettext("Unknown data type '{0}'", dtype))
    if area not in ("T", "C"):
        _check_size_letter(size, dtype)

    length = int(m["len"]) if m["len"] else int(string_length or 0)
    if dtype == "STRING" and not length:
        length = 254

    return S7Address(
        area=area,
        db_number=db,
        start=int(m["start"]),
        bit=int(bit_text) if bit_text is not None else 0,
        data_type=dtype,
        string_length=length,
    ).validate()
