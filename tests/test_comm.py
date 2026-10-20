"""Tests for communication-loss handling: grace period, reconnects, timeouts and comm alarms."""

import re
import time as real_time

import pytest

from app.alarms.engine import scan_alarms
from app.models import PLC, Alarm, AlarmEvent
from app.plc import driver
from app.plc.addressing import parse_address
from app.plc.driver import PLCConnectionManager, PLCError, SimulatedConnection, Snap7Connection
from tests.conftest import add_point


class FakeClock:
    """Replacement for the ``time`` module inside ``app.plc.driver`` with a movable monotonic clock."""

    def __init__(self):
        """Start the clock at an arbitrary point."""
        self.t = 1000.0

    def monotonic(self) -> float:
        """Current fake monotonic time."""
        return self.t

    def advance(self, seconds: float) -> None:
        """Move the clock forward.

        Args:
            seconds: Seconds to add.
        """
        self.t += seconds

    @staticmethod
    def time() -> float:
        """Real wall-clock time."""
        return real_time.time()

    @staticmethod
    def strftime(fmt: str) -> str:
        """Real ``strftime`` (used by the simulator).

        Args:
            fmt: Format string.

        Returns:
            Formatted time.
        """
        return real_time.strftime(fmt)


@pytest.fixture()
def clock(monkeypatch):
    """Patch the driver's clock.

    Returns:
        The :class:`FakeClock`.
    """
    fake = FakeClock()
    monkeypatch.setattr(driver, "time", fake)
    return fake


@pytest.fixture()
def manager():
    """A simulated connection manager (reconnect delay 5 s).

    Returns:
        :class:`PLCConnectionManager`.
    """
    SimulatedConnection.reset()
    mgr = PLCConnectionManager(simulation=True, reconnect_delay=5.0)
    yield mgr
    mgr.disconnect_all()
    SimulatedConnection.reset()


def make_plc(id_=1, ip="10.1.1.1", delay=10.0, enabled=True) -> PLC:
    """An unsaved S7 PLC model.

    Args:
        id_: Id used by the manager.
        ip: Address.
        delay: Communication timeout (``offline_delay``).
        enabled: Enabled flag.

    Returns:
        :class:`PLC`.
    """
    return PLC(id=id_, name=f"P{id_}", ip_address=ip, rack=0, slot=1, tcp_port=102, protocol="s7",
               enabled=enabled, offline_delay=delay, timeout=3.0)


ADDR = {"a": parse_address("DB1.DBW0:INT"), "b": parse_address("M0.0")}


def write_value(manager, plc, addr, value):
    """Store a fixed value in the simulator (written values are not animated)."""
    manager.write(plc, addr, value)


# ---------------------------------------------------------------- grace period
def test_short_interruption_keeps_last_values(manager, clock):
    """Within the communication timeout reads return the last good values (stale)."""
    plc = make_plc(delay=10)
    write_value(manager, plc, ADDR["a"], 42)
    first = manager.read(plc, ADDR)
    assert first["a"].value == 42 and not first["a"].stale
    assert manager.status(plc)["state"] == "online"

    SimulatedConnection.set_offline(plc.ip_address)
    res = manager.read(plc, ADDR)
    assert res["a"].ok and res["a"].stale and res["a"].value == 42
    st = manager.status(plc)
    assert st["state"] == "unstable" and st["connected"] is True and st["error"]

    clock.advance(9.9)
    assert manager.read(plc, ADDR)["a"].stale
    clock.advance(0.2)
    res = manager.read(plc, ADDR)
    assert not res["a"].ok and res["a"].error
    st = manager.status(plc)
    assert st["state"] == "offline" and st["connected"] is False and st["down_for"] >= 10

    SimulatedConnection.set_offline(plc.ip_address, False)
    clock.advance(10)                       # past any back-off
    res = manager.read(plc, ADDR)
    assert res["a"].ok and not res["a"].stale and res["a"].value == 42
    assert manager.status(plc)["state"] == "online" and manager.status(plc)["error"] is None


def test_zero_delay_reports_at_once(manager, clock):
    """``offline_delay = 0`` keeps the old behaviour: errors immediately."""
    plc = make_plc(delay=0)
    manager.read(plc, ADDR)
    SimulatedConnection.set_offline(plc.ip_address)
    res = manager.read(plc, ADDR)
    assert not res["a"].ok and manager.status(plc)["state"] == "offline"


