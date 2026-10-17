"""Tests for the automations: validation, triggers, conditions, actions, modes, editor and YAML."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.automations import engine
from app.automations.documents import from_yaml, readable, to_yaml
from app.automations.schema import ConfigError, editor_schema, parse_duration, validate
from app.models import PLC, AlarmEvent, AuditLog, Automation, AutomationRun, DataPoint, Perm, Role
from app.models.alarm import Alarm
from app.plc.dummy import DummyAddress, DummyConnection
from app.plc.service import read_datapoints, write_datapoint
from tests.conftest import login, make_user

T0 = datetime(2026, 10, 19, 8, 0, 0, tzinfo=timezone.utc)   # a Monday


@pytest.fixture(autouse=True)
def _fresh(app, monkeypatch):
    """Clean engine memory and dummy values for every test; plant clock in UTC at T0."""
    DummyConnection.reset()
    engine.STATE.reset()
    engine._running.clear()
    engine._queued.clear()
    app.config["DISPLAY_TIMEZONE"] = "UTC"
    monkeypatch.setattr(engine, "utc_now", lambda: T0)
    yield
    DummyConnection.reset()
    engine.STATE.reset()


@pytest.fixture()
def tags(db):
    """A dummy PLC with a few writable tags: temp (REAL), pump (BOOL), mode (INT), text (STRING)."""
    plc = PLC(name="Sim", ip_address="", protocol="dummy")
    db.session.add(plc)
    db.session.commit()
    out = {}
    for slot, (label, dtype) in enumerate([("Temp", "REAL"), ("Pump", "BOOL"), ("Mode", "INT"),
                                           ("Text", "STRING")], start=1):
        dp = DataPoint(plc=plc, label=label, writable=True, dummy_scope="general",
                       true_text="RUN" if dtype == "BOOL" else "ON", false_text="STOP" if dtype == "BOOL" else "OFF")
        dp.set_address(DummyAddress(start=slot, data_type=dtype, string_length=10 if dtype == "STRING" else 0))
        db.session.add(dp)
        out[label.lower()] = dp
    ro = DataPoint(plc=plc, label="Readonly", writable=False, dummy_scope="general")
    ro.set_address(DummyAddress(start=9, data_type="INT"))
    db.session.add(ro)
    out["readonly"] = ro
    db.session.commit()
    out["plc"] = plc
    return out


def setv(dp, value):
    """Set a dummy tag value."""
    write_datapoint(dp, value)


def getv(dp):
    """Read a tag value."""
    return read_datapoints([dp])[0][dp.id]["value"]


def make(db, name="Auto", triggers=(), conditions=(), actions=(), allow=True, **kw) -> Automation:
    """Create a validated automation."""
    a = Automation(name=name, allow_writes=allow, **kw)
    a.config = validate({"triggers": list(triggers), "conditions": list(conditions), "actions": list(actions)})
    db.session.add(a)
    db.session.commit()
    return a


def runs(a):
    """Runs of an automation, oldest first."""
    return AutomationRun.query.filter_by(automation_id=a.id).order_by(AutomationRun.id).all()


def notify(msg="hi", target="log"):
    """A notification action."""
    return {"type": "notify", "message": msg, "target": target, "severity": "low"}


# ---------------------------------------------------------------------------- schema
def test_validation_errors(app, tags):
    """Every problem is reported with its path."""
    with pytest.raises(ConfigError) as exc:
        validate({"triggers": [{"type": "nope"}, {"type": "numeric_state", "tag": tags["temp"].id}],
                  "actions": [{"type": "write", "tag": tags["readonly"].id, "value": "1"},
                              {"type": "delay", "duration": "abc"}, {"type": "notify", "colour": "red"}]})
    text = " | ".join(exc.value.errors)
    assert "triggers[1]: unknown type 'nope'" in text
    assert "triggers[2]: set 'above', 'below' or both" in text
    assert "read-only" in text and "not a duration" in text
    assert "unknown field 'colour'" in text and "actions[3].message: is required" in text
    with pytest.raises(ConfigError, match="Add at least one action"):
        validate({"triggers": [], "actions": []})


def test_validation_normalises_and_resolves_names(app, tags):
    """Names become ids, durations seconds, times HH:MM:SS, numbers numbers."""
    cfg = validate({"triggers": [{"type": "numeric_state", "tag": "Temp", "above": "80", "for": "00:01:30"},
                                 {"type": "time", "at": "6:05", "weekdays": ["Monday", "fri"]}],
                    "conditions": [{"type": "or", "conditions": [{"type": "state", "tag": "Pump", "state": True}]}],
                    "actions": [{"type": "write", "tag": "Mode", "value": 3}, {"type": "delay", "duration": 2.5}]})
    assert cfg["triggers"][0] == {"type": "numeric_state", "tag": tags["temp"].id, "above": 80, "for": 90.0}
    assert cfg["triggers"][1]["at"] == "06:05:00" and cfg["triggers"][1]["weekdays"] == ["mon", "fri"]
    assert cfg["conditions"][0]["conditions"][0]["state"] == "true"
    assert cfg["actions"][0]["value"] == "3"
    assert parse_duration("1:30") == 90 and parse_duration({"minutes": 2}) == 120
    schema = editor_schema()
    assert set(schema) == {"triggers", "conditions", "actions", "weekdays"}
    assert schema["actions"]["write"]["fields"][0]["kind"] == "tag_writable"


def test_yaml_round_trip(app, db, tags):
    """YAML shows names; reading it back gives the same configuration."""
    a = make(db, triggers=[{"type": "state", "tag": tags["pump"].id, "to": "RUN", "for": 5}],
             actions=[{"type": "write", "tag": tags["mode"].id, "value": "2"}])
    text = to_yaml(readable(a.to_document()))
    assert "tag: Pump" in text and "for: '00:00:05'" in text
    doc = from_yaml(text)
    assert validate(doc) == a.config
    with pytest.raises(ConfigError, match="line"):
        from_yaml("triggers: [unclosed")


# ---------------------------------------------------------------------------- triggers
def test_numeric_trigger_crossing_and_for(app, db, tags):
    """Fires on crossing into the range, after 'for', once until it leaves the range."""
    setv(tags["temp"], 50)
    a = make(db, triggers=[{"type": "numeric_state", "tag": tags["temp"].id, "above": 80, "for": 10}],
             actions=[notify()])
    assert engine.tick(T0) == []                       # first look: remembers the state
    setv(tags["temp"], 85)
    assert engine.tick(T0 + timedelta(seconds=1)) == []   # in range, waiting 10 s
    assert engine.tick(T0 + timedelta(seconds=5)) == []
    fired = engine.tick(T0 + timedelta(seconds=12))
    assert len(fired) == 1 and "Temp" in fired[0][1]
    assert engine.tick(T0 + timedelta(seconds=20)) == []  # stays in range: no repeat
    setv(tags["temp"], 70)
    engine.tick(T0 + timedelta(seconds=21))
    setv(tags["temp"], 90)
    engine.tick(T0 + timedelta(seconds=22))
    setv(tags["temp"], 70)                                # left before 10 s: no fire
    engine.tick(T0 + timedelta(seconds=25))
    assert engine.tick(T0 + timedelta(seconds=40)) == []
    assert [r.status for r in runs(a)] == ["finished"]


def test_already_in_range_at_start_does_not_fire(app, db, tags):
    """A limit already exceeded when the engine starts waits for a new crossing."""
    setv(tags["temp"], 95)
    make(db, triggers=[{"type": "numeric_state", "tag": tags["temp"].id, "above": 80}], actions=[notify()])
    assert engine.tick(T0) == [] and engine.tick(T0 + timedelta(seconds=1)) == []


def test_state_trigger_from_to(app, db, tags):
    """Fires on changes matching from / to; ON/OFF texts of the tag work."""
    setv(tags["pump"], False)
    make(db, triggers=[{"type": "state", "tag": tags["pump"].id, "from": "STOP", "to": "RUN"}], actions=[notify()])
    engine.tick(T0)
    setv(tags["pump"], True)
    assert len(engine.tick(T0 + timedelta(seconds=1))) == 1
    setv(tags["pump"], False)
    assert engine.tick(T0 + timedelta(seconds=2)) == []   # RUN -> STOP does not match


def test_time_and_interval_triggers(app, db, tags):
    """Time of day with week days; fixed intervals."""
    make(db, name="Morning", triggers=[{"type": "time", "at": "08:00:30", "weekdays": ["mon"]}], actions=[notify()])
    make(db, name="Every", triggers=[{"type": "time_pattern", "every": 10, "unit": "seconds"}], actions=[notify()])
    engine.tick(T0)
    fired = engine.tick(T0 + timedelta(seconds=31))
    assert ("Morning", "Time 08:00:30") in fired and any(n == "Every" for n, _ in fired)
    assert [n for n, _ in engine.tick(T0 + timedelta(seconds=35))] == []
    assert [n for n, _ in engine.tick(T0 + timedelta(seconds=42))] == ["Every"]
    # Tuesday: no "Morning"
    engine.tick(T0 + timedelta(days=1))
    assert "Morning" not in [n for n, _ in engine.tick(T0 + timedelta(days=1, seconds=31))]


def test_alarm_and_startup_triggers(app, db, tags, users):
    """Alarm raised / acknowledged / cleared; server start; notifications are ignored."""
    alarm = Alarm(name="Hot", datapoint=tags["temp"], operator="gt", setpoint="80", severity="high")
    db.session.add(alarm)
    db.session.commit()
    make(db, name="Start", triggers=[{"type": "startup"}], actions=[notify()])
    make(db, name="Raised", triggers=[{"type": "alarm", "event": "raised", "alarm": alarm.id}], actions=[notify()])
    make(db, name="Acked", triggers=[{"type": "alarm", "event": "acknowledged", "severity": "high"}], actions=[notify()])
    make(db, name="Cleared", triggers=[{"type": "alarm", "event": "cleared"}], actions=[notify()])
    assert [n for n, _ in engine.tick(T0)] == ["Start"]
    ev = AlarmEvent(alarm=alarm, name="Hot", severity="high")
    db.session.add_all([ev, AlarmEvent(name="note", severity="low", source="automation")])
    db.session.commit()
    assert [n for n, _ in engine.tick(T0 + timedelta(seconds=1))] == ["Raised"]
    ev.acknowledge(users["admin"])
    db.session.commit()
    assert [n for n, _ in engine.tick(T0 + timedelta(seconds=2))] == ["Acked"]
    ev.close("auto")
    db.session.commit()
    assert [n for n, _ in engine.tick(T0 + timedelta(seconds=3))] == ["Cleared"]
    assert engine.tick(T0 + timedelta(seconds=4)) == []


def test_plc_trigger(app, db, tags, monkeypatch):
    """PLC offline / back online transitions."""
    from app.plc.driver import PLCConnectionManager  # noqa: PLC0415

    state = {"s": "online"}
    real = PLCConnectionManager.status
    monkeypatch.setattr(PLCConnectionManager, "status",
                        lambda self, plc: dict(real(self, plc), state=state["s"]))
    make(db, name="Down", triggers=[{"type": "plc", "plc": tags["plc"].id, "event": "offline"}], actions=[notify()])
    make(db, name="Up", triggers=[{"type": "plc", "plc": tags["plc"].id, "event": "online"}], actions=[notify()])
    engine.tick(T0)
    state["s"] = "unstable"
    assert engine.tick(T0 + timedelta(seconds=1)) == []
    state["s"] = "offline"
    assert [n for n, _ in engine.tick(T0 + timedelta(seconds=2))] == ["Down"]
    state["s"] = "online"
    assert [n for n, _ in engine.tick(T0 + timedelta(seconds=3))] == ["Up"]


def test_disabled_trigger_and_automation(app, db, tags):
    """Disabled triggers and disabled automations never fire."""
    make(db, name="A", triggers=[{"type": "startup", "enabled": False}], actions=[notify()])
    make(db, name="B", triggers=[{"type": "startup"}], actions=[notify()], enabled=False)
    assert engine.tick(T0) == []


# ---------------------------------------------------------------------------- conditions and actions
def test_conditions_and_write_actions(app, db, tags):
    """Conditions gate the run; write, if/else, repeat and notify run in order."""
    setv(tags["mode"], 1)
    setv(tags["pump"], False)
    a = make(db, triggers=[{"type": "startup"}],
             conditions=[{"type": "and", "conditions": [
                 {"type": "state", "tag": tags["mode"].id, "state": "1"},
                 {"type": "not", "conditions": [{"type": "state", "tag": tags["pump"].id, "state": "RUN"}]}]},
                 {"type": "time", "after": "07:00", "before": "09:00"}],
             actions=[{"type": "write", "tag": tags["pump"].id, "value": "RUN"},
                      {"type": "if", "conditions": [{"type": "numeric_state", "tag": tags["mode"].id, "above": 5}],
                       "then": [{"type": "write", "tag": tags["text"].id, "value": "high"}],
                       "else": [{"type": "write", "tag": tags["text"].id, "value": "low"}]},
                      {"type": "repeat", "count": 3, "actions": [{"type": "notify", "message": "x", "target": "log"}]},
                      {"type": "notify", "message": "Started by {trigger}", "severity": "high"}])
    engine.tick(T0)
    assert getv(tags["pump"]) is True and getv(tags["text"]) == "low"
    run = runs(a)[-1]
    assert run.status == "finished", run.error
    assert sum(1 for s in run.trace if s["path"].startswith("action/2/") and s["kind"] == "action") >= 3
    note = AlarmEvent.query.filter_by(source="automation").one()
    assert note.description == "Started by Server started" and note.severity == "high" and note.is_open
    audit = AuditLog.query.filter_by(action="write").all()
    assert {e.username for e in audit} == {"Automation: Auto"} and len(audit) == 2
    # second run: condition false (pump runs now)
    engine.trigger(a, "again")
    assert runs(a)[-1].status == "condition"


def test_notification_closes_on_ack(app, db, users):
    """Automation notifications close when acknowledged."""
    ev = AlarmEvent(name="Auto", description="msg", severity="low", source="automation")
    db.session.add(ev)
    db.session.commit()
    ev.acknowledge(users["admin"])
    assert not ev.is_open and ev.close_type == "auto"


def test_write_needs_approval_and_writable_tag(app, db, tags):
    """Without the administrator's approval the write is refused and the run fails."""
    setv(tags["mode"], 1)
    a = make(db, actions=[{"type": "write", "tag": tags["mode"].id, "value": "7"}], allow=False)
    engine.trigger(a, "test")
    run = runs(a)[-1]
    assert run.status == "error" and getv(tags["mode"]) == 1
    assert any("not approved" in s["error"] for s in run.trace)
    # a tag made read-only after saving is refused too
    a.allow_writes = True
    tags["mode"].writable = False
    db.session.commit()
    engine.trigger(a, "test")
    assert runs(a)[-1].status == "error" and getv(tags["mode"]) == 1


