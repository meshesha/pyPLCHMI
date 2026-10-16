"""Tests for Modbus support: addressing, encoding, planning, simulator, real
pymodbus / pyModbusTCP clients against a pymodbus server, and the web forms."""

import asyncio
import logging
import socket
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from app.models import PLC, DataPoint
from app.plc.addressing import AddressError
from app.plc.driver import PLCConnectionManager, PLCError
from app.plc.modbus import (
    ModbusAddress, ModbusAddressError, ModbusConnection, ModbusSettings, PyModbusAdapter, PyModbusTCPAdapter,
    SimulatedModbusAdapter, decode_registers, encode_registers, parse_modbus_address, plan_modbus_reads,
)
from app.plc.protocols import parse_tag_address


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,area,start,bit,dtype", [
    ("HR100", "HR", 100, 0, "UINT"),
    ("hr100:real", "HR", 100, 0, "REAL"),
    ("HR10.3", "HR", 10, 3, "BOOL"),
    ("IR5:INT", "IR", 5, 0, "INT"),
    ("C12", "CO", 12, 0, "BOOL"),
    ("CO12", "CO", 12, 0, "BOOL"),
    ("DI3", "DI", 3, 0, "BOOL"),
    ("40001", "HR", 0, 0, "UINT"),
    ("400001:DINT", "HR", 0, 0, "DINT"),
    ("30010", "IR", 9, 0, "UINT"),
    ("10001", "DI", 0, 0, "BOOL"),
    ("00001", "CO", 0, 0, "BOOL"),
    ("40011.15", "HR", 10, 15, "BOOL"),
])
def test_parse_modbus(text, area, start, bit, dtype):
    """Named and Modicon notations are decoded."""
    a = parse_modbus_address(text)
    assert (a.area, a.start, a.bit, a.data_type) == (area, start, bit, dtype)


def test_parse_type_argument_and_strings():
    """The drop-down type applies unless a suffix is given; bit areas stay BOOL."""
    assert parse_modbus_address("HR4", "REAL").data_type == "REAL"
    assert parse_modbus_address("HR4:DINT", "REAL").data_type == "DINT"
    assert parse_modbus_address("C4", "INT").data_type == "BOOL"  # drop-down ignored for bits
    s = parse_modbus_address("HR20:STRING[15]")
    assert (s.string_length, s.size) == (15, 8)


@pytest.mark.parametrize("text,dtype", [
    ("", None), ("XX1", None), ("HR70000", None), ("C5:INT", None), ("HR5.16", None),
    ("HR5", "BOOL"), ("HR5:TIME", None), ("HR65535:REAL", None), ("40000", None), ("HR1:STRING[300]", None),
])
def test_parse_modbus_invalid(text, dtype):
    """Invalid Modbus addresses raise ModbusAddressError."""
    with pytest.raises(ModbusAddressError):
        parse_modbus_address(text, dtype)


@pytest.mark.parametrize("text", ["HR100:UINT", "HR10.3:BOOL", "IR5:REAL", "CO7:BOOL", "DI0:BOOL", "HR2:STRING[6]", "HR8:LREAL"])
def test_format_roundtrip(text):
    """format(with_type=True) reproduces the address."""
    a = parse_modbus_address(text)
    assert a.format(with_type=True) == text and parse_modbus_address(str(a)) == a


def test_protocol_dispatch():
    """parse_tag_address picks the syntax of the protocol."""
    assert parse_tag_address("HR1", protocol="modbus_tcp").area == "HR"
    assert parse_tag_address("DB1.DBW0", protocol="s7").area == "DB"
    with pytest.raises(AddressError):
        parse_tag_address("DB1.DBW0", protocol="modbus_rtu")
    with pytest.raises(AddressError):
        parse_tag_address("HR1", protocol="s7")


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------
REAL = ModbusAddress("HR", 0, data_type="REAL")