def test_tag_never_read_has_no_stale_value(manager, clock):
    """Tags without a previous good value get the error even inside the grace period."""
    plc = make_plc()
    manager.read(plc, {"a": ADDR["a"]})
    SimulatedConnection.set_offline(plc.ip_address)
    res = manager.read(plc, ADDR)
    assert res["a"].stale and not res["b"].ok


def test_changed_settings_drop_cached_values(manager, clock):
    """After the IP address changes, the old device's values are never shown."""
    plc = make_plc()
    manager.read(plc, ADDR)
    plc.ip_address = "10.9.9.254"           # simulated unreachable host
    res = manager.read(plc, ADDR)
    assert not res["a"].ok and not res["a"].stale


# ---------------------------------------------------------------- reconnects
class FlakyConnection:
    """Connection whose first read breaks the link (like a TCP reset)."""

    connects = 0
    reads = 0

    def __init__(self, *args, **kwargs):
        """Create a closed connection."""
        self._connected = False

    def connect(self):
        """Open the connection (always works)."""
        FlakyConnection.connects += 1
        self._connected = True

    @property
    def connected(self):
        """Connection state."""
        return self._connected

    def close(self):
        """Close the connection."""
        self._connected = False

    def read(self, area, db_number, start, size):
        """Fail the very first read by breaking the connection, then return zeros."""
        FlakyConnection.reads += 1
        if FlakyConnection.reads == 1:
            self._connected = False
            raise ConnectionResetError("connection reset by peer")
        return bytearray(size)


def test_broken_connection_is_reopened_immediately(manager, clock, monkeypatch):
    """A connection reset is repaired within the same request – no interruption is visible."""
    FlakyConnection.connects = FlakyConnection.reads = 0
    monkeypatch.setattr(manager, "_new_connection", lambda plc: FlakyConnection())
    plc = make_plc()
    res = manager.read(plc, {"a": ADDR["a"]})
    assert res["a"].ok and res["a"].value == 0
    assert FlakyConnection.connects == 2 and FlakyConnection.reads == 2
    st = manager.status(plc)
    assert st["state"] == "online" and st["down_for"] is None


def test_backoff_grows_and_is_capped(manager, clock):
    """Failed connection attempts are retried after 0.5, 1, 2, 4 s… at most reconnect_delay."""
    plc = make_plc(ip="10.1.1.254")
    entry = manager._entry(plc.id)
    waits = []
    for _ in range(6):
        manager.read(plc, ADDR)
        waits.append(manager._retry_wait(entry))
        clock.advance(waits[-1] + 0.01)
    assert waits == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0]


def test_backoff_after_thousands_of_failures(manager):
    """A PLC offline for days: the failure counter keeps growing, the wait stays at reconnect_delay."""
    plc = make_plc(ip="10.1.1.253")
    entry = manager._entry(plc.id)
    for failures in (1024, 1025, 10_000, 10**9):
        entry.failures = failures
        assert manager._retry_wait(entry) == manager.reconnect_delay      # no OverflowError


def test_backoff_blocks_until_due(manager, clock):
    """During the back-off no connection attempt is made."""
    plc = make_plc(ip="10.1.1.254")
    manager.read(plc, ADDR)
    entry = manager._entry(plc.id)
    assert entry.failures == 1
    manager.read(plc, ADDR)
    assert entry.failures == 1                       # still waiting, no new attempt
    clock.advance(0.6)
    manager.read(plc, ADDR)
    assert entry.failures == 2


def test_write_failure_marks_interruption(manager, clock):
    """A write to an unreachable PLC raises and starts the interruption timer."""
    plc = make_plc(ip="10.1.1.254")
    with pytest.raises(PLCError):
        manager.write(plc, ADDR["a"], 1)
    assert manager.status(plc)["down_for"] is not None


def test_probe(manager, clock):
    """``probe`` opens the connection and updates the state."""
    assert manager.probe(make_plc()) is True
    bad = make_plc(id_=2, ip="10.1.1.254", delay=0)
    assert manager.probe(bad) is False and manager.status(bad)["state"] == "offline"