def test_wait_condition_stop_and_errors(app, db, tags):
    """Wait until with timeout, condition action, stop action, invalid value."""
    setv(tags["mode"], 0)
    a = make(db, name="W", actions=[{"type": "wait", "conditions": [{"type": "state", "tag": tags["mode"].id, "state": "5"}],
                                     "timeout": 0.2, "continue_on_timeout": True},
                                    {"type": "condition", "conditions": [{"type": "state", "tag": tags["mode"].id, "state": "5"}]},
                                    notify()])
    engine.trigger(a, "t")
    assert runs(a)[-1].status == "condition"
    b = make(db, name="S", actions=[{"type": "stop", "reason": "done"}, notify()])
    engine.trigger(b, "t")
    run = runs(b)[-1]
    assert run.status == "finished" and not any(s["path"] == "action/1" for s in run.trace)
    c = make(db, name="E", actions=[{"type": "write", "tag": tags["mode"].id, "value": "abc"}])
    engine.trigger(c, "t")
    assert runs(c)[-1].status == "error" and "not a number" in runs(c)[-1].error


def test_other_automation_actions(app, db, tags):
    """Run / disable another automation; loops are limited."""
    target = make(db, name="Target", actions=[{"type": "write", "tag": tags["mode"].id, "value": "9"}])
    caller = make(db, name="Caller", actions=[{"type": "automation", "automation": target.id, "action": "run"},
                                              {"type": "automation", "automation": target.id, "action": "disable"}])
    engine.trigger(caller, "t")
    assert getv(tags["mode"]) == 9 and not db.session.get(Automation, target.id).enabled
    assert runs(target)[-1].trigger == "Called by automation 'Caller'"
    with pytest.raises(ConfigError, match="cannot call itself"):
        from app.automations.schema import Lookup  # noqa: PLC0415

        validate({"actions": [{"type": "automation", "automation": caller.id}]}, Lookup(exclude_automation=caller.id))


