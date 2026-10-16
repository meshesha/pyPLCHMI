"""Tests for S7 address parsing/formatting and data type conversion."""

from datetime import datetime

import pytest

from app.plc.addressing import AddressError, parse_address
from app.plc.datatypes import byte_size, decode, encode


@pytest.mark.parametrize(
    "text,area,db,start,bit,dtype",
    [
        ("DB1.DBX0.0", "DB", 1, 0, 0, "BOOL"),
        ("db12.dbx3.7", "DB", 12, 3, 7, "BOOL"),
        ("DB1.DBB2", "DB", 1, 2, 0, "BYTE"),
        ("DB1.DBW4", "DB", 1, 4, 0, "WORD"),
        ("DB1.DBD6", "DB", 1, 6, 0, "DWORD"),
        ("DB1.DBD6:REAL", "DB", 1, 6, 0, "REAL"),
        ("M10.5", "M", 0, 10, 5, "BOOL"),
        ("MX10.5", "M", 0, 10, 5, "BOOL"),
        ("MW20:INT", "M", 0, 20, 0, "INT"),
        ("I0.1", "I", 0, 0, 1, "BOOL"),
        ("E0.1", "I", 0, 0, 1, "BOOL"),
        ("IW64", "I", 0, 64, 0, "WORD"),
        ("Q4.0", "Q", 0, 4, 0, "BOOL"),
        ("AB2", "Q", 0, 2, 0, "BYTE"),
        ("T5", "T", 0, 5, 0, "WORD"),
        ("Z3", "C", 0, 3, 0, "WORD"),
        ("DB3.DBB0:LREAL", "DB", 3, 0, 0, "LREAL"),
    ],
)
def test_parse_valid(text, area, db, start, bit, dtype):
    """Valid addresses are decomposed correctly."""
    a = parse_address(text)
    assert (a.area, a.db_number, a.start, a.bit, a.data_type) == (area, db, start, bit, dtype)


def test_parse_with_type_argument():
    """The data_type argument overrides the size-letter default."""
    assert parse_address("DB1.DBD4", "REAL").data_type == "REAL"
    assert parse_address("MW2", "INT").data_type == "INT"


def test_suffix_beats_argument():
    """A ``:TYPE`` suffix wins over the data_type argument."""
    assert parse_address("DB1.DBD4:DINT", "REAL").data_type == "DINT"


def test_string_length():
    """STRING length comes from ``[n]`` or the argument."""
    a = parse_address("DB2.DBB10:STRING[20]")
    assert (a.data_type, a.string_length, a.size) == ("STRING", 20, 22)
    assert parse_address("DB2.DBB10", "STRING", 5).string_length == 5


@pytest.mark.parametrize(
    "text,dtype",
    [
        ("", None),
        ("XYZ", None),
        ("DB0.DBW0", None),  # DB number 0
        ("DB1.DBX0", None),  # missing bit
        ("DB1.DBX0.8", None),  # bit 8
        ("DB1.DBW0.1", None),  # bit on word
        ("M10", None),  # no size letter, no bit
        ("DB1.DBW0", "REAL"),  # 2 bytes vs 4
        ("DB1.DBD0", "BOOL"),  # bool needs bit address
        ("DB1.DBX0.0", "INT"),  # bit address for INT
        ("T1", "REAL"),  # timers are words
        ("DB1.DBB0:FOO", None),  # unknown type
        ("DB1.DBB0:STRING[300]", None),  # too long
    ],
)
def test_parse_invalid(text, dtype):
    """Invalid or inconsistent addresses raise AddressError."""
    with pytest.raises(AddressError):
        parse_address(text, dtype)


@pytest.mark.parametrize(
    "text", ["DB1.DBX0.3:BOOL", "DB5.DBD8:REAL", "MW2:INT", "IB0:BYTE", "Q1.7:BOOL", "T4:WORD", "DB2.DBB10:STRING[20]", "DB3.DBB0:LREAL"]
)
def test_format_roundtrip(text):
    """``format(with_type=True)`` reproduces a parseable, equal address."""
    a = parse_address(text)
    assert a.format(with_type=True) == text
    assert parse_address(a.format(with_type=True)) == a


@pytest.mark.parametrize(
    "dtype,value",
    [
        ("BYTE", 200), ("USINT", 7), ("SINT", -5), ("CHAR", "A"), ("WORD", 65535), ("UINT", 1234),
        ("INT", -32768), ("DWORD", 4000000000), ("UDINT", 99), ("DINT", -2000000000),
        ("REAL", 1.5), ("LREAL", 3.141592653589793), ("STRING", "hello"),
    ],
)
def test_encode_decode_roundtrip(dtype, value):
    """Every writable type survives encode → decode."""
    buf = encode(value, dtype, 10)
    assert len(buf) == byte_size(dtype, 10)
    assert decode(buf, 0, dtype) == value


def test_bool_decode():
    """BOOL values are read from the right bit."""
    buf = bytearray([0b00100000])
    assert decode(buf, 0, "BOOL", 5) is True
    assert decode(buf, 0, "BOOL", 4) is False


def test_encode_range_check():
    """Out-of-range integers are rejected."""
    with pytest.raises(ValueError):
        encode(70000, "INT")
    with pytest.raises(ValueError):
        encode(-1, "WORD")


def test_encode_string_too_long():
    """Strings longer than their declared length are rejected."""
    with pytest.raises(ValueError):
        encode("toolong", "STRING", 3)


def test_dtl_read_only():
    """DTL can be decoded but not encoded."""
    buf = bytearray([0x07, 0xEA, 10, 14, 4, 12, 30, 15, 0, 0, 0, 0])
    assert isinstance(decode(buf, 0, "DTL"), datetime)
    with pytest.raises(ValueError):
        encode(datetime.now(), "DTL")
