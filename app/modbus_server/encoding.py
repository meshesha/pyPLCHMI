"""How tag values are written into Modbus registers and bits.

Each published tag has a **Modbus type**:

=========  ==========  ==============================================================
Type       Registers   Value
=========  ==========  ==============================================================
BIT        (1 bit)     coil / discrete input: ON = 1 (BOOL tags)
BOOL16     1           0 or 1 in a register (for SCADA systems that read only registers)
INT16      1           −32768 … 32767
UINT16     1           0 … 65535
INT32      2           −2147483648 … 2147483647
UINT32     2           0 … 4294967295
FLOAT32    2           IEEE 754 single precision
FLOAT64    4           IEEE 754 double precision
STRING     n / 2       ASCII / Latin-1 text, 2 characters per register, padded with 0
=========  ==========  ==============================================================

32/64-bit values and strings use the server's **value order** (``ABCD`` = big
endian, the Modbus convention; ``CDAB`` word swapped; ``BADC`` byte swapped;
``DCBA`` little endian) – the same names as in the Modbus client settings of
WinCC, ControlMaestro, Ignition, Kepware …

Before encoding, numbers are multiplied by the tag's *Modbus scale* (e.g. 10 to
send 21.5 °C as the integer 215).  Integers are rounded and **clamped** to the
type's range (a value outside it is sent as the nearest limit).
"""

from __future__ import annotations

import math
import struct

from app.i18n.catalog import gettext

#: Modbus type → (registers, description).  ``BIT`` uses the bit area (0 registers).
TYPES = {
    "BIT": (0, "BIT – coil / discrete input (BOOL)"),
    "BOOL16": (1, "BOOL16 – 0/1 in a register"),
    "INT16": (1, "INT16 – 16-bit signed"),
    "UINT16": (1, "UINT16 – 16-bit unsigned"),
    "INT32": (2, "INT32 – 32-bit signed (2 registers)"),
    "UINT32": (2, "UINT32 – 32-bit unsigned (2 registers)"),
    "FLOAT32": (2, "FLOAT32 – 32-bit float (2 registers)"),
    "FLOAT64": (4, "FLOAT64 – 64-bit float (4 registers)"),
    "STRING": (0, "STRING – text, 2 characters per register"),
}
#: Value orders (as in the Modbus client settings of SCADA systems).
ORDERS = {
    "ABCD": "ABCD – big endian (Modbus standard)",
    "CDAB": "CDAB – word swapped",
    "BADC": "BADC – byte swapped",
    "DCBA": "DCBA – little endian",
}
_INT_RANGES = {"INT16": (-32768, 32767), "UINT16": (0, 65535), "INT32": (-2**31, 2**31 - 1),
               "UINT32": (0, 2**32 - 1)}
_PACK = {"INT16": ">h", "UINT16": ">H", "INT32": ">i", "UINT32": ">I", "FLOAT32": ">f", "FLOAT64": ">d",
         "BOOL16": ">H"}
#: Longest published STRING (characters).
MAX_STRING = 254