def test_modes(app, db, tags):
    """single skips while running; queued and parallel follow max_runs; restart stops the old run."""
    a = make(db, name="M", actions=[notify()], mode="single")
    handle = engine.RunHandle(a.id)
    engine._running[a.id].append(handle)        # pretend a run is in progress
    assert engine.trigger(a, "x") == "skipped" and runs(a)[-1].status == "skipped"
    a.mode, a.max_runs = "queued", 2
    db.session.commit()
    assert engine.trigger(a, "q1") == "queued" and engine.trigger(a, "q2") == "skipped"
    a.mode = "restart"
    db.session.commit()
    assert engine.trigger(a, "r") == "restarted" and handle.stop.is_set()
    engine._running[a.id].remove(handle)
    engine._finish(app, handle)                  # the queued run starts now
    assert "q1" in [r.trigger for r in runs(a)]
    assert engine.stop_runs(a.id) == 0


def test_runs_are_pruned(app, db, tags):
    """Only the newest runs are kept."""
    a = make(db, actions=[notify()])
    for i in range(engine.KEEP_RUNS + 5):
        engine.trigger(a, f"r{i}")
    assert AutomationRun.query.filter_by(automation_id=a.id).count() == engine.KEEP_RUNS


# ---------------------------------------------------------------------------- pages
def test_permission(app, client, login_as):
    """Only users with manage_automations open the pages; Admin has it."""
    assert Perm.MANAGE_AUTOMATIONS in Role.query.filter_by(name="Admin").one().permission_codes
    login_as("viewer")
    assert client.get("/automations/").status_code == 403
    assert client.post("/automations/save", json={}).status_code == 403


