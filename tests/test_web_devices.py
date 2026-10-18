"""Web devices: HTTP (also header-less HTTP/0.9) and WebSocket, JSON / XML, the Add device wizard."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.models import PLC, Alarm, DataPoint
from app.plc.driver import get_manager
from app.plc.protocols import parse_tag_address
from app.plc.web import WebAddress, WebConnection, WebSettings, parse_web_address, probe
from app.plc.web_client import WebError, WebSocketClient, http_get, parse_url
from app.plc.web_data import (Missing, WebDataError, check_path, convert, flatten, parse_document, resolve,
                              suggest_type)
from app.admin.web_devices import path_to_name
from tests.web_servers import RawServer, WSServer, wait_for, ws_frame

DOC = {"meter": {"power": 12.5, "phases": [230.1, 229.8], "ok": True, "serial": "A-17"},
       "count": 42, "flow rate": 3, "none": None}
XML = ('<?xml version="1.0"?><status xmlns="urn:dev"><sensor id="T1"><temp unit="C">21.5</temp></sensor>'
       '<sensor id="T2"><temp unit="C">19.0</temp></sensor><run>true</run><note>ok</note></status>')


# ---------------------------------------------------------------------------
# servers
# ---------------------------------------------------------------------------
def body_answer(body: bytes):
    """Handler: header-less answer (HTTP/0.9 style), then close."""
    return lambda conn, request: conn.sendall(body)


def http_answer(body: bytes, status: str = "200 OK", ctype: str = "application/json"):
    """Handler: HTTP/1.1 answer with Content-Length."""
    def handler(conn, request):
        conn.sendall(f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                     + body)
    return handler


@pytest.fixture()
def raw_server():
    """Factory of :class:`RawServer` instances (closed after the test)."""
    servers = []

    def make(handler, **kwargs):
        server = RawServer(handler, **kwargs)
        servers.append(server)
        return server
    yield make
    for server in servers:
        server.close()


@pytest.fixture()
def ws_server():
    """Factory of :class:`WSServer` instances (closed after the test)."""
    servers = []

    def make(script, **kwargs):
        server = WSServer(script, **kwargs)
        servers.append(server)
        return server
    yield make
    for server in servers:
        server.close()


@pytest.fixture()
def http_server():
    """A standard library HTTP server whose answer can be changed (``state["body"]``)."""
    state = {"body": json.dumps(DOC).encode(), "ctype": "application/json", "hits": 0}

    class Handler(BaseHTTPRequestHandler):
        """Answers every GET with ``state["body"]``."""

        def do_GET(self):  # noqa: N802
            state["hits"] += 1
            self.send_response(200)
            self.send_header("Content-Type", state["ctype"])
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}/data.json"
    yield state
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
def test_http_get_with_headers(http_server):
    resp = http_get(http_server["url"], timeout=2)
    assert resp.status == 200 and not resp.headerless
    assert json.loads(resp.text()) == DOC
    assert "200" in resp.summary()


def test_http_get_headerless_http09(raw_server):
    server = raw_server(body_answer(b'{"t": 21.5}'))
    resp = http_get(server.url("/v"), timeout=2)
    assert resp.headerless and resp.version == "HTTP/0.9"
    assert resp.text() == '{"t": 21.5}'
    assert server.requests[0].startswith(b"GET /v HTTP/1.1\r\n")


def test_http09_request_mode_sends_one_line(raw_server):
    server = raw_server(body_answer(b"<a>1</a>"))
    resp = http_get(server.url("/x?y=1"), timeout=2, mode="http09")
    assert resp.text() == "<a>1</a>"
    assert server.requests[0] == b"GET /x?y=1\r\n"


def test_http_get_device_keeps_connection_open(raw_server):
    """A device that sends its data but never closes: the data received before the time limit is the answer."""
    def handler(conn, request):
        conn.sendall(b'{"v": 1}')
        time.sleep(1.5)
    server = raw_server(handler)
    started = time.monotonic()
    resp = http_get(server.url(), timeout=0.6)
    assert resp.text() == '{"v": 1}' and time.monotonic() - started < 1.4


def test_http_get_chunked(raw_server):
    def handler(conn, request):
        conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\n{\"a\"\r\n3\r\n: 1\r\n1\r\n}\r\n0\r\n\r\n")
        time.sleep(1)   # keeps the connection open: completion must be detected from the last chunk
    server = raw_server(handler)
    started = time.monotonic()
    resp = http_get(server.url(), timeout=3)
    assert json.loads(resp.text()) == {"a": 1}
    assert time.monotonic() - started < 0.9


def test_http_get_errors(raw_server):
    server = raw_server(http_answer(b"nope", "404 Not Found", "text/plain"))
    with pytest.raises(WebError, match="404"):
        http_get(server.url(), timeout=2)
    silent = raw_server(lambda conn, request: time.sleep(1))
    with pytest.raises(WebError, match="No answer"):
        http_get(silent.url(), timeout=0.3)
    closed = raw_server(lambda conn, request: None)
    with pytest.raises(WebError, match="without sending data"):
        http_get(closed.url(), timeout=2)
    port = closed.port
    closed.close()
    with pytest.raises(WebError, match="Cannot connect"):
        http_get(f"http://127.0.0.1:{port}/", timeout=1)


def test_http_basic_auth_and_url_checks(raw_server):
    server = raw_server(http_answer(b"{}"))
    http_get(f"http://user:p%40ss@127.0.0.1:{server.port}/", timeout=2)
    assert b"Authorization: Basic dXNlcjpwQHNz\r\n" in server.requests[0]
    for bad in ("", "ftp://x/", "http:///nohost", "http://h/a b"):
        with pytest.raises(WebError):
            parse_url(bad, "http")
    with pytest.raises(WebError, match="ws://"):
        parse_url("http://h/", "websocket")
    assert parse_url("wss://h/live", "websocket").port == 443


# ---------------------------------------------------------------------------
# WebSocket client
# ---------------------------------------------------------------------------
def test_websocket_messages_fragments_ping_and_send(ws_server):
    def script(conn, server):
        conn.sendall(ws_frame(0x1, b'{"a": 1}'))
        conn.sendall(ws_frame(0x9, b"hi"))                          # ping -> client must answer pong
        server.record(conn)
        conn.sendall(ws_frame(0x1, b'{"b":', fin=False) + ws_frame(0x0, b" 2}"))   # fragmented message
        server.record(conn)                                         # the client's text message
        conn.sendall(ws_frame(0x2, b"x" * 70000))                  # 64-bit length, binary
        conn.sendall(ws_frame(0x8, struct_close(1000)))
        time.sleep(0.2)
    server = ws_server(script)
    ws = WebSocketClient(server.url("/live"), timeout=2)
    ws.connect()
    assert ws.receive() == '{"a": 1}'
    assert ws.receive() == '{"b": 2}'
    assert server.received[0] == (0xA, b"hi")
    ws.send_text("hello")
    assert ws.receive() == b"x" * 70000
    assert wait_for(lambda: len(server.received) >= 2)
    assert server.received[1] == (0x1, b"hello")
    with pytest.raises(WebError, match="closed"):
        ws.receive()
    assert not ws.connected


def struct_close(code: int) -> bytes:
    """Payload of a close frame."""
    return code.to_bytes(2, "big")


def test_websocket_handshake_errors(ws_server):
    bad_accept = ws_server(lambda conn, server: None, accept_override="wrong")
    with pytest.raises(WebError, match="Sec-WebSocket-Accept"):
        WebSocketClient(bad_accept.url(), timeout=2).connect()
    refused = ws_server(lambda conn, server: None, status=403)
    with pytest.raises(WebError, match="403"):
        WebSocketClient(refused.url(), timeout=2).connect()


def test_websocket_receive_timeout_returns_none(ws_server):
    server = ws_server(lambda conn, server: time.sleep(1))
    ws = WebSocketClient(server.url(), timeout=2)
    ws.connect()
    assert ws.receive(timeout=0.2) is None
    ws.close()


# ---------------------------------------------------------------------------
# Documents and keys
# ---------------------------------------------------------------------------
def test_flatten_and_resolve_json():
    doc = parse_document(json.dumps(DOC), "json")
    keys = dict(flatten(doc, "json"))
    assert keys["meter.power"] == 12.5 and keys["meter.phases[1]"] == 229.8
    assert keys['["flow rate"]'] == 3 and keys["none"] is None and keys["meter.ok"] is True
    for path, value in keys.items():
        assert resolve(doc, path, "json") == value
    assert flatten(parse_document("[1, {\"a\": 2}]", "json"), "json") == [("[0]", 1), ("[1].a", 2)]
    assert flatten(parse_document("7", "json"), "json") == [("$", 7)]
    with pytest.raises(Missing):
        resolve(doc, "meter.phases[5]", "json")


def test_flatten_and_resolve_xml():
    doc = parse_document(XML, "xml")
    keys = dict(flatten(doc, "xml"))
    assert keys == {"status/sensor[1]/@id": "T1", "status/sensor[1]/temp/@unit": "C", "status/sensor[1]/temp": "21.5",
                    "status/sensor[2]/@id": "T2", "status/sensor[2]/temp/@unit": "C", "status/sensor[2]/temp": "19.0",
                    "status/run": "true", "status/note": "ok"}
    for path, value in keys.items():
        assert resolve(doc, path, "xml") == value
    assert resolve(doc, "status/sensor/temp", "xml") == "21.5"      # no index = the first
    with pytest.raises(Missing):
        resolve(doc, "status/sensor[3]/temp", "xml")
    with pytest.raises(WebDataError, match="ENTITY"):
        parse_document('<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>', "xml")
    with pytest.raises(WebDataError, match="XML"):
        parse_document("<x>", "xml")
    with pytest.raises(WebDataError, match="JSON"):
        parse_document("{bad", "json")


def test_paths_types_and_conversion():
    assert check_path(" a.b[0] ", "json") == "a.b[0]"
    for bad in ("a b", ".a", "a[x]", "a[0]b", ""):
        with pytest.raises(WebDataError):
            check_path(bad, "json")
    with pytest.raises(WebDataError):
        check_path("status/sensor[0]", "xml")
    assert suggest_type(True) == ("BOOL", 0) and suggest_type(5) == ("DINT", 0)
    assert suggest_type(2**40) == ("LREAL", 0) and suggest_type("21.5") == ("LREAL", 0)
    assert suggest_type("false")[0] == "BOOL" and suggest_type("abc")[0] == "STRING"
    assert convert("21.5", "REAL") == 21.5 and convert("on", "BOOL") is True and convert(0, "BOOL") is False
    assert convert(3.6, "INT") == 4 and convert("abcdef", "STRING", 3) == "abc"
    with pytest.raises(WebDataError, match="range"):
        convert(70000, "INT")
    with pytest.raises(WebDataError, match="null"):
        convert(None, "REAL")
    with pytest.raises(WebDataError, match="number"):
        convert("x", "REAL")
    assert path_to_name('status/sensor[2]/@id') == "status.sensor_2.id"
    assert path_to_name('data["flow rate"]') == "data.flow rate"


def test_web_address_parsing():
    addr = parse_tag_address("meter.power:REAL", None, None, "http")
    assert isinstance(addr, WebAddress) and addr.path == "meter.power" and addr.data_type == "REAL"
    assert not addr.writable_area
    assert parse_web_address("status/sensor[2]/@id", "STRING", 10).string_length == 10
    with pytest.raises(WebDataError):
        parse_web_address("a b", "REAL")
    with pytest.raises(WebDataError, match="not available"):
        parse_web_address("a", "TIME")


# ---------------------------------------------------------------------------
# Connection (through the connection manager)
# ---------------------------------------------------------------------------
def make_device(db, url, protocol="http", fmt="json", **kwargs) -> PLC:
    """Create and commit a web device."""
    plc = PLC(name=kwargs.pop("name", "Meter"), enabled=True, offline_delay=0)
    WebSettings(protocol=protocol, url=url, data_format=fmt, poll_interval=kwargs.pop("poll_interval", 0.2),
                timeout=kwargs.pop("timeout", 1.0), **kwargs).apply(plc)
    db.session.add(plc)
    db.session.commit()
    return plc


def test_http_device_read_through_manager(db, http_server):
    plc = make_device(db, http_server["url"], poll_interval=60)
    assert plc.ip_address == "127.0.0.1" and plc.is_web and plc.endpoint == http_server["url"]
    manager = get_manager()
    addresses = {1: parse_web_address("meter.power", "REAL"), 2: parse_web_address("meter.ok", "BOOL"),
                 3: parse_web_address("missing", "REAL"), 4: parse_web_address("none", "REAL"),
                 5: parse_web_address('["flow rate"]', "INT")}
    res = manager.read(plc, addresses)
    assert res[1].value == 12.5 and res[2].value is True and res[5].value == 3
    assert "not in the data" in res[3].error and "null" in res[4].error
    hits = http_server["hits"]
    manager.read(plc, addresses)
    assert http_server["hits"] == hits                     # within the poll interval: no new request
    assert manager.status(plc)["state"] == "online"
    with pytest.raises(Exception, match="read only"):
        manager.write(plc, addresses[1], 1.0)


def test_http_device_offline_and_back(db, raw_server):
    state = {"down": False}

    def handler(conn, request):
        if state["down"]:
            return            # closes without data
        conn.sendall(b'{"v": 5}')
    server = raw_server(handler)
    plc = make_device(db, server.url(), poll_interval=0.1)
    manager = get_manager()
    addr = {1: parse_web_address("v", "INT")}
    assert manager.read(plc, addr)[1].value == 5
    state["down"] = True
    time.sleep(0.15)
    res = manager.read(plc, addr)
    assert res[1].error and manager.status(plc)["state"] == "offline"
    state["down"] = False
    assert wait_for(lambda: manager.read(plc, addr)[1].value == 5, timeout=5)


def test_xml_device_and_test_connection(db, raw_server):
    server = raw_server(http_answer(XML.encode(), ctype="application/xml"))
    plc = make_device(db, server.url(), fmt="xml")
    res = get_manager().read(plc, {1: parse_web_address("status/sensor[2]/temp", "REAL"),
                                   2: parse_web_address("status/run", "BOOL")})
    assert res[1].value == 19.0 and res[2].value is True
    info = get_manager().test_connection(plc)
    assert info["ok"] and "XML" in info["module"]


def test_websocket_device_merges_messages(db, ws_server):
    def script(conn, server):
        server.record(conn)                          # subscribe message
        conn.sendall(ws_frame(0x1, b'{"temp": 20.5}'))
        conn.sendall(ws_frame(0x1, b'{"hum": 40}'))
        time.sleep(3)
    server = ws_server(script)
    plc = make_device(db, server.url("/live"), protocol="websocket", ws_message='{"subscribe": "all"}',
                      poll_interval=10)
    manager = get_manager()
    addr = {1: parse_web_address("temp", "REAL"), 2: parse_web_address("hum", "INT")}
    assert wait_for(lambda: manager.read(plc, addr)[2].value == 40)
    assert manager.read(plc, addr)[1].value == 20.5          # kept from the earlier message
    assert server.received[0] == (0x1, b'{"subscribe": "all"}')
    manager.disconnect(plc.id)


def test_websocket_device_closed_goes_offline(db, ws_server):
    server = ws_server(lambda conn, server: (conn.sendall(ws_frame(0x1, b'{"a": 1}')), time.sleep(0.3)))
    plc = make_device(db, server.url(), protocol="websocket")
    manager = get_manager()
    addr = {1: parse_web_address("a", "INT")}
    assert wait_for(lambda: manager.read(plc, addr)[1].value == 1)
    server.close()
    assert wait_for(lambda: manager.read(plc, addr)[1].error is not None, timeout=4)


def test_probe_lists_keys(raw_server, ws_server):
    server = raw_server(body_answer(json.dumps(DOC).encode()))
    result = probe(WebSettings(url=server.url(), timeout=1))
    assert result["ok"] and "HTTP/0.9" in result["summary"]
    rows = {r["path"]: r for r in result["rows"]}
    assert rows["meter.power"]["data_type"] == "LREAL" and rows["meter.ok"]["data_type"] == "BOOL"
    assert rows["count"]["data_type"] == "DINT" and rows["meter.serial"]["data_type"] == "STRING"
    assert '"power": 12.5' in result["raw"]

    bad = raw_server(body_answer(b"not json"))
    result = probe(WebSettings(url=bad.url(), timeout=1))
    assert not result["ok"] and "JSON" in result["error"] and result["raw"] == "not json"

    ws = ws_server(lambda conn, server: (conn.sendall(ws_frame(0x1, b"<r><a>1</a></r>")), time.sleep(1)))
    result = probe(WebSettings(protocol="websocket", url=ws.url(), data_format="xml", timeout=1), extra_wait=0.2)
    assert result["ok"] and result["rows"][0]["path"] == "r/a"


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------
def settings_body(url, **extra):
    """JSON body of the wizard."""
    body = {"name": "Meter 1", "protocol": "http", "url": url, "data_format": "json", "poll_interval": 1,
            "timeout": 1, "http_mode": "auto", "verify_tls": True, "ws_message": ""}
    body.update(extra)
    return body


def test_wizard_pages_and_permissions(client, login_as, db, http_server):
    login_as("viewer")
    assert client.get("/admin/plcs/web/new").status_code == 403
    assert client.post("/admin/plcs/web/api/test", json=settings_body(http_server["url"])).status_code == 403
    client.post("/auth/logout")
    login_as("admin")
    page = client.get("/admin/plcs/web/new").get_data(as_text=True)
    assert 'id="wd-test"' in page and "Save Selected" in page and "Save All" in page
    assert "Add device" in client.get("/admin/plcs").get_data(as_text=True)


def test_wizard_test_and_save(client, login_as, db, http_server):
    login_as("admin")
    resp = client.post("/admin/plcs/web/api/test", json=settings_body("ftp://x", poll_interval=0))
    assert resp.status_code == 400 and set(resp.json["errors"]) >= {"url", "poll_interval"}

    data = client.post("/admin/plcs/web/api/test", json=settings_body(http_server["url"])).json
    assert data["ok"]
    rows = {r["path"]: r for r in data["rows"]}
    assert rows["meter.power"]["name"] == "Meter_1.meter.power"
    assert rows['["flow rate"]']["name"] == "Meter_1.flow_rate"
    assert len({r["name"].lower() for r in data["rows"]}) == len(data["rows"])

    chosen = [{"path": "meter.power", "name": "Meter_1.meter.power", "data_type": "REAL"},
              {"path": "meter.ok", "name": "Meter_1.meter.ok", "data_type": "BOOL"},
              {"path": "meter.serial", "name": "Meter_1.serial", "data_type": "STRING", "string_length": 20}]
    resp = client.post("/admin/plcs/web/api/save", json=settings_body(http_server["url"], rows=chosen))
    assert resp.status_code == 200 and resp.json["created"] == 3
    plc = PLC.query.filter_by(name="Meter 1").one()
    assert plc.protocol == "http" and plc.url == http_server["url"] and plc.comm_alarm is not None
    tags = {dp.label: dp for dp in plc.datapoints}
    assert tags["Meter_1.meter.power"].path == "meter.power" and not tags["Meter_1.meter.power"].writable
    assert tags["Meter_1.meter.ok"].widget == "indicator" and tags["Meter_1.serial"].string_length == 20
    assert tags["Meter_1.meter.power"].address == "meter.power"
    values = get_manager().read(plc, {dp.id: dp.tag_address for dp in tags.values()})
    assert values[tags["Meter_1.meter.power"].id].value == pytest.approx(12.5)

    # The saved device: existing keys are marked, new names avoid the taken ones.
    data = client.post("/admin/plcs/web/api/test", json={"plc_id": plc.id}).json
    rows = {r["path"]: r for r in data["rows"]}
    assert rows["meter.power"]["existing"] == "Meter_1.meter.power" and rows["count"]["existing"] is None
    assert client.get(f"/admin/plcs/{plc.id}/web").status_code == 200
    resp = client.post("/admin/plcs/web/api/save", json={"plc_id": plc.id, "rows": [
        {"path": "count", "name": "Meter_1.count", "data_type": "DINT"}]})
    assert resp.json["ok"] and plc.datapoints.count() == 4


def test_wizard_save_rejects_bad_rows_and_saves_nothing(client, login_as, db, http_server):
    login_as("admin")
    rows = [{"path": "meter.power", "name": "1bad name", "data_type": "REAL"},
            {"path": "count", "name": "dup", "data_type": "DINT"},
            {"path": "count", "name": "DUP", "data_type": "DINT"},
            {"path": "a b", "name": "ok_name", "data_type": "REAL"},
            {"path": "x", "name": "s", "data_type": "STRING", "string_length": 0}]
    resp = client.post("/admin/plcs/web/api/save", json=settings_body(http_server["url"], rows=rows))
    assert resp.status_code == 400
    assert set(resp.json["rows"]) == {"0", "2", "3", "4"}
    assert "twice" in resp.json["rows"]["2"][0]
    assert PLC.query.count() == 0 and DataPoint.query.count() == 0 and Alarm.query.count() == 0

    resp = client.post("/admin/plcs/web/api/save", json=settings_body(http_server["url"], rows=[]))
    assert resp.status_code == 400 and "at least one" in resp.json["error"]
    resp = client.post("/admin/plcs/web/api/save", json=settings_body(http_server["url"], name="", rows=[
        {"path": "count", "name": "c", "data_type": "DINT"}]))
    assert "name" in resp.json["errors"]


def test_wizard_check_names(client, login_as, db, plc):
    login_as("admin")
    from tests.conftest import add_point  # noqa: PLC0415
    from app.models import Dashboard  # noqa: PLC0415

    dash = Dashboard(name="D", slug="d")
    db.session.add(dash)
    add_point(dash, plc, "Taken", "DB1.DBW0", data_type="INT")
    db.session.commit()
    problems = client.post("/admin/plcs/web/api/check-names", json={"names": ["ok", "taken", "a b", "x", "X"]}).json
    assert problems["problems"][0] == [] and problems["problems"][1] and problems["problems"][2]
    assert problems["problems"][3] == [] and "twice" in problems["problems"][4][0]


def test_plc_form_edits_web_device(client, login_as, db, http_server):
    login_as("admin")
    resp = client.post("/admin/plcs/new", data={
        "name": "Web1", "protocol": "websocket", "url": "http://bad", "data_format": "xml", "poll_interval": "2",
        "timeout": "3", "offline_delay": "10", "http_mode": "auto", "comm_severity": "high"})
    assert resp.status_code == 200 and "ws://" in resp.get_data(as_text=True)
    resp = client.post("/admin/plcs/new", data={
        "name": "Web1", "protocol": "websocket", "url": "wss://10.0.0.9:8443/live", "data_format": "xml",
        "poll_interval": "2", "timeout": "3", "offline_delay": "10", "http_mode": "auto", "comm_severity": "high",
        "verify_tls": "", "ws_message": "hi", "enabled": "y"})
    assert resp.status_code == 302
    plc = PLC.query.filter_by(name="Web1").one()
    assert (plc.ip_address, plc.tcp_port, plc.data_format, plc.verify_tls, plc.ws_message) == \
        ("10.0.0.9", 8443, "xml", False, "hi")
    page = client.get(f"/admin/plcs/{plc.id}/edit").get_data(as_text=True)
    assert "wss://10.0.0.9:8443/live" in page and "Test and add tags" in page


def test_tag_form_accepts_key_path(client, login_as, db, http_server):
    login_as("admin")
    plc = make_device(db, http_server["url"])
    form = {"label": "Power_1", "plc_id": str(plc.id), "address": "meter.phases[1]", "data_type": "REAL",
            "widget": "value", "scale": "1", "offset": "0", "decimals": "1", "width": "1", "write_access": "all",
            "true_text": "ON", "false_text": "OFF", "log_interval": "60"}
    resp = client.post("/tags/new", data=form)
    assert resp.status_code == 302, resp.get_data(as_text=True)[:3000]
    dp = DataPoint.query.filter_by(label="Power_1").one()
    assert dp.path == "meter.phases[1]" and dp.address == "meter.phases[1]"
    resp = client.post("/tags/new", data=dict(form, label="Power_2", writable="y"))
    assert resp.status_code == 200 and "cannot be written" in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# POST requests and extra headers
# ---------------------------------------------------------------------------
def post_echo(conn, request):
    """Handler: reads the POST body (Content-Length) and answers with what it received."""
    head, _, body = request.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":")[1])
    while len(body) < length:
        body += conn.recv(4096)
    first = head.split(b"\r\n")[0].decode()
    headers = {k.strip().lower(): v.strip() for k, _, v in
               (line.decode().partition(":") for line in head.split(b"\r\n")[1:])}
    answer = json.dumps({"line": first, "body": body.decode(), "ctype": headers.get("content-type", ""),
                         "key": headers.get("x-api-key", ""), "temp": 21.5}).encode()
    conn.sendall(answer)    # header-less answer to a POST also works


def test_post_with_body_and_headers(raw_server):
    from app.plc.web_client import parse_headers  # noqa: PLC0415

    server = raw_server(post_echo)
    resp = http_get(server.url("/api"), timeout=2, method="POST", body='{"cmd": "read"}',
                    content_type="application/json", headers=parse_headers("X-API-Key: 1234"))
    echo = json.loads(resp.text())
    assert echo == {"line": "POST /api HTTP/1.1", "body": '{"cmd": "read"}', "ctype": "application/json",
                    "key": "1234", "temp": 21.5}
    with pytest.raises(WebError, match="HTTP/0.9"):
        http_get(server.url(), timeout=1, mode="http09", method="POST")


def test_header_rules():
    from app.plc.web_client import parse_headers  # noqa: PLC0415

    assert parse_headers("# comment\n\nX-A: 1\nAuthorization: Bearer x y") == [("X-A", "1"),
                                                                             ("Authorization", "Bearer x y")]
    for bad, match in (("no colon", "line 1"), ("Host: x", "automatically"), ("Bad Name: 1", "line 1"),
                       ("X-A: \x01", "control"), ("X-A: שלום", "Latin")):
        with pytest.raises(WebError, match=match):
            parse_headers(bad)


def test_settings_validate_post(db):
    settings, errors = WebSettings.from_dict(settings_body("http://h/x", http_method="POST", http_body="a=1",
                                                           http_content_type="", http_headers="X-K: 1"))
    assert not errors and settings.http_content_type == "application/x-www-form-urlencoded"
    assert settings.request_options()["body"] == "a=1" and settings.request_options()["headers"] == [("X-K", "1")]
    get = WebSettings(http_method="GET", http_body="a=1")
    assert get.request_options()["body"] == b""                           # the body is sent with POST only
    _s, errors = WebSettings.from_dict(settings_body("http://h/x", http_method="POST", http_mode="http09",
                                                     http_headers="Host: x"))
    assert set(errors) == {"http_method", "http_headers"}
    _s, errors = WebSettings.from_dict(settings_body("http://h/x", http_method="PUT"))
    assert "http_method" in errors


def test_post_device_through_wizard_and_manager(client, login_as, db, raw_server):
    server = raw_server(post_echo)
    login_as("admin")
    body = settings_body(server.url("/api"), http_method="POST", http_body="id=3&page=1",
                         http_headers="X-API-Key: k1")
    data = client.post("/admin/plcs/web/api/test", json=body).json
    rows = {r["path"]: r for r in data["rows"]}
    assert data["ok"] and rows["body"]["value"] == "id=3&page=1" and rows["key"]["value"] == "k1"
    assert rows["ctype"]["value"] == "application/x-www-form-urlencoded"
    resp = client.post("/admin/plcs/web/api/save", json=dict(body, rows=[
        {"path": "temp", "name": "Post_temp", "data_type": "REAL"}]))
    assert resp.json["ok"]
    plc = PLC.query.filter_by(name="Meter 1").one()
    assert (plc.http_method, plc.http_body, plc.http_headers) == ("POST", "id=3&page=1", "X-API-Key: k1")
    dp = DataPoint.query.filter_by(label="Post_temp").one()
    assert get_manager().read(plc, {1: dp.tag_address})[1].value == pytest.approx(21.5)
    assert all(r.startswith(b"POST /api HTTP/1.1") for r in server.requests)


def test_websocket_handshake_sends_extra_headers(ws_server):
    from app.plc.web_client import parse_headers  # noqa: PLC0415

    server = ws_server(lambda conn, server: time.sleep(0.3))
    ws = WebSocketClient(server.url(), timeout=2, headers=parse_headers("Authorization: Bearer T"))
    ws.connect()
    ws.close()
    assert b"Authorization: Bearer T\r\n" in server.requests[0]


def test_plc_form_post_fields(client, login_as, db):
    login_as("admin")
    data = {"name": "Post1", "protocol": "http", "url": "http://10.0.0.5/api", "data_format": "json",
            "poll_interval": "2", "timeout": "3", "offline_delay": "10", "http_mode": "auto",
            "comm_severity": "high", "verify_tls": "y", "enabled": "y", "http_method": "POST",
            "http_content_type": "application/json", "http_body": '{"read": 1}', "http_headers": "Host: x"}
    resp = client.post("/admin/plcs/new", data=data)
    assert resp.status_code == 200 and "set automatically" in resp.get_data(as_text=True)
    resp = client.post("/admin/plcs/new", data=dict(data, http_headers="X-Token: abc"))
    assert resp.status_code == 302
    plc = PLC.query.filter_by(name="Post1").one()
    assert (plc.http_method, plc.http_content_type, plc.http_body, plc.http_headers) == \
        ("POST", "application/json", '{"read": 1}', "X-Token: abc")
    assert 'id="post-fields"' in client.get(f"/admin/plcs/{plc.id}/edit").get_data(as_text=True)
