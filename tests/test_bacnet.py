"""Tests for BACnet/IP support: addressing, value conversion, simulator, a real
BAC0 device on the loopback interface, and the web forms / pages."""

import asyncio
import socket
from types import SimpleNamespace

import pytest

from app.models import PLC, DataPoint
from app.plc.addressing import AddressError
from app.plc.bacnet import (
    BACnetAddress, BACnetAddressError, BACnetNetwork, SimulatedBACnetAdapter, convert_value, parse_bacnet_address,
    wire_value,
)
from app.plc.driver import PLCConnectionManager, PLCError
from app.plc.protocols import parse_tag_address


# ---------------------------------------------------------------------------
# Addressing and conversion
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,area,inst,prop,prio,dtype", [
    ("AV3", "AV", 3, "presentValue", 0, "REAL"),
    ("av3", "AV", 3, "presentValue", 0, "REAL"),
    ("analogValue:3", "AV", 3, "presentValue", 0, "REAL"),
    ("analog-value,3", "AV", 3, "presentValue", 0, "REAL"),
    ("BO12@10", "BO", 12, "presentValue", 10, "BOOL"),
    ("MSV2", "MV", 2, "presentValue", 0, "UINT"),
    ("multiStateInput:7", "MI", 7, "presentValue", 0, "UINT"),
    ("AI1.units", "AI", 1, "units", 0, "STRING"),
    ("AV5.outOfService:BOOL", "AV", 5, "outOfService", 0, "BOOL"),
    ("MV2:INT", "MV", 2, "presentValue", 0, "INT"),
    ("CS4", "CS", 4, "presentValue", 0, "STRING"),
])
def test_parse_bacnet(text, area, inst, prop, prio, dtype):
    """Short and long object names, properties, priorities and types."""
    a = parse_bacnet_address(text, "INT")  # drop-down type is ignored
    assert (a.area, a.start, a.property_name, a.priority, a.data_type) == (area, inst, prop, prio, dtype)


@pytest.mark.parametrize("text", ["", "XX1", "AV", "AV-1", "AV4194303", "AV1@17", "AV1:TIME", "AV1.bad-prop"])
def test_parse_bacnet_invalid(text):
    """Invalid BACnet addresses raise BACnetAddressError."""
    with pytest.raises(BACnetAddressError):
        parse_bacnet_address(text)


@pytest.mark.parametrize("text", ["AV3", "BO12@10", "AI1.units", "MV2:INT", "AV5.outOfService:BOOL"])
def test_format_roundtrip(text):
    """format() keeps non-default types so stored addresses re-parse identically."""
    a = parse_bacnet_address(text)
    assert a.format() == text and parse_bacnet_address(a.format()) == a


def test_writable_rules():
    """Only outputs/values and only presentValue are writable."""
    assert parse_bacnet_address("AV1").writable_area
    assert not parse_bacnet_address("AI1").writable_area
    assert not parse_bacnet_address("AV1.units").writable_area


def test_protocol_dispatch():
    """The BACnet syntax is used for bacnet_ip PLCs only."""
    assert parse_tag_address("AV1", protocol="bacnet_ip").area == "AV"
    with pytest.raises(AddressError):
        parse_tag_address("HR1", protocol="bacnet_ip")


@pytest.mark.parametrize("raw,dtype,expected", [
    ("active", "BOOL", True), ("inactive", "BOOL", False), (21.5, "REAL", 21.5), (2, "UINT", 2),
    ("degrees-celsius", "STRING", "degrees-celsius"), (3.7, "INT", 3),
])
def test_convert_value(raw, dtype, expected):
    """bacpypes values are converted to the tag type."""
    assert convert_value(raw, dtype) == expected


def test_convert_missing_and_wire_values():
    """Missing properties raise; write values are formatted for BAC0."""
    from app.plc.bacnet import BACnetDeviceError

    with pytest.raises(BACnetDeviceError):
        convert_value(None, "REAL")
    assert wire_value(True, BACnetAddress("BV", 1, data_type="BOOL")) == "active"
    assert wire_value(0, BACnetAddress("BV", 1, data_type="BOOL")) == "inactive"
    assert wire_value(2.5, BACnetAddress("AV", 1, data_type="REAL")) == "2.5"
    assert wire_value(3.6, BACnetAddress("MV", 1, data_type="UINT")) == "4"
    with pytest.raises(ValueError):
        wire_value(-1, BACnetAddress("MV", 1, data_type="UINT"))
    with pytest.raises(ValueError):
        wire_value("two words", BACnetAddress("CS", 1, data_type="STRING"))


