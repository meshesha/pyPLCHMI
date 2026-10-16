"""S7 data types and their conversion to/from raw bytes.

All conversions are delegated to :mod:`snap7.util` (``get_real``,
``set_int``, ``get_string`` ...).  This module adds a small registry
(:data:`DATA_TYPES`) describing each supported type – its size in bytes,
whether it is numeric/integer, its valid range and whether it may be written
– so the rest of the application can stay generic.
"""

from dataclasses import dataclass
from datetime import date, datetime, time


@dataclass(frozen=True)
class DataTypeInfo:
    """Description of one S7 elementary data type.

    Attributes:
        name: S7 type name (``"REAL"``).
        size: Size in bytes (``0`` means *variable*, i.e. STRING).
        numeric: ``True`` if the value is a number (scaling applies).
        integer: ``True`` for integer types.
        min: Smallest raw value (integers only).
        max: Largest raw value (integers only).
        writable: ``False`` for types that are only supported for reading.
        description: Text shown in the data type drop-down.
    """

    name: str
    size: int
    numeric: bool
    integer: bool
    min: int | None
    max: int | None
    writable: bool
    description: str


def _t(name, size, numeric, integer, lo, hi, writable, desc) -> tuple[str, DataTypeInfo]:
    """Build one ``(name, DataTypeInfo)`` registry entry.

    Args:
        name: Type name.
        size: Size in bytes.
        numeric: Numeric flag.
        integer: Integer flag.
        lo: Minimum raw value or ``None``.
        hi: Maximum raw value or ``None``.
        writable: Whether writing is supported.
        desc: Description for the UI.

    Returns:
        A tuple suitable for building a ``dict``.
    """
    return name, DataTypeInfo(name, size, numeric, integer, lo, hi, writable, desc)


#: Registry of all supported data types, keyed by S7 type name.
DATA_TYPES: dict[str, DataTypeInfo] = dict(
    [
        _t("BOOL", 1, False, False, None, None, True, "BOOL – single bit"),
        _t("BYTE", 1, True, True, 0, 255, True, "BYTE – 8 bit unsigned"),
        _t("USINT", 1, True, True, 0, 255, True, "USINT – 8 bit unsigned"),
        _t("SINT", 1, True, True, -128, 127, True, "SINT – 8 bit signed"),
        _t("CHAR", 1, False, False, None, None, True, "CHAR – one character"),
        _t("WORD", 2, True, True, 0, 65535, True, "WORD – 16 bit unsigned"),
        _t("UINT", 2, True, True, 0, 65535, True, "UINT – 16 bit unsigned"),
        _t("INT", 2, True, True, -32768, 32767, True, "INT – 16 bit signed"),
        _t("DWORD", 4, True, True, 0, 4294967295, True, "DWORD – 32 bit unsigned"),
        _t("UDINT", 4, True, True, 0, 4294967295, True, "UDINT – 32 bit unsigned"),
        _t("DINT", 4, True, True, -2147483648, 2147483647, True, "DINT – 32 bit signed"),
        _t("REAL", 4, True, False, None, None, True, "REAL – 32 bit float"),
        _t("LREAL", 8, True, False, None, None, True, "LREAL – 64 bit float"),
        _t("TIME", 4, False, False, None, None, True, "TIME – IEC duration (d:h:m:s.ms)"),
        _t("DTL", 12, False, False, None, None, False, "DTL – date and time (read only)"),
        _t("STRING", 0, False, False, None, None, True, "STRING[n] – S7 string"),
    ]
)

#: ``(value, label)`` pairs for WTForms ``SelectField`` choices.
DATA_TYPE_CHOICES = [(name, info.description) for name, info in DATA_TYPES.items()]


def _util():
    """Import :mod:`snap7.util` lazily.

    Keeping the import inside a function lets the web application start (and
    show a helpful error) even if ``python-snap7`` is missing.

    Returns:
        The ``snap7.util`` module.
    """
    import snap7.util as util  # noqa: PLC0415

    return util


def is_numeric(data_type: str) -> bool:
    """Tell whether a data type holds a number.

    Args:
        data_type: S7 type name.

    Returns:
        ``True`` for numeric types, ``False`` otherwise or if unknown.
    """
    info = DATA_TYPES.get(data_type)
    return bool(info and info.numeric)


def byte_size(data_type: str, string_length: int = 0) -> int:
    """Number of bytes a value occupies in PLC memory.

    Args:
        data_type: S7 type name.
        string_length: Declared maximum length for ``STRING`` (``STRING[n]``).

    Returns:
        Size in bytes.  An S7 ``STRING[n]`` uses ``n + 2`` bytes (max length
        byte, actual length byte, characters).

    Raises:
        ValueError: For unknown data types.
    """
    if data_type not in DATA_TYPES:
        raise ValueError(f"Unsupported data type: {data_type}")
    if data_type == "STRING":
        return max(int(string_length or 0), 1) + 2
    return DATA_TYPES[data_type].size