def register_count(mb_type: str, string_length: int = 0) -> int:
    """Number of 16-bit registers a type occupies (0 for ``BIT``).

    Args:
        mb_type: Modbus type.
        string_length: Characters (``STRING`` only).
    """
    if mb_type == "STRING":
        return max(1, (int(string_length or 0) + 1) // 2)
    return TYPES[mb_type][0]


def is_bit(mb_type: str) -> bool:
    """``True`` for types stored in the bit area (coils / discrete inputs)."""
    return mb_type == "BIT"


def _order_words(data: bytes, order: str) -> list[int]:
    """Split big-endian bytes into registers in the requested value order."""
    words = [data[i:i + 2] for i in range(0, len(data), 2)]
    if order in ("BADC", "DCBA"):
        words = [w[::-1] for w in words]
    if order in ("CDAB", "DCBA"):
        words = words[::-1]
    return [int.from_bytes(w, "big") for w in words]


def encode(value, mb_type: str, order: str = "ABCD", scale: float = 1.0, string_length: int = 0):
    """Encode one tag value.

    Args:
        value: Engineering value of the tag (number, bool or text).
        mb_type: Modbus type.
        order: Value order (``ABCD`` …).
        scale: Multiplier applied to numbers before encoding.
        string_length: Characters for ``STRING``.

    Returns:
        ``bool`` for ``BIT``, otherwise a list of register values (0..65535).

    Raises:
        ValueError: If the value cannot be shown in this type (text in a number type…).
    """
    if mb_type == "BIT":
        return _truth(value)
    if mb_type == "STRING":
        length = register_count("STRING", string_length) * 2
        raw = str("" if value is None else value).encode("latin-1", errors="replace")[:string_length or length]
        data = raw.ljust(length, b"\0")
        # Text keeps its character order; only the byte order inside a register can be swapped.
        return _order_words(data, "BADC" if order in ("BADC", "DCBA") else "ABCD")
    if mb_type == "BOOL16":
        return [1 if _truth(value) else 0]
    number = _number(value) * (scale if scale not in (None, 0) else 1.0)
    if mb_type in _INT_RANGES:
        if math.isnan(number):
            raise ValueError(gettext("not a number"))
        lo, hi = _INT_RANGES[mb_type]
        number = int(max(lo, min(hi, round(number)))) if not math.isinf(number) else (hi if number > 0 else lo)
    return _order_words(struct.pack(_PACK[mb_type], number), order)


def _join_words(words: list[int], order: str) -> bytes:
    """Registers in a value order → big-endian bytes (inverse of :func:`_order_words`)."""
    parts = [int(w & 0xFFFF).to_bytes(2, "big") for w in words]
    if order in ("CDAB", "DCBA"):
        parts = parts[::-1]
    if order in ("BADC", "DCBA"):
        parts = [p[::-1] for p in parts]
    return b"".join(parts)


def decode(data, mb_type: str, order: str = "ABCD", scale: float = 1.0, string_length: int = 0):
    """Turn written registers / a written bit back into a tag value (inverse of :func:`encode`).

    Args:
        data: ``bool`` for ``BIT``, otherwise the list of register values.
        mb_type: Modbus type.
        order: Value order.
        scale: The published tag's multiplier (the value is divided by it).
        string_length: Characters for ``STRING``.

    Returns:
        ``bool``, ``int``, ``float`` or ``str`` (engineering value of the tag).

    Raises:
        ValueError: For the wrong number of registers or a value that is not a number (NaN).
    """
    if mb_type == "BIT":
        return bool(data)
    words = list(data)
    if len(words) != register_count(mb_type, string_length):
        raise ValueError(gettext("{0} needs {1} registers.", mb_type, register_count(mb_type, string_length)))
    if mb_type == "STRING":
        raw = _join_words(words, "BADC" if order in ("BADC", "DCBA") else "ABCD")
        return raw.split(b"\0", 1)[0].decode("latin-1")[: string_length or None]
    if mb_type == "BOOL16":
        return words[0] != 0
    number = struct.unpack(_PACK[mb_type], _join_words(words, order))[0]
    if isinstance(number, float) and (math.isnan(number) or math.isinf(number)):
        raise ValueError(gettext("not a number"))
    divisor = scale if scale not in (None, 0) else 1.0
    if mb_type in _INT_RANGES and divisor == 1.0:
        return int(number)
    return number / divisor


def zero(mb_type: str, string_length: int = 0):
    """The value sent when a tag has no valid value and the server sends zeros."""
    return False if mb_type == "BIT" else [0] * register_count(mb_type, string_length)


def _truth(value) -> bool:
    """Value → bool (numbers: not 0; text: true/on/1)."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes")
    return bool(value)


def _number(value) -> float:
    """Value → float."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        raise ValueError(gettext("'{0}' is not a number", value)) from None


def default_type(dp) -> tuple[str, int]:
    """Suggested Modbus type for a tag.

    BOOL → ``BIT``; STRING/CHAR → ``STRING``; scaled numbers and REAL/LREAL →
    ``FLOAT32``; integers → the smallest fitting integer type.

    Args:
        dp: The :class:`~app.models.dashboard.DataPoint`.

    Returns:
        ``(mb_type, string_length)``.
    """
    from app.plc.datatypes import DATA_TYPES  # noqa: PLC0415

    if dp.data_type == "BOOL":
        return "BIT", 0
    if dp.data_type in ("STRING", "CHAR"):
        return "STRING", min(MAX_STRING, max(2, dp.string_length or (1 if dp.data_type == "CHAR" else 20)))
    info = DATA_TYPES.get(dp.data_type)
    scaled = (dp.scale not in (None, 1, 1.0)) or bool(dp.offset)
    if info is None or not info.integer or scaled:
        return "FLOAT32", 0
    for name in ("INT16", "UINT16", "INT32", "UINT32"):
        lo, hi = _INT_RANGES[name]
        if info.min is not None and lo <= info.min and info.max <= hi:
            return name, 0
    return "FLOAT32", 0


def notation(area: str, address: int, read_only_regs: bool = False) -> str:
    """Classic Modbus reference (1-based): registers ``40001``…, bits ``00001``….

    Args:
        area: ``reg`` or ``bit``.
        address: 0-based address.
        read_only_regs: Return the input register / discrete input form (``3xxxx`` / ``1xxxx``).
    """
    if area == "bit":
        prefix = 1 if read_only_regs else 0
    else:
        prefix = 3 if read_only_regs else 4
    number = address + 1
    return f"{prefix}{number:05d}" if number > 9999 else f"{prefix}{number:04d}"