def test_editor_save_and_approval(app, client, db, tags, users):
    """Create in the editor; approval only by admins; non-admin changes to writes withdraw it."""
    login(client, "admin")
    assert client.get("/automations/new").status_code == 200
    doc = {"name": "Cool", "mode": "single", "enabled": True,
           "triggers": [{"type": "numeric_state", "tag": tags["temp"].id, "above": 80}],
           "conditions": [], "actions": [{"type": "write", "tag": tags["pump"].id, "value": "STOP"}]}
    r = client.post("/automations/save", json={"id": None, "doc": doc, "allow_writes": True}).get_json()
    assert r["ok"] and r["created"]
    a = db.session.get(Automation, r["id"])
    assert a.allow_writes and a.writes_tags()
    page = client.get(f"/automations/{a.id}/edit").get_data(as_text=True)
    assert 'id="auto-data"' in page and "Then do" in page
    assert AuditLog.query.filter_by(object_type="Automation", action="create").count() == 1
    # errors come back as a list
    bad = client.post("/automations/save", json={"id": a.id, "doc": dict(doc, actions=[])}).get_json()
    assert bad["ok"] is False and "Add at least one action." in bad["errors"]
    # an engineer (not admin) with manage_automations
    role = Role(name="Engineer")
    role.set_permissions([Perm.MANAGE_AUTOMATIONS, Perm.VIEW_SCREENS])
    db.session.add(role)
    db.session.commit()
    make_user("eng", "Engineer")
    client.post("/auth/logout")
    login(client, "eng")
    same = client.post("/automations/save", json={"id": a.id, "doc": dict(doc, description="x"), "allow_writes": False}).get_json()
    assert same["ok"] and db.session.get(Automation, a.id).allow_writes      # writes unchanged: kept
    changed = dict(doc, actions=[{"type": "write", "tag": tags["pump"].id, "value": "RUN"}])
    r2 = client.post("/automations/save", json={"id": a.id, "doc": changed}).get_json()
    db.session.expire_all()
    assert not db.session.get(Automation, a.id).allow_writes
    assert any("approve writing again" in n for n in r2["notes"])