# ---------------------------------------------------------------------------
# Simulator through the connection manager
# ---------------------------------------------------------------------------
def fake_bacnet_plc(plc_id=70, host="10.2.2.2", port=47808, device_id=1001, **kw):
    """Minimal BACnet PLC-like object.

    Args:
        plc_id: Id.
        host: Device IP.
        port: Device UDP port.
        device_id: Device instance.
        **kw: Overrides.

    Returns:
        SimpleNamespace with the attributes the manager uses.
    """
    attrs = dict(id=plc_id, name="BAC", protocol="bacnet_ip", ip_address=host, tcp_port=port, bacnet_device_id=device_id,
                 bacnet_priority=8, timeout=2.0, enabled=True)
    attrs.update(kw)
    ns = SimpleNamespace(**attrs)
    ns.connection_signature = tuple(sorted(attrs.items()))
    return ns


@pytest.fixture()
def sim_manager():
    """A simulation-mode manager with fresh simulated BACnet devices.

    Returns:
        PLCConnectionManager.
    """
    SimulatedBACnetAdapter.reset()
    return PLCConnectionManager(simulation=True, reconnect_delay=0)


def test_sim_read_write(sim_manager):
    """Simulated objects animate and keep written values."""
    plc = fake_bacnet_plc()
    tags = {t: parse_bacnet_address(t) for t in ("AI1", "BV2", "MV3", "AI1.units")}
    res = sim_manager.read(plc, tags)
    assert 9 <= res["AI1"].value <= 91 and isinstance(res["BV2"].value, bool) and res["MV3"].value in (1, 2, 3)
    assert res["AI1.units"].value == "degrees-celsius"
    sim_manager.write(plc, parse_bacnet_address("AV5"), 42.5)
    sim_manager.write(plc, parse_bacnet_address("BO1"), True)
    back = sim_manager.read(plc, {"a": parse_bacnet_address("AV5"), "b": parse_bacnet_address("BO1")})
    assert back["a"].value == 42.5 and back["b"].value is True


def test_sim_read_only_and_offline(sim_manager):
    """Inputs are read only; offline devices give errors."""
    with pytest.raises(ValueError):
        sim_manager.write(fake_bacnet_plc(), parse_bacnet_address("AI1"), 1)
    off = fake_bacnet_plc(plc_id=71, host="10.2.2.254")
    assert not sim_manager.read_one(off, parse_bacnet_address("AV1")).ok
    assert sim_manager.test_connection(off)["ok"] is False
    info = sim_manager.test_connection(fake_bacnet_plc())
    assert info["ok"] and "BACnet/IP" in info["module"]


def test_sim_browse_and_whois(sim_manager):
    """Object browsing and Who-Is work in simulation."""
    rows = sim_manager.bacnet_browse(fake_bacnet_plc())
    assert {"AI1", "AV1", "BO1", "MV1"} <= {r["address"] for r in rows}
    assert sim_manager.bacnet_who_is()[0]["device_id"] == 1001


