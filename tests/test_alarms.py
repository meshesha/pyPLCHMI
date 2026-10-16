"""Tests for the alarm system: model logic, engine, pages, events API, banner."""

import re
import time

import pytest

from app.alarms import engine
from app.alarms.engine import AlarmScanner, scan_alarms, scan_if_due
from app.models import Alarm, AlarmEvent, Dashboard, DataPoint
from app.models.alarm import parse_setpoint, value_kind
from app.plc.service import write_datapoint
from tests.conftest import add_point


@pytest.fixture()
def hot(db, dashboards):
    """An enabled critical alarm "Temp > 80" linked to the private screen.

    Returns:
        The :class:`Alarm`.
    """
    alarm = Alarm(name="High temp", description="Boiler too hot", datapoint=dashboards["points"]["temp"],
                  operator="gt", setpoint="80", severity="critical", screen=dashboards["private"])
    db.session.add(alarm)
    db.session.commit()
    return alarm


def set_value(datapoint, value):
    """Write a simulated PLC value (written values are not animated).

    Args:
        datapoint: Tag to write.
        value: Engineering value.
    """
    writable = datapoint.writable
    datapoint.writable = True
    try:
        write_datapoint(datapoint, value)
    finally:
        datapoint.writable = writable


# ---------------------------------------------------------------- model
def test_parse_setpoint_and_kind(dashboards):
    """Setpoints are parsed by the tag type."""
    pts = dashboards["points"]
    assert value_kind(pts["temp"]) == "number" and value_kind(pts["run"]) == "bool" and value_kind(pts["name"]) == "text"
    assert parse_setpoint("80.5", "number") == 80.5
    assert parse_setpoint("ON", "bool") is True and parse_setpoint("0", "bool") is False
    with pytest.raises(ValueError):
        parse_setpoint("abc", "number")
    with pytest.raises(ValueError):
        parse_setpoint("maybe", "bool")


@pytest.mark.parametrize("op,setpoint,value,expected", [
    ("gt", "80", 81, True), ("gt", "80", 80, False), ("ge", "80", 80, True), ("lt", "10", 9.9, True),
    ("le", "10", 10.1, False), ("eq", "5", 5.0, True), ("ne", "5", 5, False),
])
def test_evaluate_numeric(hot, op, setpoint, value, expected):
    """Numeric comparisons."""
    hot.operator, hot.setpoint = op, setpoint
    assert hot.evaluate(value) is expected


def test_evaluate_bool_text_and_none(db, dashboards, hot):
    """BOOL and STRING comparisons; ``None`` never triggers."""
    run = Alarm(name="Run", datapoint=dashboards["points"]["run"], operator="eq", setpoint="true")
    text = Alarm(name="Txt", datapoint=dashboards["points"]["name"], operator="eq", setpoint="FAULT")
    db.session.add_all([run, text])
    assert run.evaluate(True) and not run.evaluate(False)
    assert text.evaluate("FAULT") and not text.evaluate("ok")
    assert hot.evaluate(None) is False
    assert hot.condition_text == "Temp > 80 °C" and hot.severity_color == "#dc3545"


# ---------------------------------------------------------------- engine
def test_engine_raise_and_auto_clear(db, dashboards, hot):
    """An event opens when the condition becomes true and closes automatically."""
    temp = dashboards["points"]["temp"]
    set_value(temp, 90)
    assert scan_alarms()["raised"] == 1
    ev = hot.open_event
    assert ev is not None and ev.name == "High temp" and ev.condition == "Temp > 80 °C" and ev.value.startswith("90")
    assert scan_alarms()["raised"] == 0                       # still active: no duplicate
    assert AlarmEvent.query.count() == 1

    set_value(temp, 20)
    assert scan_alarms()["cleared"] == 1
    db.session.refresh(ev)
    assert ev.cleared_at is not None and ev.close_type == "auto" and ev.close_text == "Automatic"
    assert hot.open_event is None

    set_value(temp, 95)
    scan_alarms()
    assert AlarmEvent.query.count() == 2                       # fires again


