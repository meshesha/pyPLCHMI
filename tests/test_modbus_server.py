"""Modbus TCP server: encoding, addresses, the server itself (with a real Modbus client) and the admin page."""

import socket
import struct
from types import SimpleNamespace

import pytest
from pyModbusTCP.client import ModbusClient

from app.modbus_server.config import ServerConfig
from app.modbus_server.encoding import default_type, encode, notation, register_count
from app.modbus_server.layout import check, check_config, next_free
from app.modbus_server.server import ModbusPublisher, build_image, port_error
from app.models import DataPoint, ModbusExport
from app.plc.driver import get_manager
from tests.conftest import add_point


def free_port() -> int:
    """A TCP port that is free right now."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------
def test_encode_types_and_orders():
    assert encode(21.5, "FLOAT32") == [0x41AC, 0x0000]
    assert encode(21.5, "FLOAT32", "CDAB") == [0x0000, 0x41AC]
    assert encode(21.5, "FLOAT32", "BADC") == [0xAC41, 0x0000]
    assert encode(21.5, "FLOAT32", "DCBA") == [0x0000, 0xAC41]
    assert encode(-2, "INT16") == [0xFFFE] and encode(70000, "INT16") == [0x7FFF]       # clamped
    assert encode(-5, "UINT16") == [0] and encode(21.54, "INT16", scale=10) == [215]
    assert encode(70000, "INT32") == [0x0001, 0x1170] and encode(70000, "UINT32", "CDAB") == [0x1170, 0x0001]
    assert struct.unpack(">d", b"".join(w.to_bytes(2, "big") for w in encode(1.5, "FLOAT64")))[0] == 1.5
    assert encode(True, "BIT") is True and encode(0, "BIT") is False and encode(True, "BOOL16") == [1]
    assert encode("ABC", "STRING", string_length=4) == [0x4142, 0x4300]
    assert encode("ABC", "STRING", "BADC", string_length=4) == [0x4241, 0x0043]
    assert register_count("STRING", 5) == 3 and register_count("FLOAT64") == 4
    with pytest.raises(ValueError):
        encode("abc", "INT16")
    assert notation("reg", 0) == "40001" and notation("reg", 0, True) == "30001"
    assert notation("bit", 9) == "00010" and notation("reg", 12345) == "412346"


def test_default_types():
    def dp(data_type, scale=1.0, offset=0.0, length=0):
        return SimpleNamespace(data_type=data_type, scale=scale, offset=offset, string_length=length)
    assert default_type(dp("BOOL")) == ("BIT", 0)
    assert default_type(dp("INT")) == ("INT16", 0) and default_type(dp("WORD")) == ("UINT16", 0)
    assert default_type(dp("DINT")) == ("INT32", 0) and default_type(dp("UDINT")) == ("UINT32", 0)
    assert default_type(dp("REAL")) == ("FLOAT32", 0) and default_type(dp("INT", scale=0.1)) == ("FLOAT32", 0)
    assert default_type(dp("STRING", length=10)) == ("STRING", 10)


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
def row(id_, area, address, mb_type, label="x", length=0):
    """A published tag stand-in."""
    return SimpleNamespace(id=id_, area=area, address=address, mb_type=mb_type, string_length=length,
                           datapoint=SimpleNamespace(label=label))


def test_layout_next_free_and_checks():
    assert next_free("reg", 2, {0, 1, 3}) == 4 and next_free("reg", 1, {0, 1, 3}) == 2
    assert next_free("reg", 2, set(range(0, 65535))) is None
    exports = [row(1, "reg", 0, "FLOAT32", "A"), row(2, "bit", 0, "BIT", "B")]
    cfg = ServerConfig(heartbeat=10, quality_base=100)
    assert check(row(None, "reg", 1, "INT16"), exports, cfg) == ["Overlaps A (registers 0–1)."]
    assert check(row(None, "reg", 2, "INT16"), exports, cfg) == []
    assert "heartbeat" in check(row(None, "reg", 9, "FLOAT32"), exports, cfg)[0]
    assert "quality" in check(row(None, "bit", 101, "BIT"), exports, cfg)[0]
    assert "65535" in check(row(None, "reg", 65535, "FLOAT32"), exports, cfg)[0]
    assert set(check_config(ServerConfig(heartbeat=1, quality_base=0), exports)) == {"heartbeat", "quality_base"}


def test_config_allow_list_and_validation():
    cfg = ServerConfig(allowed="10.0.0.5, 192.168.1.0/24\n::1")
    assert cfg.client_allowed("10.0.0.5") and cfg.client_allowed("192.168.1.77") and cfg.client_allowed("::1")
    assert cfg.client_allowed("::ffff:10.0.0.5") and not cfg.client_allowed("10.0.0.6")
    assert ServerConfig().client_allowed("1.2.3.4")
    bad = ServerConfig(host="x", port=0, unit_id=300, order="X", interval=0, bad_value="?", allowed="1.2.3.999",
                       heartbeat=70000)
    assert set(bad.validate()) == {"host", "port", "unit_id", "order", "interval", "bad_value", "allowed", "heartbeat"}
    assert "5020" in port_error(PermissionError(13, "Permission denied"), 502)


def test_build_image_hold_zero_quality_heartbeat():
    exports = [SimpleNamespace(id=1, datapoint_id=11, area="reg", address=0, mb_type="FLOAT32", scale=1.0,
                               string_length=0),
               SimpleNamespace(id=2, datapoint_id=12, area="bit", address=0, mb_type="BIT", scale=1.0,
                               string_length=0)]
    cfg = ServerConfig(heartbeat=100, quality_base=10)
    held = {}
    image = build_image(cfg, exports, {11: {"value": 21.5, "quality": "good"}, 12: {"value": True,
                                                                                   "quality": "good"}}, held, 7)
    assert image.regs == {0: 0x41AC, 1: 0, 100: 7} and image.bits == {0: True, 10: True, 11: True}
    bad = {11: {"value": None, "quality": "bad"}, 12: {"value": None, "quality": "bad"}}
    image = build_image(cfg, exports, bad, held, 8)
    assert image.regs[0] == 0x41AC and image.bits[0] is True                # last good value kept
    assert image.bits[10] is False and image.bits[11] is False and image.bad == 2
    image = build_image(ServerConfig(bad_value="zero"), exports, bad, held)
    assert image.regs[0] == 0 and image.bits[0] is False


# ---------------------------------------------------------------------------
# The server, read by a real Modbus TCP client
# ---------------------------------------------------------------------------
@pytest.fixture()
def publisher():
    """A publisher (stopped after the test)."""
    pub = ModbusPublisher()
    yield pub
    pub.stop_server()


def publish(db, plc, port, **cfg):
    """Tags with values, published on ``port``."""
    temp = add_point(None, plc, "Temp", "DB1.DBD0:REAL")
    count = add_point(None, plc, "Count", "DB1.DBW4:INT")
    run = add_point(None, plc, "Run", "M0.0")
    manager = get_manager()
    manager.write(plc, temp.tag_address, 21.5)
    manager.write(plc, count.tag_address, -3)
    manager.write(plc, run.tag_address, True)
    db.session.add_all([ModbusExport(datapoint=temp, area="reg", address=0, mb_type="FLOAT32"),
                        ModbusExport(datapoint=count, area="reg", address=2, mb_type="INT16"),
                        ModbusExport(datapoint=run, area="bit", address=5, mb_type="BIT")])
    ServerConfig(enabled=True, host="127.0.0.1", port=port, **cfg).save()
    db.session.commit()
    return temp, count, run


def test_server_serves_values_read_only(db, plc, publisher):
    port = free_port()
    publish(db, plc, port, heartbeat=50)
    publisher.cycle()
    assert publisher.running, publisher.error
    client = ModbusClient(host="127.0.0.1", port=port, unit_id=1, timeout=2)
    assert client.read_holding_registers(0, 4) == [0x41AC, 0, 0xFFFD, 0]          # 21.5, -3, gap = 0
    assert client.read_input_registers(0, 3) == [0x41AC, 0, 0xFFFD]               # same data (function 4)
    assert client.read_coils(4, 3) == [False, True, False]
    assert client.read_discrete_inputs(5, 1) == [True]
    assert client.read_holding_registers(50, 1) == [1]                             # heartbeat
    assert client.write_single_register(0, 1) is False and client.last_except == 2    # not writable
    assert client.write_multiple_coils(5, [False]) is False and client.last_except == 2
    publisher.cycle()
    assert client.read_holding_registers(50, 1) == [2]
    status = publisher.status()
    assert status["state"] == "running" and status["writes_refused"] == 2 and status["good"] == 3
    assert status["clients"][0]["address"] == "127.0.0.1"
    client.close()


def test_server_unit_id_and_allow_list(db, plc, publisher):
    port = free_port()
    publish(db, plc, port, unit_id=5, allowed="10.9.9.9")
    publisher.cycle()
    client = ModbusClient(host="127.0.0.1", port=port, unit_id=5, timeout=1, auto_close=True)
    assert client.read_holding_registers(0, 1) is None                       # 127.0.0.1 not allowed: closed
    assert publisher.status()["refused"] >= 1
    ServerConfig(enabled=True, host="127.0.0.1", port=port, unit_id=5, allowed="127.0.0.1").save()
    db.session.commit()
    publisher.cycle()
    assert client.read_holding_registers(0, 1) == [0x41AC]
    wrong = ModbusClient(host="127.0.0.1", port=port, unit_id=6, timeout=1, auto_close=True)
    assert wrong.read_holding_registers(0, 1) is None and wrong.last_except == 0x0B


def test_server_stops_when_disabled_and_reports_busy_port(db, plc, publisher):
    port = free_port()
    publish(db, plc, port)
    publisher.cycle()
    assert publisher.running
    other = ModbusPublisher()
    other.cycle()                                                           # same port: already used
    assert not other.running and "already used" in other.error and other.status()["state"] == "error"
    ServerConfig(enabled=False, host="127.0.0.1", port=port).save()
    db.session.commit()
    publisher.cycle()
    assert not publisher.running and publisher.status()["state"] == "disabled"


def test_bad_values_published_as_held(db, plc, publisher):
    port = free_port()
    publish(db, plc, port, quality_base=0)
    publisher.cycle()
    plc.enabled, plc.offline_delay = False, 0                                # every tag becomes bad at once
    db.session.commit()
    publisher.cycle()
    client = ModbusClient(host="127.0.0.1", port=port, timeout=2, auto_close=True)
    assert client.read_holding_registers(0, 2) == [0x41AC, 0]               # last good value
    assert client.read_coils(0, 3) == [False, False, False]                  # quality bits off ...
    assert client.read_coils(5, 1) == [True]                                 # ... value bit held


# ---------------------------------------------------------------------------
# Admin page
# ---------------------------------------------------------------------------
def test_page_permissions_and_settings(client, login_as, db):
    login_as("viewer")
    assert client.get("/admin/modbus-server/").status_code == 403
    client.post("/auth/logout")
    login_as("admin")
    page = client.get("/admin/modbus-server/").get_data(as_text=True)
    assert "Modbus TCP server" in page and "Add tags" in page
    resp = client.post("/admin/modbus-server/settings", data={
        "enabled": "y", "host": "0.0.0.0", "port": "5020", "unit_id": "0", "order": "CDAB", "interval": "0.5",
        "bad_value": "zero", "allowed": "10.0.0.0/24", "heartbeat": "1000", "quality_base": ""})
    assert resp.status_code == 302
    cfg = ServerConfig.load()
    assert (cfg.enabled, cfg.port, cfg.order, cfg.interval, cfg.bad_value, cfg.heartbeat, cfg.quality_base) == \
        (True, 5020, "CDAB", 0.5, "zero", 1000, None)
    resp = client.post("/admin/modbus-server/settings", data={
        "host": "my-pc", "port": "502", "order": "ABCD", "interval": "1", "bad_value": "hold", "allowed": "x"})
    assert resp.status_code == 400 and ServerConfig.load().port == 5020
    status = client.get("/admin/modbus-server/api/status").json
    assert status["status"]["state"] in ("disabled", "starting", "running", "error")


def test_add_update_remove_and_address_list(client, login_as, db, plc):
    login_as("admin")
    temp = add_point(None, plc, "Temp", "DB1.DBD0:REAL")
    count = add_point(None, plc, "Count", "DB1.DBW4:INT")
    run = add_point(None, plc, "Run", "M0.0")
    name = add_point(None, plc, "Name", "DB1.DBB20:STRING[10]")
    ServerConfig(heartbeat=2).save()
    db.session.commit()
    tags = client.get("/admin/modbus-server/api/tags").json
    assert {t["label"] for t in tags} == {"Temp", "Count", "Run", "Name"}
    data = client.post("/admin/modbus-server/api/add", json={"ids": [temp.id, count.id, run.id, name.id]}).json
    rows = {r["label"]: r for r in data["exports"]}
    assert rows["Count"]["address"] == 0 and rows["Count"]["mb_type"] == "INT16"
    assert rows["Name"]["mb_type"] == "STRING" and rows["Name"]["address"] == 3 and rows["Name"]["count"] == 5
    assert rows["Temp"]["address"] == 8 and rows["Temp"]["ref"] == "40009" and rows["Temp"]["ref_ro"] == "30009"
    assert rows["Run"]["area"] == "bit" and rows["Run"]["address"] == 0
    assert client.get("/admin/modbus-server/api/tags").json == []

    url = f"/admin/modbus-server/api/{rows['Temp']['id']}"
    resp = client.post(url, json={"address": 4})
    assert resp.status_code == 400 and "Overlaps Name" in resp.json["errors"][0]
    resp = client.post(url, json={"address": 1, "mb_type": "INT16", "scale": 10})
    assert resp.json["ok"] and resp.json["export"]["address"] == 1 and resp.json["export"]["scale"] == 10
    resp = client.post(url, json={"mb_type": "BIT", "address": ""})              # new area: next free bit
    assert resp.json["export"]["area"] == "bit" and resp.json["export"]["address"] == 1
    assert client.post(url, json={"mb_type": "STRING"}).status_code == 400
    assert client.post(f"/admin/modbus-server/api/{rows['Name']['id']}", json={"mb_type": "INT16"}).status_code == 400
    assert client.post(url, json={"scale": 0}).status_code == 400

    xlsx = client.get("/admin/modbus-server/addresses.xlsx")
    assert xlsx.status_code == 200 and xlsx.data[:2] == b"PK"
    csv_text = client.get("/admin/modbus-server/addresses.csv").get_data(as_text=True)
    assert "Count" in csv_text and "40001" in csv_text and "(heartbeat counter)" in csv_text

    resp = client.post("/admin/modbus-server/api/remove", json={"ids": [rows["Count"]["id"]]})
    assert resp.json["removed"] == 1 and ModbusExport.query.count() == 3


def test_deleting_a_tag_removes_its_export(db, plc):
    dp = add_point(None, plc, "Temp", "DB1.DBD0:REAL")
    db.session.add(ModbusExport(datapoint=dp, area="reg", address=0, mb_type="FLOAT32"))
    db.session.commit()
    db.session.delete(db.session.get(DataPoint, dp.id))
    db.session.commit()
    assert ModbusExport.query.count() == 0


# ---------------------------------------------------------------------------
# Writes from other SCADA systems
# ---------------------------------------------------------------------------
def allow_writes(db, *points):
    """Make tags writable in pyPLCHMI and allow Modbus writes for them."""
    for dp in points:
        dp.writable = True
        dp.modbus_export.writable = True
    db.session.commit()


def test_writes_allowed_per_tag(db, plc, publisher):
    from app.models import AuditLog  # noqa: PLC0415

    port = free_port()
    temp, count, run = publish(db, plc, port, order="CDAB")
    allow_writes(db, temp, run)
    publisher.cycle()
    client = ModbusClient(host="127.0.0.1", port=port, timeout=2)
    manager = get_manager()

    assert client.write_multiple_registers(0, encode(30.25, "FLOAT32", "CDAB")) is True
    assert manager.read_one(plc, temp.tag_address).value == pytest.approx(30.25)
    assert client.read_holding_registers(0, 2) == encode(30.25, "FLOAT32", "CDAB")   # image updated at once
    assert client.write_single_coil(5, False) is True
    assert manager.read_one(plc, run.tag_address).value is False

    # Half of a FLOAT32, a read-only tag, a request reaching a read-only tag, an unused address: 02.
    assert client.write_single_register(0, 1) is False and client.last_except == 2
    assert client.write_single_register(2, 7) is False and client.last_except == 2
    assert client.write_multiple_registers(0, [0, 0, 5]) is False and client.last_except == 2
    assert client.write_single_coil(9, True) is False and client.last_except == 2
    assert manager.read_one(plc, temp.tag_address).value == pytest.approx(30.25)       # nothing written
    # NaN cannot be written: 03.
    assert client.write_multiple_registers(0, encode(float("nan"), "FLOAT32", "CDAB")) is False
    assert client.last_except == 3

    status = publisher.status()
    assert status["writes_done"] == 2 and status["writes_refused"] == 5
    assert status["recent_writes"][0]["tag"] == "Run" and status["recent_writes"][0]["error"] is None
    users = {a.username for a in AuditLog.query.filter_by(action="write").all()}
    assert users == {"Modbus TCP 127.0.0.1"}
    client.close()


def test_write_needs_writable_tag_and_scale(db, plc, publisher):
    port = free_port()
    temp, count, run = publish(db, plc, port)
    exp = count.modbus_export
    exp.scale, exp.writable = 10, True        # tag itself still read only
    db.session.commit()
    publisher.cycle()
    client = ModbusClient(host="127.0.0.1", port=port, timeout=2)
    assert client.write_single_register(2, 55) is False and client.last_except == 2
    count.writable = True
    db.session.commit()
    publisher.cycle()
    assert client.write_single_register(2, 55) is True                         # 55 / 10 = 5.5 -> INT 6
    assert get_manager().read_one(plc, count.tag_address).value == 6
    assert client.write_single_register(2, (-25540) & 0xFFFF) is True          # INT16 -25540 / 10
    assert get_manager().read_one(plc, count.tag_address).value == -2554
    client.close()


def test_page_allows_write_only_for_writable_tags(client, login_as, db, plc):
    login_as("admin")
    temp = add_point(None, plc, "Temp", "DB1.DBD0:REAL")
    exp = ModbusExport(datapoint=temp, area="reg", address=0, mb_type="FLOAT32")
    db.session.add(exp)
    db.session.commit()
    url = f"/admin/modbus-server/api/{exp.id}"
    resp = client.post(url, json={"writable": True})
    assert resp.status_code == 400 and "read only" in resp.json["errors"][0]
    temp.writable = True
    db.session.commit()
    resp = client.post(url, json={"writable": True})
    assert resp.json["ok"] and resp.json["export"]["writable"] and resp.json["export"]["write_ok"]
    csv_text = client.get("/admin/modbus-server/addresses.csv").get_data(as_text=True)
    assert "Read / write" in csv_text
    assert client.post(url, json={"writable": False}).json["export"]["writable"] is False