def test_yaml_endpoints_and_save(app, client, users, db, tags):
    """to-yaml / from-yaml and saving YAML text."""
    login(client, "admin")
    y = client.post("/automations/api/to-yaml", json={"doc": {"name": "Y", "triggers": [{"type": "startup"}],
                    "actions": [{"type": "write", "tag": tags["mode"].id, "value": "1"}]}}).get_json()["yaml"]
    assert "tag: Mode" in y
    back = client.post("/automations/api/from-yaml", json={"yaml": y}).get_json()
    assert back["ok"] and back["doc"]["actions"][0]["tag"] == tags["mode"].id
    err = client.post("/automations/api/from-yaml", json={"yaml": "name: X\nactions:\n  - type: fly\n"}).get_json()
    assert not err["ok"] and "unknown type 'fly'" in " ".join(err["errors"])
    saved = client.post("/automations/save", json={"yaml": y}).get_json()
    assert saved["ok"] and not db.session.get(Automation, saved["id"]).allow_writes


def test_list_toggle_run_runs_delete(app, client, users, db, tags):
    """List page, enable/disable, run now, runs page, export, delete."""
    a = make(db, name="Listed", triggers=[{"type": "startup"}], actions=[notify("hello")])
    login(client, "admin")
    page = client.get("/automations/").get_data(as_text=True)
    assert "Listed" in page and 'id="automation-table"' in page
    client.post(f"/automations/{a.id}/toggle")
    assert not db.session.get(Automation, a.id).enabled
    r = client.post(f"/automations/{a.id}/run", json={}).get_json()
    assert r["result"] == "started"
    run = runs(a)[-1]
    assert run.status == "finished" and run.trigger == "Run manually by admin"
    html = client.get(f"/automations/{a.id}/runs").get_data(as_text=True)
    assert 'id="run-trace"' in html and "hello" in html
    text = client.get(f"/automations/{a.id}/export.yaml").get_data(as_text=True)
    assert "name: Listed" in text
    client.post(f"/automations/{a.id}/delete")
    assert db.session.get(Automation, a.id) is None and AutomationRun.query.count() == 0


