"""All callers share the reads of a PLC (screens, alarms, history, automations)."""

from __future__ import annotations

import time

import pytest

from app.plc.driver import SimulatedConnection
from app.plc.service import read_datapoints, write_datapoint
from tests.conftest import add_point


@pytest.fixture()
def reads(monkeypatch):
    """Count the reads that reach the simulated PLC (``reads["n"]``)."""
    real = SimulatedConnection.read
    state = {"n": 0}

    def read(self, area, db_number, start, size):
        state["n"] += 1
        return real(self, area, db_number, start, size)

    SimulatedConnection.reset()
    monkeypatch.setattr(SimulatedConnection, "read", read)
    return state


@pytest.fixture()
def tags(db, plc):
    """Three INT tags in one DB (one block read covers them all)."""
    return [add_point(None, plc, f"T{i}", f"DB1.DBW{2 * i}:INT", writable=True) for i in range(3)]


def _page(app, points):
    with app.test_request_context("/"):
        return read_datapoints(points)[0]


def test_identical_screen_polls_share_one_read(app, tags, reads):
    for _ in range(5):                 # five wall displays showing the same screen
        values = _page(app, tags[:2])
        assert all(v["quality"] == "good" for v in values.values())
    assert reads["n"] == 1


def test_a_read_covers_the_tags_of_every_recent_caller(app, tags, reads):
    _page(app, [tags[0]])               # screen A
    _page(app, [tags[1]])               # screen B: its read also includes A's tag
    assert reads["n"] == 2
    _page(app, [tags[0]])               # A again: already read with B's read
    _page(app, [tags[1]])
    assert reads["n"] == 2


def test_fresh_reads_never_use_an_earlier_read(app, tags, reads):
    _page(app, tags)
    read_datapoints(tags)               # background worker (fresh)
    assert reads["n"] == 2


def test_values_are_shared_only_for_a_short_time(app, tags, reads):
    app.config["PLC_SHARE_AGE"] = 0.1
    _page(app, tags)
    time.sleep(0.15)
    _page(app, tags)
    assert reads["n"] == 2


def test_a_write_drops_the_shared_values(app, tags, reads):
    before = _page(app, [tags[0]])[tags[0].id]["value"]
    with app.test_request_context("/"):
        write_datapoint(tags[0], before + 7)
    assert _page(app, [tags[0]])[tags[0].id]["value"] == before + 7


def test_tags_nobody_asks_for_leave_the_shared_read(app, tags, reads):
    from app.plc import service
    from app.plc.driver import get_manager

    app.config["PLC_INTEREST_TTL"] = 0.1
    _page(app, [tags[0]])
    time.sleep(0.15)
    _page(app, [tags[1]])
    interest = service._state(get_manager())["interest"][tags[1].plc_id]
    assert set(interest) == {tags[1].id}


def test_changed_connection_settings_are_not_answered_from_the_old_device(app, db, plc, tags, reads):
    _page(app, tags)
    plc.ip_address = "10.0.0.254"       # simulated offline device
    db.session.commit()
    values = _page(app, tags)
    assert all(v["quality"] == "bad" for v in values.values())