def test_engine_skips_disabled_and_unreadable(db, dashboards, hot, plc):
    """Disabled alarms are ignored; unreadable tags neither raise nor clear."""
    temp = dashboards["points"]["temp"]
    set_value(temp, 90)
    hot.enabled = False
    db.session.commit()
    assert scan_alarms()["checked"] == 0
    hot.enabled = True
    db.session.commit()
    scan_alarms()
    assert hot.is_active
    plc.ip_address = "10.0.0.254"          # simulated offline PLC
    db.session.commit()
    stats = scan_alarms()
    assert stats["unreadable"] == 1 and hot.is_active


def test_manual_remove_suppresses_until_clear(client, login_as, db, dashboards, hot):
    """After Remove, the alarm only fires again after its condition was false once."""
    temp = dashboards["points"]["temp"]
    set_value(temp, 90)
    scan_alarms()
    login_as("admin")
    ev = hot.open_event
    data = client.post(f"/api/alarms/events/{ev.id}/remove").get_json()
    assert data["close_type"] == "manual" and data["close_text"] == "Manual (admin)"
    db.session.refresh(hot)
    assert hot.suppressed
    scan_alarms()
    assert hot.open_event is None                              # suppressed while still true
    set_value(temp, 10)
    scan_alarms()
    db.session.refresh(hot)
    assert not hot.suppressed
    set_value(temp, 90)
    scan_alarms()
    assert hot.open_event is not None


def test_scan_if_due_throttles(app, db, dashboards, hot, monkeypatch):
    """``scan_if_due`` scans only after the interval and swallows errors."""
    calls = []
    monkeypatch.setattr(engine, "scan_alarms", lambda: calls.append(1))
    app.config["ALARM_SCAN_INTERVAL"] = 3600
    engine._last_scan["t"] = time.monotonic() - 10_000
    scan_if_due()
    engine._last_scan["t"] = time.monotonic()
    scan_if_due()
    assert calls == [1]

    def boom():
        """Fail like a broken scan."""
        raise RuntimeError("x")
    monkeypatch.setattr(engine, "scan_alarms", boom)
    engine._last_scan["t"] = time.monotonic() - 10_000
    scan_if_due()                                              # no exception


def test_scanner_disabled_in_testing(client, app):
    """The background scanner is off in the testing configuration."""
    client.get("/")
    assert app.config["ALARM_SCANNER"] is False and AlarmScanner.thread is None


# ---------------------------------------------------------------- pages
@pytest.mark.parametrize("who", ["viewer", "guest"])
def test_alarm_pages_admin_only(client, login_as, hot, who):
    """Only admins manage alarms; everyone logged in sees the history."""
    login_as(who)
    for url in ("/alarms/", "/alarms/new", f"/alarms/{hot.id}", f"/alarms/{hot.id}/edit"):
        assert client.get(url).status_code == 403, url
    assert client.post(f"/alarms/{hot.id}/delete").status_code == 403
    assert client.post(f"/alarms/{hot.id}/toggle").status_code == 403
    assert client.get("/alarms/history").status_code == 200


def test_history_requires_login(client, hot):
    """Anonymous visitors cannot see the history or the API."""
    assert client.get("/alarms/history").status_code == 302
    assert client.get("/api/alarms/active").status_code == 401


def _form(dashboards, **over):
    """Valid alarm form data.

    Args:
        dashboards: Fixture with points.
        **over: Overrides.

    Returns:
        Dict for POST.
    """
    data = {"name": "Low setpoint", "description": "Check", "datapoint_id": dashboards["points"]["setpoint"].id,
            "operator": "lt", "setpoint": "5", "severity": "low", "screen_id": dashboards["public"].id, "enabled": "y"}
    data.update(over)
    return data