def test_import(app, client, users, db, tags):
    """Import a list: all or nothing; names get numbers; not approved."""
    make(db, name="Dup", actions=[notify()])
    login(client, "admin")
    good = to_yaml([{"name": "Dup", "actions": [{"type": "write", "tag": "Mode", "value": "1"}]},
                    {"name": "New one", "triggers": [{"type": "startup"}], "actions": [notify()]}])
    import io  # noqa: PLC0415

    client.post("/automations/import", data={"file": (io.BytesIO(good.encode()), "a.yaml")},
                content_type="multipart/form-data")
    names = {a.name for a in Automation.query}
    assert {"Dup", "Dup (2)", "New one"} <= names
    assert not Automation.query.filter_by(name="Dup (2)").one().allow_writes
    bad = to_yaml([{"name": "X1", "actions": [notify()]}, {"name": "X2", "actions": [{"type": "warp"}]}])
    page = client.post("/automations/import", data={"file": (io.BytesIO(bad.encode()), "b.yaml")},
                       content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "Nothing was imported" in page and Automation.query.filter_by(name="X1").first() is None


def test_navigation_and_search(app, client, users, tags):
    """The Admin menu, the sidebar items and the quick search list Automations."""
    login(client, "admin")
    assert 'id="nav-automations"' in client.get("/screens/").get_data(as_text=True)
    results = client.get("/api/search?q=autom").get_json()["results"]
    assert any(r["label"] == "Automations" for r in results)