def test_status_disabled_and_unknown(manager):
    """Disabled PLCs and PLCs never contacted have their own states."""
    assert manager.status(make_plc())["state"] == "unknown"
    assert manager.status(make_plc(id_=3, enabled=False))["state"] == "disabled"


# ---------------------------------------------------------------- snap7 timeouts
class FakeSocket:
    """Records ``settimeout`` calls."""

    def __init__(self):
        """No timeout yet."""
        self.timeout = None

    def settimeout(self, value):
        """Remember the timeout."""
        self.timeout = value


class FakeISO:
    """Stands in for snap7 3.x ``ISOTCPConnection``."""

    def __init__(self):
        """Default 5 s like snap7."""
        self.timeout = 5.0
        self.socket = FakeSocket()


class FakeClient:
    """Stands in for ``snap7.client.Client``."""

    def __init__(self):
        """Start disconnected."""
        self.params = {}
        self.connection = None

    def set_param(self, param, value):
        """Record parameters."""
        self.params[param] = value


def test_snap7_timeouts_applied():
    """Before connecting: set_param in ms; after: the 3.x socket timeout."""
    from snap7.type import Parameter

    conn = Snap7Connection("10.0.0.1", 0, 1, timeout=1.5)
    client = FakeClient()
    conn._apply_timeouts(client, connected=False)
    assert client.params == {Parameter.PingTimeout: 1500, Parameter.SendTimeout: 1500, Parameter.RecvTimeout: 1500}
    client.connection = FakeISO()
    conn._apply_timeouts(client, connected=True)
    assert client.connection.timeout == 1.5 and client.connection.socket.timeout == 1.5


def test_snap7_timeout_none_keeps_defaults():
    """Without a timeout nothing is changed."""
    client = FakeClient()
    client.connection = FakeISO()
    Snap7Connection("10.0.0.1", 0, 1)._apply_timeouts(client, connected=True)
    assert client.connection.timeout == 5.0 and client.params == {}


def test_manager_passes_timeout_to_snap7():
    """Real (non-simulated) S7 connections get the PLC's request timeout."""
    mgr = PLCConnectionManager(simulation=False)
    conn = mgr._new_connection(make_plc())
    assert isinstance(conn, Snap7Connection) and conn.timeout == 3.0


def test_timeout_is_part_of_s7_signature():
    """Changing the S7 timeout reconnects with the new value."""
    a, b = make_plc(), make_plc()
    b.timeout = 1.0
    assert a.connection_signature != b.connection_signature


# ---------------------------------------------------------------- communication alarms
@pytest.fixture()
def comm(db, plc):
    """The fixture PLC (10 s timeout) with a tag and an enabled communication alarm.

    Returns:
        Dict ``{"plc", "alarm", "tag"}``.
    """
    tag = add_point(None, plc, "Level", "DB5.DBW0:INT")
    alarm = Alarm.sync_comm_alarm(plc, True, "critical")
    db.session.commit()
    return {"plc": plc, "alarm": alarm, "tag": tag}


def test_comm_alarm_waits_for_timeout_then_clears(app, db, comm, clock):
    """No alarm for a short drop; an alarm after the timeout; auto close on recovery."""
    plc, alarm = comm["plc"], comm["alarm"]
    assert alarm.name == "PLC1 communication lost" and alarm.is_comm and alarm.severity == "critical"
    scan_alarms()
    assert alarm.open_event is None

    SimulatedConnection.set_offline(plc.ip_address)
    scan_alarms()
    clock.advance(5)
    scan_alarms()
    assert alarm.open_event is None                    # 5 s < 10 s: still in the grace period

    clock.advance(6)
    stats = scan_alarms()
    ev = alarm.open_event
    assert stats["raised"] == 1 and ev is not None
    assert ev.condition == "No communication with PLC1 for 10 s" and "10.0.0.1" in ev.value
    assert ev.severity == "critical"

    SimulatedConnection.set_offline(plc.ip_address, False)
    clock.advance(10)
    assert scan_alarms()["cleared"] == 1
    db.session.refresh(ev)
    assert ev.close_type == "auto"