def decode(buffer: bytearray, offset: int, data_type: str, bit: int = 0):
    """Decode a value from a raw byte buffer using :mod:`snap7.util`.

    Args:
        buffer: Bytes read from the PLC (``client.read_area`` result).
        offset: Index in ``buffer`` where the value starts.
        data_type: S7 type name.
        bit: Bit number 0..7 (only for ``BOOL``).

    Returns:
        A Python value: ``bool``, ``int``, ``float``, ``str`` or ``datetime``.

    Raises:
        ValueError: For unknown data types.
    """
    util = _util()
    readers = {
        "BOOL": lambda: util.get_bool(buffer, offset, bit),
        "BYTE": lambda: util.get_byte(buffer, offset),
        "USINT": lambda: util.get_usint(buffer, offset),
        "SINT": lambda: util.get_sint(buffer, offset),
        "CHAR": lambda: util.get_char(buffer, offset),
        "WORD": lambda: util.get_word(buffer, offset),
        "UINT": lambda: util.get_uint(buffer, offset),
        "INT": lambda: util.get_int(buffer, offset),
        "DWORD": lambda: util.get_dword(buffer, offset),
        "UDINT": lambda: util.get_udint(buffer, offset),
        "DINT": lambda: util.get_dint(buffer, offset),
        "REAL": lambda: util.get_real(buffer, offset),
        "LREAL": lambda: util.get_lreal(buffer, offset),
        "TIME": lambda: util.get_time(buffer, offset),
        "DTL": lambda: util.get_dtl(buffer, offset),
        "STRING": lambda: util.get_string(buffer, offset),
    }
    if data_type not in readers:
        raise ValueError(f"Unsupported data type: {data_type}")
    value = readers[data_type]()
    if isinstance(value, (bytes, bytearray)):  # some snap7 versions return bytes for BYTE
        value = value[0] if len(value) == 1 else value.decode("latin-1")
    return value


def encode(value, data_type: str, string_length: int = 0) -> bytearray:
    """Encode a Python value into the S7 byte representation.

    ``BOOL`` values are *not* handled here because a single bit must be
    merged into the existing byte; see :func:`app.plc.driver.BaseDriver.write`.

    Args:
        value: Value to encode (already converted to raw units).
        data_type: S7 type name.
        string_length: Maximum length for ``STRING``.

    Returns:
        A new ``bytearray`` of :func:`byte_size` bytes.

    Raises:
        ValueError: If the type is unknown, read-only, ``BOOL``, or the value
            does not fit.
    """
    info = DATA_TYPES.get(data_type)
    if info is None:
        raise ValueError(f"Unsupported data type: {data_type}")
    if not info.writable:
        raise ValueError(f"Data type {data_type} is read-only")
    if data_type == "BOOL":
        raise ValueError("BOOL values are written bit-wise, not with encode()")
    if info.integer:
        value = int(value)
        if not info.min <= value <= info.max:
            raise ValueError(f"{value} out of range for {data_type} ({info.min}..{info.max})")
    util = _util()
    buf = bytearray(byte_size(data_type, string_length))
    writers = {
        "BYTE": lambda: util.set_byte(buf, 0, value),
        "USINT": lambda: util.set_usint(buf, 0, value),
        "SINT": lambda: util.set_sint(buf, 0, value),
        "CHAR": lambda: util.set_char(buf, 0, str(value)[:1] or " "),
        "WORD": lambda: util.set_word(buf, 0, value),
        "UINT": lambda: util.set_uint(buf, 0, value),
        "INT": lambda: util.set_int(buf, 0, value),
        "DWORD": lambda: util.set_dword(buf, 0, value),
        "UDINT": lambda: util.set_udint(buf, 0, value),
        "DINT": lambda: util.set_dint(buf, 0, value),
        "REAL": lambda: util.set_real(buf, 0, float(value)),
        "LREAL": lambda: util.set_lreal(buf, 0, float(value)),
        "TIME": lambda: util.set_time(buf, 0, str(value)),
        "STRING": lambda: _set_string(util, buf, str(value), string_length),
    }
    writers[data_type]()
    return buf


def _set_string(util, buf: bytearray, value: str, string_length: int) -> None:
    """Write an S7 ``STRING[n]`` into ``buf`` with length checking.

    Args:
        util: The ``snap7.util`` module.
        buf: Destination buffer of ``string_length + 2`` bytes.
        value: Text to store.
        string_length: Declared maximum length ``n``.

    Raises:
        ValueError: If ``value`` is longer than ``string_length``.
    """
    max_len = max(int(string_length or 0), 1)
    if len(value) > max_len:
        raise ValueError(f"String longer than STRING[{max_len}]")
    util.set_string(buf, 0, value, max_len)


def json_safe(value):
    """Convert decoded PLC values into JSON-serialisable objects.

    Args:
        value: Output of :func:`decode` (possibly scaled).

    Returns:
        ``datetime``/``date``/``time`` as ISO strings; other values unchanged.
    """
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value