def test_create_edit_delete_alarm(client, login_as, db, dashboards, hot):
    """CRUD with validation; deleting keeps the history."""
    login_as("admin")
    assert client.post("/alarms/new", data=_form(dashboards)).status_code == 302
    alarm = Alarm.query.filter_by(name="Low setpoint").one()
    assert alarm.severity == "low" and alarm.screen_id == dashboards["public"].id and alarm.enabled

    html = client.post("/alarms/new", data=_form(dashboards, setpoint="abc", name="X")).get_data(as_text=True)
    assert "Enter a number" in html
    html = client.post("/alarms/new", data=_form(dashboards)).get_data(as_text=True)
    assert "already exists" in html
    html = client.post("/alarms/new", data=_form(dashboards, name="B", datapoint_id=dashboards["points"]["run"].id,
                                                  operator="gt", setpoint="true")).get_data(as_text=True)
    assert "BOOL" in html
    html = client.post("/alarms/new", data=_form(dashboards, name="C", datapoint_id=0)).get_data(as_text=True)
    assert "Choose a tag" in html

    assert client.post(f"/alarms/{alarm.id}/edit", data=_form(dashboards, severity="high", screen_id=0)).status_code == 302
    db.session.refresh(alarm)
    assert alarm.severity == "high" and alarm.screen_id is None

    set_value(dashboards["points"]["temp"], 90)
    scan_alarms()
    ev_id = hot.open_event.id
    assert client.post(f"/alarms/{hot.id}/delete").status_code == 302
    assert db.session.get(Alarm, hot.id) is None
    ev = db.session.get(AlarmEvent, ev_id)
    assert ev is not None and ev.alarm_id is None and ev.close_type == "manual" and ev.name == "High temp"


def test_create_prefills_tag(client, login_as, dashboards):
    """``/alarms/new?tag=<id>`` preselects the tag."""
    login_as("admin")
    tag = dashboards["points"]["speed"]
    html = client.get(f"/alarms/new?tag={tag.id}").get_data(as_text=True)
    assert re.search(rf'<option selected value="{tag.id}">|<option value="{tag.id}" selected', html)


def test_toggle_and_disable_closes_event(client, login_as, db, dashboards, hot):
    """Disabling an active alarm closes its event manually."""
    set_value(dashboards["points"]["temp"], 90)
    scan_alarms()
    login_as("admin")
    ev = hot.open_event
    client.post(f"/alarms/{hot.id}/toggle")
    db.session.refresh(hot)
    db.session.refresh(ev)
    assert not hot.enabled and ev.close_type == "manual"
    client.post(f"/alarms/{hot.id}/toggle")
    db.session.refresh(hot)
    assert hot.enabled


def test_alarm_list_filters_sort_and_details(client, login_as, db, dashboards, hot):
    """List shows buttons, filters by severity/state/enabled and sorts by severity."""
    db.session.add_all([
        Alarm(name="Speed low", datapoint=dashboards["points"]["speed"], operator="lt", setpoint="1", severity="low"),
        Alarm(name="Speed high", datapoint=dashboards["points"]["speed"], operator="gt", setpoint="999",
              severity="high", enabled=False)])
    db.session.commit()
    set_value(dashboards["points"]["temp"], 90)
    scan_alarms()
    login_as("admin")
    html = client.get("/alarms/").get_data(as_text=True)
    assert 'id="add-alarm-btn"' in html and "Details" in html and "Edit" in html and "Delete" in html
    names = lambda h: re.findall(r'<td class="alarm-name"><strong>([^<]+)</strong>', h)  # noqa: E731
    assert names(client.get("/alarms/?severity=critical").get_data(as_text=True)) == ["High temp"]
    assert names(client.get("/alarms/?state=active").get_data(as_text=True)) == ["High temp"]
    assert names(client.get("/alarms/?enabled=no").get_data(as_text=True)) == ["Speed high"]
    assert names(client.get("/alarms/?sort=severity&dir=desc").get_data(as_text=True)) == \
        ["High temp", "Speed high", "Speed low"]
    assert names(client.get("/alarms/?q=boiler").get_data(as_text=True)) == ["High temp"]
    assert "1–1 of 1" in client.get("/alarms/?q=boiler&per_page=10").get_data(as_text=True)

    html = client.get(f"/alarms/{hot.id}").get_data(as_text=True)
    assert "Temp &gt; 80" in html and "Active" in html and "Boiler too hot" in html