# ---------------------------------------------------------------------------
# Real BAC0 device on the loopback interface
# ---------------------------------------------------------------------------
def _free_udp_port() -> int:
    """Return a free local UDP port.

    Returns:
        Port number.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def bacnet_device():
    """Start the HMI's BACnet stack and a simulated BAC0 device (instance 1001) on 127.0.0.1.

    Yields:
        Tuple ``(device_port, local_settings)``.
    """
    BAC0 = pytest.importorskip("BAC0")
    from BAC0.core.devices.local.factory import (
        ObjectFactory, analog_input, analog_value, binary_value, character_string, multistate_value,
    )

    BACnetNetwork.shutdown()
    local = {"ip": "127.0.0.1/8", "port": _free_udp_port(), "device_id": 3999}
    try:
        net = BACnetNetwork.get(local["ip"], local["port"], local["device_id"])
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"BACnet stack unavailable: {exc}")
    dev_port = _free_udp_port()
    holder = {}

    async def make_device():
        """Create the remote device with a few objects (inside the BACnet loop)."""
        dev = BAC0.lite(ip="127.0.0.1/8", port=dev_port, deviceId=1001, localObjName="TestAHU", ping=False)
        await asyncio.sleep(0.5)
        ObjectFactory.clear_objects()
        analog_value(instance=1, name="Setpoint", presentValue=21.5, is_commandable=True)
        binary_value(instance=2, name="Fan", presentValue=False, is_commandable=True)
        multistate_value(instance=3, name="Mode", presentValue=2, is_commandable=True)
        character_string(instance=5, name="Label", presentValue="hello")
        analog_input(instance=4, name="SupplyTemp", presentValue=17.25).add_objects_to_application(dev)
        holder["dev"] = dev

    net.run(make_device(), 15)
    yield dev_port, local

    async def stop_device():
        """Disconnect the remote device."""
        result = holder["dev"].disconnect()
        if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
            await result

    try:
        net.run(stop_device(), 10)
    except Exception:  # pragma: no cover
        pass
    BACnetNetwork.shutdown()


def real_manager(local):
    """A non-simulated manager using the test's local BACnet settings.

    Args:
        local: Local stack settings.

    Returns:
        PLCConnectionManager.
    """
    return PLCConnectionManager(simulation=False, reconnect_delay=0, bacnet_local=local)


def test_real_read(bacnet_device):
    """Several objects are read in one go (ReadPropertyMultiple)."""
    port, local = bacnet_device
    mgr = real_manager(local)
    plc = fake_bacnet_plc(plc_id=80, host="127.0.0.1", port=port)
    tags = {t: parse_bacnet_address(t) for t in ("AV1", "BV2", "MV3", "AI4", "CS5")}
    res = mgr.read(plc, tags)
    assert res["AV1"].value == 21.5 and res["BV2"].value is False and res["MV3"].value == 2
    assert res["AI4"].value == 17.25 and res["CS5"].value == "hello"
    assert mgr.status(plc)["connected"]


def test_real_write_with_priority(bacnet_device):
    """Writes reach the device (default priority and @priority)."""
    port, local = bacnet_device
    mgr = real_manager(local)
    plc = fake_bacnet_plc(plc_id=81, host="127.0.0.1", port=port)
    mgr.write(plc, parse_bacnet_address("AV1"), 23.75)
    mgr.write(plc, parse_bacnet_address("BV2@10"), True)
    mgr.write(plc, parse_bacnet_address("MV3"), 3)
    res = mgr.read(plc, {t: parse_bacnet_address(t) for t in ("AV1", "BV2", "MV3")})
    assert (res["AV1"].value, res["BV2"].value, res["MV3"].value) == (23.75, True, 3)


def test_real_errors_isolated(bacnet_device):
    """Unknown objects fail alone; read-only writes are rejected by the device."""
    port, local = bacnet_device
    mgr = real_manager(local)
    plc = fake_bacnet_plc(plc_id=82, host="127.0.0.1", port=port)
    res = mgr.read(plc, {"bad": parse_bacnet_address("AV99"), "ok": parse_bacnet_address("AV1")})
    assert not res["bad"].ok and res["ok"].ok
    with pytest.raises(PLCError):
        mgr.write(plc, parse_bacnet_address("AV99"), 1)


def test_real_identify_browse_whois(bacnet_device):
    """Test connection, object browsing and a directed Who-Is."""
    port, local = bacnet_device
    mgr = real_manager(local)
    plc = fake_bacnet_plc(plc_id=83, host="127.0.0.1", port=port)
    info = mgr.test_connection(plc)
    assert info["ok"] and info["name"] == "TestAHU"
    rows = {r["address"]: r for r in mgr.bacnet_browse(plc)}
    assert rows["AV1"]["name"] == "Setpoint" and rows["AI4"]["value"] == "17.25"
    found = mgr.bacnet_who_is(f"127.0.0.1:{port}")
    assert any(d["device_id"] == 1001 for d in found)


def test_real_no_device(bacnet_device):
    """A port without a device yields clean errors."""
    _, local = bacnet_device
    mgr = real_manager(local)
    plc = fake_bacnet_plc(plc_id=84, host="127.0.0.1", port=_free_udp_port(), timeout=1)
    assert not mgr.read_one(plc, parse_bacnet_address("AV1")).ok
    assert mgr.test_connection(plc)["ok"] is False


# ---------------------------------------------------------------------------
# Web forms / pages
# ---------------------------------------------------------------------------
def bacnet_form(**extra):
    """POST data for a BACnet device.

    Args:
        **extra: Overrides.

    Returns:
        Form data dict.
    """
    data = {"name": "AHU-1", "protocol": "bacnet_ip", "ip_address": "192.168.1.40", "tcp_port": 47808,
            "bacnet_device_id": 1001, "bacnet_priority": 10, "timeout": 3, "modbus_library": "pymodbus",
            "data_order": "ABCD", "baudrate": 9600, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": "y"}
    data.update(extra)
    return data


def test_create_bacnet_plc(client, login_as):
    """Admins add a BACnet device; the device instance is required."""
    login_as("admin")
    assert b"device instance" in client.post("/admin/plcs/new", data=bacnet_form(bacnet_device_id="")).data
    assert client.post("/admin/plcs/new", data=bacnet_form()).status_code == 302
    plc = PLC.query.filter_by(name="AHU-1").one()
    assert (plc.protocol, plc.bacnet_device_id, plc.bacnet_priority) == ("bacnet_ip", 1001, 10)
    page = client.get("/admin/plcs").data.decode()
    assert "BACnet/IP (BAC0)" in page and "dev 1001" in page and "Browse objects" in page


def test_browse_page_and_whois_api(client, login_as):
    """The object browser and Who-Is endpoint work (simulation)."""
    login_as("admin")
    client.post("/admin/plcs/new", data=bacnet_form())
    plc = PLC.query.filter_by(name="AHU-1").one()
    html = client.get(f"/admin/plcs/{plc.id}/browse").data.decode()
    assert "BACnet objects" in html and 'data-addr="AV1"' in html
    assert client.post("/api/bacnet/whois", json={}).get_json()["devices"][0]["device_id"] == 1001


def test_browse_requires_bacnet_and_permission(client, login_as, plc):
    """Browsing is for BACnet devices and PLC managers only."""
    login_as("admin")
    assert client.get(f"/admin/plcs/{plc.id}/browse").status_code == 302  # S7 PLC
    client.post("/auth/logout")
    login_as("viewer")
    assert client.get(f"/admin/plcs/{plc.id}/browse").status_code == 403
    assert client.post("/api/bacnet/whois", json={}).status_code == 403


def test_bacnet_datapoint_end_to_end(client, login_as, dashboards):
    """BACnet tags: syntax check, writable check, write + read through the API."""
    login_as("admin")
    client.post("/admin/plcs/new", data=bacnet_form())
    dev = PLC.query.filter_by(name="AHU-1").one()
    url = f"/screens/{dashboards['private'].id}/points/new"
    base = {"label": "Room SP", "plc_id": dev.id, "data_type": "INT", "string_length": 20, "widget": "value",
            "scale": 1, "offset": 0, "decimals": 1, "true_text": "ON", "false_text": "OFF", "width": 1, "position": 0}
    assert b"Unknown BACnet object type" in client.post(url, data={**base, "address": "HR1"}).data
    assert b"cannot be written" in client.post(url, data={**base, "address": "AI1", "writable": "y"}).data
    assert client.post(url, data={**base, "address": "AV7@12", "writable": "y"}).status_code == 302
    dp = DataPoint.query.filter_by(label="Room SP").one()
    assert (dp.area, dp.start, dp.priority, dp.data_type, dp.address) == ("AV", 7, 12, "REAL", "AV7@12")
    r = client.post(f"/api/datapoints/{dp.id}/write", json={"value": 22.5}).get_json()
    assert r["value"] == 22.5
    t = client.post(f"/api/plcs/{dev.id}/read", json={"address": "AI1.units"}).get_json()
    assert t["value"] == "degrees-celsius"
    edit = client.get(f"/screens/{dp.screens[0].id}/points/{dp.id}/edit").data.decode()
    assert 'value="AV7@12"' in edit and "BACnet (object + instance)" in edit