def test_comm_alarm_for_plc_without_tags(app, db, clock):
    """PLCs without tags are probed by opening the connection."""
    plc = PLC(name="Bare", ip_address="10.0.0.254", offline_delay=0)
    db.session.add(plc)
    db.session.flush()
    alarm = Alarm.sync_comm_alarm(plc, True, "high")
    db.session.commit()
    scan_alarms()
    assert alarm.is_active


def test_comm_alarm_ignored_for_disabled_plc(app, db, comm, clock):
    """Disabling the PLC closes its communication alarm automatically."""
    plc, alarm = comm["plc"], comm["alarm"]
    plc.offline_delay = 0
    plc.ip_address = "10.0.0.254"
    db.session.commit()
    scan_alarms()
    assert alarm.is_active
    plc.enabled = False
    db.session.commit()
    scan_alarms()
    assert not alarm.is_active


def test_stale_values_do_not_flip_tag_alarms(app, db, comm, clock):
    """Tag alarms keep their state while the PLC briefly drops (stale values)."""
    plc, tag = comm["plc"], comm["tag"]
    tag.writable = True
    from app.plc.service import write_datapoint  # noqa: PLC0415
    write_datapoint(tag, 50)
    high = Alarm(name="Level high", datapoint=tag, operator="gt", setpoint="40")
    db.session.add(high)
    db.session.commit()
    scan_alarms()
    assert high.is_active
    SimulatedConnection.set_offline(plc.ip_address)
    clock.advance(2)
    stats = scan_alarms()
    assert high.is_active and stats["cleared"] == 0 and stats["unreadable"] == 0


def test_values_api_reports_stale_and_state(client, login_as, db, dashboards, plc, clock):
    """The screen API marks stale values and reports ``state: unstable``."""
    login_as("admin")
    url = f"/api/dashboards/{dashboards['private'].id}/values"
    client.get(url)
    SimulatedConnection.set_offline(plc.ip_address)
    data = client.get(url).get_json()
    temp = data["values"][str(dashboards["points"]["temp"].id)]
    assert temp["quality"] == "good" and temp["stale"] is True
    p = data["plcs"][str(plc.id)]
    assert p["state"] == "unstable" and p["connected"] is True and p["offline_delay"] == 10.0