# ---------------------------------------------------------------- events API
def test_active_api_ack_and_screen_url(client, login_as, db, dashboards, hot):
    """Viewers see active events, can acknowledge but not remove."""
    set_value(dashboards["points"]["temp"], 90)
    scan_alarms()
    login_as("viewer")
    data = client.get("/api/alarms/active").get_json()
    assert data["count"] == 1 and data["unacked"] == 1 and data["highest"] == "critical"
    assert data["color"] == "#dc3545" and data["can_remove"] is False
    ev = data["events"][0]
    assert ev["screen_url"] == f"/screens/private?alarm={ev['id']}" and ev["occurred_at"].endswith("+00:00")

    acked = client.post(f"/api/alarms/events/{ev['id']}/ack").get_json()
    assert acked["acked_at"] and acked["acked_by"] == "viewer"
    assert client.get("/api/alarms/active").get_json()["unacked"] == 0
    assert client.post(f"/api/alarms/events/{ev['id']}/remove").status_code == 403
    assert client.get(f"/api/alarms/events/{ev['id']}").get_json()["name"] == "High temp"
    assert client.get("/api/alarms/events/999").status_code == 404


def test_screen_url_hidden_without_access(client, login_as, db, dashboards, hot, users):
    """A user who may not open the screen gets no "Open Screen" link."""
    from app.models import Role  # noqa: PLC0415
    guest_role = Role.query.filter_by(name="Guest").one()
    assert users["guest"].role == guest_role
    set_value(dashboards["points"]["temp"], 90)
    scan_alarms()
    login_as("guest")
    ev = client.get("/api/alarms/active").get_json()["events"][0]
    assert ev["screen_url"] is None


def test_admin_flags_and_ui_markup(client, login_as, dashboards, hot):
    """Admins may remove; every page has the Events button and windows."""
    login_as("admin")
    assert client.get("/api/alarms/active").get_json()["can_remove"] is True
    html = client.get("/screens/").get_data(as_text=True)
    for marker in ('id="events-btn"', 'id="events-modal"', 'id="alarm-modal"', 'id="event-history-btn"',
                   'id="alarm-modal-open"', 'id="alarm-modal-ack-btn"', "js/alarms.js"):
        assert marker in html, marker


def test_anonymous_has_no_alarm_ui(client, dashboards):
    """The public screen shows no Events button for visitors."""
    html = client.get("/screens/public").get_data(as_text=True)
    assert 'id="events-btn"' not in html and 'id="hmi-alarms"' not in html


# ---------------------------------------------------------------- banner
def test_open_screen_shows_banner(client, login_as, db, dashboards, hot):
    """``?alarm=<id>`` shows the alarm at the top of grid and image screens."""
    set_value(dashboards["points"]["temp"], 90)
    scan_alarms()
    ev = hot.open_event
    login_as("admin")
    html = client.get(f"/screens/private?alarm={ev.id}").get_data(as_text=True)
    assert 'id="alarm-banner"' in html and "Boiler too hot" in html and f'data-banner-event="{ev.id}"' in html
    assert 'id="alarm-banner"' not in client.get("/screens/private").get_data(as_text=True)

    image = Dashboard(name="Plant", slug="plant", kind="image")
    db.session.add(image)
    db.session.commit()
    html = client.get(f"/screens/plant?alarm={ev.id}").get_data(as_text=True)
    assert "alarm-banner-overlay" in html