@pytest.mark.parametrize("order,regs", [
    (("big", "big"), [0x3F80, 0x0000]),        # ABCD
    (("big", "little"), [0x0000, 0x3F80]),     # CDAB
    (("little", "big"), [0x803F, 0x0000]),     # BADC
    (("little", "little"), [0x0000, 0x803F]),  # DCBA
])
def test_real_byte_word_orders(order, regs):
    """1.0 as REAL in all four byte/word orders."""
    assert encode_registers(1.0, REAL, *order) == regs
    assert decode_registers(regs, REAL, *order) == 1.0


@pytest.mark.parametrize("dtype,value", [("INT", -1234), ("UINT", 65535), ("DINT", -100000), ("UDINT", 4000000000),
                                         ("REAL", 2.5), ("LREAL", 3.141592653589793), ("WORD", 7)])
@pytest.mark.parametrize("order", [("big", "big"), ("big", "little"), ("little", "little")])
def test_roundtrip_types(dtype, value, order):
    """Every numeric type survives encode → decode."""
    addr = ModbusAddress("HR", 0, data_type=dtype)
    assert decode_registers(encode_registers(value, addr, *order), addr, *order) == value


def test_string_and_bits():
    """Strings pack 2 chars per register; bits are taken from registers."""
    s = ModbusAddress("HR", 0, data_type="STRING", string_length=5)
    regs = encode_registers("ABCDE", s)
    assert regs == [0x4142, 0x4344, 0x4500] and decode_registers(regs, s) == "ABCDE"
    assert decode_registers([0b1000], ModbusAddress("HR", 0, bit=3, data_type="BOOL")) is True
    with pytest.raises(ValueError):
        encode_registers("TOO LONG", s)
    with pytest.raises(ValueError):
        encode_registers(70000, ModbusAddress("HR", 0, data_type="INT"))


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
def test_plan_merges_and_splits():
    """Neighbouring tags share requests; protocol limits are respected."""
    tags = {t: parse_modbus_address(t) for t in ("HR0:REAL", "HR2", "HR3.1", "HR10", "HR400", "C0", "C5", "IR0")}
    blocks = plan_modbus_reads(tags, max_gap=16)
    by_area = {}
    for b in blocks:
        by_area.setdefault(b.area, []).append((b.start, b.count))
    assert by_area["HR"] == [(0, 11), (400, 1)]
    assert by_area["CO"] == [(0, 6)] and by_area["IR"] == [(0, 1)]
    many = {i: ModbusAddress("HR", i * 2, data_type="REAL") for i in range(100)}  # 200 registers
    assert all(b.count <= 125 for b in plan_modbus_reads(many))


# ---------------------------------------------------------------------------
# Simulator through the connection manager
# ---------------------------------------------------------------------------
def fake_modbus_plc(plc_id=50, host="10.1.1.1", **kw):
    """Minimal Modbus PLC-like object.

    Args:
        plc_id: Id.
        host: IP address.
        **kw: Attribute overrides.

    Returns:
        SimpleNamespace with the attributes the manager uses.
    """
    attrs = dict(id=plc_id, name="MB", protocol="modbus_tcp", modbus_library="pymodbus", ip_address=host, tcp_port=502,
                 unit_id=1, byte_order="big", word_order="big", timeout=1.0, serial_port="", baudrate=9600,
                 parity="N", stopbits=1, bytesize=8, enabled=True, rack=0, slot=0)
    attrs.update(kw)
    ns = SimpleNamespace(**attrs)
    ns.connection_signature = tuple(sorted(attrs.items()))
    return ns


@pytest.fixture()
def sim_manager():
    """A simulation-mode manager with fresh simulated memory.

    Returns:
        PLCConnectionManager.
    """
    SimulatedModbusAdapter.reset()
    return PLCConnectionManager(simulation=True, reconnect_delay=0)


def test_sim_write_read(sim_manager):
    """Writes to holding registers/coils are read back."""
    plc = fake_modbus_plc()
    sim_manager.write(plc, parse_modbus_address("HR10:REAL"), 12.5)
    sim_manager.write(plc, parse_modbus_address("C3"), True)
    res = sim_manager.read(plc, {"r": parse_modbus_address("HR10:REAL"), "c": parse_modbus_address("C3")})
    assert res["r"].value == 12.5 and res["c"].value is True


