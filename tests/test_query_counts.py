"""Hot paths run a fixed number of database queries, whatever the number of alarms or screens."""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import event

from app.alarms.engine import scan_alarms
from app.models import Alarm, Dashboard
from tests.conftest import add_point


@contextmanager
def count_queries(db):
    """Count the SQL statements run inside the block (``counter["n"]``)."""
    counter = {"n": 0}

    def before(*_args):
        counter["n"] += 1

    event.listen(db.engine, "before_cursor_execute", before)
    try:
        yield counter
    finally:
        event.remove(db.engine, "before_cursor_execute", before)


def _alarms(db, plc, n, start):
    for i in range(start, start + n):
        dp = add_point(None, plc, f"T{i}", f"DB1.DBW{2 * i}:INT")
        db.session.add(Alarm(name=f"A{i}", datapoint=dp, operator="ge", setpoint="0"))
    db.session.commit()


def _scan_queries(db):
    db.session.expire_all()
    with count_queries(db) as counter:
        scan_alarms()
    return counter["n"]


def test_alarm_scan_queries_do_not_grow_with_the_alarms(app, db, plc):
    _alarms(db, plc, 2, 0)
    scan_alarms()                       # raise the events once
    few = _scan_queries(db)
    _alarms(db, plc, 10, 2)
    scan_alarms()
    many = _scan_queries(db)
    assert many == few


def _screens(db, plc, n, start):
    for i in range(start, start + n):
        screen = Dashboard(name=f"S{i}", slug=f"s{i}", is_public=True)
        db.session.add(screen)
        db.session.flush()
        add_point(screen, plc, f"V{i}", f"DB2.DBW{2 * i}:INT")
    db.session.commit()


def _overview_queries(client, db):
    db.session.expire_all()
    with count_queries(db) as counter:
        assert client.get("/api/overview").status_code == 200
    return counter["n"]


def test_overview_queries_do_not_grow_with_the_screens(client, login_as, db, plc):
    login_as("admin")
    _screens(db, plc, 2, 0)
    few = _overview_queries(client, db)
    _screens(db, plc, 8, 2)
    many = _overview_queries(client, db)
    assert many == few