# ---------------------------------------------------------------- history
def test_history_filters_and_sort(client, login_as, db, dashboards, hot):
    """History lists closed and open events with closure type and filters."""
    temp = dashboards["points"]["temp"]
    set_value(temp, 90)
    scan_alarms()
    set_value(temp, 10)
    scan_alarms()                    # event 1 closed automatically
    set_value(temp, 90)
    scan_alarms()                    # event 2 open
    login_as("admin")
    html = client.get("/alarms/history").get_data(as_text=True)
    assert html.count('data-alarm-event="') == 2 and "Automatic" in html and "Active" in html
    assert client.get("/alarms/history?status=open").get_data(as_text=True).count('data-alarm-event="') == 1
    assert client.get("/alarms/history?status=auto").get_data(as_text=True).count('data-alarm-event="') == 1
    assert client.get("/alarms/history?status=manual").get_data(as_text=True).count('data-alarm-event="') == 0
    assert client.get("/alarms/history?severity=low").get_data(as_text=True).count('data-alarm-event="') == 0
    assert client.get("/alarms/history?from=2000-01-01&to=2000-01-02").get_data(as_text=True).count('data-alarm-event="') == 0
    assert client.get("/alarms/history?from=bad").get_data(as_text=True).count('data-alarm-event="') == 2
    ids = re.findall(r'data-alarm-event="(\d+)"', client.get("/alarms/history?sort=occurred&dir=asc").get_data(as_text=True))
    assert ids == sorted(ids, key=int)


def test_history_manual_close_shows_user(client, login_as, db, dashboards, hot):
    """Manual closures show the user's name."""
    set_value(dashboards["points"]["temp"], 90)
    scan_alarms()
    login_as("admin")
    client.post(f"/api/alarms/events/{hot.open_event.id}/remove")
    html = client.get("/alarms/history?status=manual").get_data(as_text=True)
    assert "Manual" in html and "admin" in html


# ---------------------------------------------------------------- screen/tag deletion
def test_screen_delete_keeps_alarm_tags(client, login_as, db, dashboards, hot):
    """Deleting a screen keeps its tags (no screen any more) and unlinks the alarm."""
    set_value(dashboards["points"]["temp"], 90)
    scan_alarms()
    login_as("admin")
    private = dashboards["private"]
    temp_id, speed_id, ev_id = dashboards["points"]["temp"].id, dashboards["points"]["speed"].id, hot.open_event.id
    resp = client.post(f"/screens/{private.id}/delete")
    assert resp.status_code == 302
    db.session.expire_all()
    assert db.session.get(Dashboard, private.id) is None
    tag = db.session.get(DataPoint, temp_id)
    assert tag is not None and tag.screens == []
    assert db.session.get(DataPoint, speed_id) is not None
    assert db.session.get(Alarm, hot.id).screen_id is None
    assert db.session.get(AlarmEvent, ev_id).screen_id is None


def test_point_delete_detaches_alarm_tag(client, login_as, db, dashboards, hot):
    """Removing an alarm tag from a screen keeps the tag in the tag table."""
    login_as("admin")
    temp = dashboards["points"]["temp"]
    client.post(f"/screens/{dashboards['private'].id}/points/{temp.id}/delete")
    db.session.expire_all()
    tag = db.session.get(DataPoint, temp.id)
    assert tag is not None and tag.screens == []


def test_loose_tag_alarm(db, plc):
    """Alarms work on tags that are on no screen."""
    tag = add_point(None, plc, "Loose", "MW50:INT")
    set_value(tag, 7)
    alarm = Alarm(name="Loose eq", datapoint=tag, operator="eq", setpoint="7", severity="low")
    db.session.add(alarm)
    db.session.commit()
    scan_alarms()
    assert alarm.is_active and alarm.open_event.screen_id is None