def test_sim_register_bit_write_preserves_others(sim_manager):
    """Writing HRx.y changes only that bit."""
    plc = fake_modbus_plc()
    sim_manager.write(plc, parse_modbus_address("HR5"), 0b1000_0000_0000_0001)
    sim_manager.write(plc, parse_modbus_address("HR5.4"), True)
    assert sim_manager.read_one(plc, parse_modbus_address("HR5")).value == 0b1000_0000_0001_0001


def test_sim_read_only_areas(sim_manager):
    """Input registers and discrete inputs cannot be written."""
    plc = fake_modbus_plc()
    for text in ("IR1", "DI1"):
        with pytest.raises(ValueError):
            sim_manager.write(plc, parse_modbus_address(text), 1)


def test_sim_offline_and_test_connection(sim_manager):
    """Offline devices give errors; test_connection reports the library."""
    off = fake_modbus_plc(host="10.1.1.254")
    assert not sim_manager.read_one(off, parse_modbus_address("HR0")).ok
    info = sim_manager.test_connection(fake_modbus_plc())
    assert info["ok"] and "Modbus TCP" in info["module"]


def test_sim_animation(sim_manager):
    """Unwritten simulated tags change over time (demo values)."""
    plc = fake_modbus_plc()
    v = sim_manager.read_one(plc, parse_modbus_address("IR7:REAL")).value
    assert 9 <= v <= 91


def test_adapter_selection():
    """The library setting selects the adapter class."""
    tcp = ModbusConnection.for_plc(fake_modbus_plc(modbus_library="pymodbustcp"))
    assert isinstance(tcp.adapter, PyModbusTCPAdapter)
    assert isinstance(ModbusConnection.for_plc(fake_modbus_plc()).adapter, PyModbusAdapter)
    rtu = ModbusConnection.for_plc(fake_modbus_plc(protocol="modbus_rtu", modbus_library="pymodbustcp", serial_port="COM3"))
    assert isinstance(rtu.adapter, PyModbusAdapter)  # pyModbusTCP cannot do RTU
    assert "COM3" in rtu.settings.description