# ---------------------------------------------------------------- PLC form
def _plc_form(**over):
    """Valid PLC form data.

    Args:
        **over: Overrides.

    Returns:
        Dict for POST.
    """
    data = {"name": "Line1", "protocol": "s7", "ip_address": "10.0.0.20", "tcp_port": 102, "rack": 0, "slot": 1,
            "timeout": 2.5, "offline_delay": 15, "comm_alarm": "y", "comm_severity": "critical",
            "modbus_library": "pymodbus", "unit_id": 1, "data_order": "ABCD", "bacnet_priority": 8,
            "baudrate": 9600, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": "y"}
    data.update(over)
    return data


def test_plc_form_creates_and_syncs_comm_alarm(client, login_as, db):
    """Creating a PLC stores the timeouts and creates its communication alarm; edits keep it in sync."""
    login_as("admin")
    html = client.get("/admin/plcs/new").get_data(as_text=True)
    assert 'id="offline_delay"' in html and 'id="comm_alarm"' in html and 'id="comm-settings"' in html

    assert client.post("/admin/plcs/new", data=_plc_form()).status_code == 302
    plc = PLC.query.filter_by(name="Line1").one()
    assert plc.timeout == 2.5 and plc.offline_delay == 15
    alarm = plc.comm_alarm
    assert alarm.name == "Line1 communication lost" and alarm.enabled and alarm.severity == "critical"
    assert alarm.condition_text == "No communication with Line1 for 15 s"

    html = client.get(f"/admin/plcs/{plc.id}/edit").get_data(as_text=True)
    assert re.search(r'<input[^>]*checked[^>]*id="comm_alarm"|<input[^>]*id="comm_alarm"[^>]*checked', html)
    assert re.search(r'<option selected value="critical">|<option value="critical" selected', html)

    form = _plc_form(name="Line2", comm_severity="low")
    form.pop("comm_alarm")
    assert client.post(f"/admin/plcs/{plc.id}/edit", data=form).status_code == 302
    db.session.refresh(alarm)
    assert alarm.name == "Line2 communication lost" and not alarm.enabled and alarm.severity == "low"
    assert Alarm.query.filter_by(kind="comm").count() == 1


def test_plc_form_without_comm_alarm(client, login_as, db):
    """Unticking the box on a new PLC creates no alarm."""
    login_as("admin")
    form = _plc_form(offline_delay=0)
    form.pop("comm_alarm")
    client.post("/admin/plcs/new", data=form)
    plc = PLC.query.filter_by(name="Line1").one()
    assert plc.comm_alarm is None and plc.offline_delay == 0


def test_plc_list_shows_timeout(client, login_as, comm):
    """The PLC list shows the communication timeout and alarm icon."""
    login_as("admin")
    html = client.get("/admin/plcs").get_data(as_text=True)
    assert "timeout 10.0 s" in html and "Communication alarm on" in html


def test_delete_plc_keeps_event_history(client, login_as, db, comm, clock):
    """Deleting a PLC deletes its alarms but keeps their events."""
    plc, alarm = comm["plc"], comm["alarm"]
    plc.offline_delay = 0
    plc.ip_address = "10.0.0.254"
    db.session.commit()
    scan_alarms()
    ev_id = alarm.open_event.id
    login_as("admin")
    assert client.post(f"/admin/plcs/{plc.id}/delete").status_code == 302
    db.session.expire_all()
    ev = db.session.get(AlarmEvent, ev_id)
    assert ev is not None and ev.alarm_id is None and ev.close_type == "manual"
    assert Alarm.query.count() == 0


# ---------------------------------------------------------------- alarm pages
def test_alarm_form_comm_kind(client, login_as, db, plc, comm):
    """Communication alarms can be created from the alarm form, one per PLC."""
    login_as("admin")
    base = {"name": "Other", "kind": "comm", "plc_id": plc.id, "severity": "high", "screen_id": 0, "enabled": "y"}
    html = client.post("/alarms/new", data=base).get_data(as_text=True)
    assert "already has a communication alarm" in html
    html = client.post("/alarms/new", data={**base, "plc_id": 0}).get_data(as_text=True)
    assert "Choose a PLC" in html

    other = PLC(name="PLC2", ip_address="10.0.0.2")
    db.session.add(other)
    db.session.commit()
    assert client.post("/alarms/new", data={**base, "plc_id": other.id}).status_code == 302
    new = Alarm.query.filter_by(name="Other").one()
    assert new.is_comm and new.plc_id == other.id and new.datapoint_id is None

    html = client.get(f"/alarms/new?plc={other.id}").get_data(as_text=True)
    assert '<option selected value="comm">' in html or 'value="comm" selected' in html

    tag_form = {"name": "T", "kind": "tag", "datapoint_id": 0, "operator": "gt", "setpoint": "", "severity": "low",
                "screen_id": 0}
    html = client.post("/alarms/new", data=tag_form).get_data(as_text=True)
    assert "Choose a tag" in html and "Enter the value" in html


def test_alarm_list_and_details_for_comm(client, login_as, db, comm, clock):
    """The alarm list filters by trigger and the details page shows the PLC state."""
    login_as("admin")
    db.session.add(Alarm(name="Level high", datapoint=comm["tag"], operator="gt", setpoint="40"))
    db.session.commit()
    html = client.get("/alarms/?kind=comm").get_data(as_text=True)
    assert "PLC1 communication lost" in html and "Level high" not in html and "bi-ethernet" in html
    html = client.get("/alarms/?q=PLC1").get_data(as_text=True)
    assert "PLC1 communication lost" in html
    html = client.get("/alarms/?sort=tag").get_data(as_text=True)
    assert "Level high" in html and "PLC1 communication lost" in html
    html = client.get(f"/alarms/{comm['alarm'].id}").get_data(as_text=True)
    assert 'id="alarm-comm-state"' in html and "edit PLC / timeout" in html


def test_comm_event_shows_in_active_api(client, login_as, db, comm, clock):
    """Communication events appear in the Events API like tag alarms."""
    plc = comm["plc"]
    plc.offline_delay = 0
    plc.ip_address = "10.0.0.254"
    db.session.commit()
    scan_alarms()
    login_as("viewer")
    data = client.get("/api/alarms/active").get_json()
    assert data["count"] == 1 and data["events"][0]["name"] == "PLC1 communication lost"
    assert data["highest"] == "critical"