# ---------------------------------------------------------------------------
# Real clients against a pymodbus TCP server
# ---------------------------------------------------------------------------
def _free_port() -> int:
    """Return a free local TCP port.

    Returns:
        Port number.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def modbus_server():
    """Run a pymodbus TCP server (unit 1) in a background thread.

    Yields:
        Tuple ``(port, device_context)``.
    """
    pytest.importorskip("pymodbus")
    from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
    from pymodbus.server import ModbusTcpServer

    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    ir = [0] * 200
    ir[0:2] = struct.unpack(">HH", struct.pack(">f", 21.5))
    ir[4] = (-42) & 0xFFFF
    device = ModbusDeviceContext(
        co=ModbusSequentialDataBlock(1, [False] * 100), di=ModbusSequentialDataBlock(1, [True, False, True] + [False] * 97),
        hr=ModbusSequentialDataBlock(1, [0] * 200), ir=ModbusSequentialDataBlock(1, ir))
    context = ModbusServerContext(devices={1: device}, single=False)
    port = _free_port()
    loop = asyncio.new_event_loop()
    holder = {}

    def run():
        """Event loop thread running the server."""
        asyncio.set_event_loop(loop)

        async def main():
            """Create and serve the Modbus server."""
            holder["server"] = ModbusTcpServer(context, address=("127.0.0.1", port))
            await holder["server"].serve_forever()

        try:
            loop.run_until_complete(main())
        except Exception:  # pragma: no cover
            pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield port, device
    asyncio.run_coroutine_threadsafe(holder["server"].shutdown(), loop).result(5)


@pytest.mark.parametrize("library", ["pymodbus", "pymodbustcp"])
def test_real_read_write(modbus_server, library):
    """Both client libraries read and write a real Modbus TCP server."""
    port, device = modbus_server
    mgr = PLCConnectionManager(simulation=False, reconnect_delay=0)
    plc = fake_modbus_plc(plc_id=100 + len(library), host="127.0.0.1", tcp_port=port, modbus_library=library)
    tags = {t: parse_modbus_address(t) for t in ("IR0:REAL", "IR4:INT", "DI0", "DI1", "DI2")}
    res = mgr.read(plc, tags)
    assert res["IR0:REAL"].value == 21.5 and res["IR4:INT"].value == -42
    assert [res[k].value for k in ("DI0", "DI1", "DI2")] == [True, False, True]

    mgr.write(plc, parse_modbus_address("HR10:REAL"), 3.25)
    mgr.write(plc, parse_modbus_address("HR20"), 1234)
    mgr.write(plc, parse_modbus_address("C7"), True)
    mgr.write(plc, parse_modbus_address("HR30.2"), True)
    mgr.write(plc, parse_modbus_address("HR40:STRING[6]"), "PUMP1")
    back = mgr.read(plc, {k: parse_modbus_address(k) for k in ("HR10:REAL", "HR20", "C7", "HR30", "HR40:STRING[6]")})
    assert back["HR10:REAL"].value == 3.25 and back["HR20"].value == 1234 and back["C7"].value is True
    assert back["HR30"].value == 0b100 and back["HR40:STRING[6]"].value == "PUMP1"
    assert mgr.status(plc)["connected"]
    info = mgr.test_connection(plc)
    assert info["ok"] and library.replace("pymodbustcp", "pyModbusTCP") in info["module"]
    mgr.disconnect_all()


def test_real_word_swapped(modbus_server):
    """CDAB devices: the configured word order is used when reading."""
    port, device = modbus_server
    mgr = PLCConnectionManager(simulation=False, reconnect_delay=0)
    plc = fake_modbus_plc(plc_id=120, host="127.0.0.1", tcp_port=port, word_order="little")
    mgr.write(plc, parse_modbus_address("HR60:REAL"), 1.0)
    raw = mgr.read_one(plc, parse_modbus_address("HR60:UINT")).value, mgr.read_one(plc, parse_modbus_address("HR61:UINT")).value
    assert raw == (0x0000, 0x3F80)
    assert mgr.read_one(plc, parse_modbus_address("HR60:REAL")).value == 1.0
    mgr.disconnect_all()


@pytest.mark.parametrize("library", ["pymodbus", "pymodbustcp"])
def test_real_bad_address_isolated(modbus_server, library):
    """An illegal address fails only its own tag; the connection stays up."""
    port, _ = modbus_server
    mgr = PLCConnectionManager(simulation=False, reconnect_delay=0)
    plc = fake_modbus_plc(plc_id=130 + len(library), host="127.0.0.1", tcp_port=port, modbus_library=library)
    res = mgr.read(plc, {"bad": parse_modbus_address("HR5000"), "ok": parse_modbus_address("IR4:INT")})
    assert not res["bad"].ok and "exception" in res["bad"].error.lower()
    assert res["ok"].value == -42 and mgr.status(plc)["connected"]
    with pytest.raises(PLCError):
        mgr.write(plc, parse_modbus_address("HR5000"), 1)
    mgr.disconnect_all()


@pytest.mark.parametrize("library", ["pymodbus", "pymodbustcp"])
def test_real_unreachable(library):
    """A closed port gives a clean error for both libraries."""
    mgr = PLCConnectionManager(simulation=False, reconnect_delay=0)
    plc = fake_modbus_plc(plc_id=140, host="127.0.0.1", tcp_port=_free_port(), modbus_library=library, timeout=0.5)
    assert not mgr.read_one(plc, parse_modbus_address("HR0")).ok
    assert mgr.test_connection(plc)["ok"] is False


# ---------------------------------------------------------------------------
# Web forms / API
# ---------------------------------------------------------------------------
def plc_form(**extra):
    """POST data for a Modbus TCP device.

    Args:
        **extra: Overrides.

    Returns:
        Form data dict.
    """
    data = {"name": "Meter", "protocol": "modbus_tcp", "ip_address": "10.0.0.50", "tcp_port": 502,
            "modbus_library": "pymodbustcp", "unit_id": 3, "data_order": "CDAB", "timeout": 2,
            "baudrate": 9600, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": "y"}
    data.update(extra)
    return data


def test_create_modbus_tcp_plc(client, login_as):
    """Admins add a Modbus TCP device with its settings."""
    login_as("admin")
    assert client.post("/admin/plcs/new", data=plc_form()).status_code == 302
    plc = PLC.query.filter_by(name="Meter").one()
    assert (plc.protocol, plc.modbus_library, plc.unit_id, plc.byte_order, plc.word_order) == \
        ("modbus_tcp", "pymodbustcp", 3, "big", "little")
    page = client.get("/admin/plcs").data.decode()
    assert "Modbus TCP (pyModbusTCP)" in page and "unit 3 · CDAB" in page
    edit = client.get(f"/admin/plcs/{plc.id}/edit").data.decode()
    assert '<option selected value="CDAB">' in edit


def test_create_modbus_rtu_plc(client, login_as):
    """RTU needs a serial port but no IP address."""
    login_as("admin")
    resp = client.post("/admin/plcs/new", data=plc_form(name="Drive", protocol="modbus_rtu", ip_address="", serial_port=""))
    assert b"serial port" in resp.data
    client.post("/admin/plcs/new", data=plc_form(name="Drive", protocol="modbus_rtu", ip_address="", serial_port="COM4",
                                                 baudrate=19200, parity="E"))
    plc = PLC.query.filter_by(name="Drive").one()
    assert plc.endpoint == "COM4 19200 8E1" and plc.is_modbus


def test_s7_still_requires_ip(client, login_as):
    """S7 PLCs still need an IP address."""
    login_as("admin")
    resp = client.post("/admin/plcs/new", data={"name": "S7", "protocol": "s7", "ip_address": "", "tcp_port": 102,
                                                 "rack": 0, "slot": 1})
    assert b"Enter the IP address" in resp.data


def test_modbus_datapoint_and_test_read(client, login_as, db, dashboards):
    """Tags on a Modbus device use Modbus syntax; writable read-only areas are refused."""
    login_as("admin")
    client.post("/admin/plcs/new", data=plc_form(name="Meter2", modbus_library="pymodbus"))
    mb = PLC.query.filter_by(name="Meter2").one()
    url = f"/screens/{dashboards['private'].id}/points/new"
    base = {"label": "Power", "plc_id": mb.id, "data_type": "INT", "string_length": 20, "widget": "value",
            "scale": 1, "offset": 0, "decimals": 1, "true_text": "ON", "false_text": "OFF", "width": 1, "position": 0}
    assert b"not a valid Modbus address" in client.post(url, data={**base, "address": "DB1.DBD0"}).data
    assert b"cannot be written" in client.post(url, data={**base, "address": "IR3", "writable": "y"}).data
    assert client.post(url, data={**base, "address": "HR100:REAL", "writable": "y"}).status_code == 302
    dp = DataPoint.query.filter_by(label="Power").one()
    assert (dp.area, dp.start, dp.data_type, dp.address) == ("HR", 100, "REAL", "HR100")
    # end-to-end through the simulator: write and read back via the API
    r = client.post(f"/api/datapoints/{dp.id}/write", json={"value": 42.5}).get_json()
    assert r["value"] == 42.5
    t = client.post(f"/api/plcs/{mb.id}/read", json={"address": "HR100:REAL"}).get_json()
    assert t["value"] == 42.5 and t["address"] == "HR100:REAL"
    assert client.post(f"/api/plcs/{mb.id}/read", json={"address": "M0.0"}).status_code == 400


def test_point_form_offers_protocol_map(client, login_as, dashboards):
    """The tag form knows each PLC's protocol for the address help."""
    login_as("admin")
    html = client.get(f"/screens/{dashboards['private'].id}/points/new").data.decode()
    assert 'id="plc-protocols"' in html and "Modbus (0-based protocol addresses)" in html
