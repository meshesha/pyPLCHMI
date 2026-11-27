"""PLCs are read in parallel: a slow PLC or device does not delay the others."""

from __future__ import annotations

import time

import pytest

from app.models import PLC, DataPoint
from app.plc.addressing import parse_address
from app.plc.driver import SimulatedConnection, get_manager
from app.plc.service import read_datapoints, read_plcs


@pytest.fixture()
def slow_db9(monkeypatch):
    """Simulated S7 reads of DB9 take 1.5 s (the "slow PLC"); other DBs answer at once.

    Returns:
        A dict with the number of DB9 reads (``calls``).
    """
    real = SimulatedConnection.read
    state = {"calls": 0}

    def read(self, area, db_number, start, size):
        if db_number == 9:
            state["calls"] += 1
            time.sleep(1.5)
        return real(self, area, db_number, start, size)

    SimulatedConnection.reset()
    monkeypatch.setattr(SimulatedConnection, "read", read)
    return state


def _plc_with_tag(db, name, address):
    """A PLC with one INT tag at ``address``."""
    plc = PLC(name=name, ip_address="10.0.0.1", rack=0, slot=1, enabled=True)
    db.session.add(plc)
    db.session.flush()
    dp = DataPoint(plc=plc, label=name + "_t", widget="value", unit="", scale=1.0, offset=0.0, writable=False,
                   width=1, decimals=0)
    dp.set_address(parse_address(address, "INT"))
    db.session.add(dp)
    db.session.commit()
    return plc, dp


def test_page_read_does_not_wait_for_a_slow_plc(app, db, slow_db9):
    fast_plc, fast = _plc_with_tag(db, "Fast", "DB1.DBW0")
    slow_plc, slow = _plc_with_tag(db, "Slow", "DB9.DBW0")
    with app.test_request_context("/"):
        started = time.monotonic()
        values, _ = read_datapoints([fast, slow], max_wait=0.3)
        assert time.monotonic() - started < 1.0
        assert values[fast.id]["quality"] == "good"
        assert values[slow.id]["quality"] == "bad" and "busy" in values[slow.id]["error"]

        # The slow read went on in the background; its value is shown (stale) on the next call.
        assert _wait(lambda: slow_db9["calls"] >= 1 and not _running(slow_plc))
        values, _ = read_datapoints([fast, slow], max_wait=0.3)
        assert values[slow.id]["quality"] == "good" and values[slow.id]["stale"]


def test_no_second_read_while_one_is_running(app, db, slow_db9):
    slow_plc, slow = _plc_with_tag(db, "Slow", "DB9.DBW0")
    groups = {slow_plc.id: (slow_plc, {slow.id: slow.s7_address})}
    read_plcs(groups, max_wait=0.1)
    read_plcs(groups, max_wait=0.1)
    read_plcs(groups, max_wait=0.1)
    assert _wait(lambda: not _running(slow_plc))
    assert slow_db9["calls"] == 1


def test_background_workers_wait_for_every_plc(app, db, slow_db9):
    """Outside a web request (alarms, history, automations) the default is to wait for the values."""
    _, slow = _plc_with_tag(db, "Slow", "DB9.DBW0")
    values, _ = read_datapoints([slow])
    assert values[slow.id]["quality"] == "good" and not values[slow.id]["stale"]


def _running(plc) -> bool:
    """``True`` while a read of ``plc`` is still running in the pool."""
    from app.plc import service

    future = service._running.get(plc.id)
    return future is not None and not future.done()


def _wait(condition, timeout=5.0) -> bool:
    """Wait until ``condition()`` is true (or the time is over)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False



# --- fresh reads: background workers and write read-backs never answer from the cache -----------------
@pytest.fixture()
def slowish_db9(monkeypatch):
    """Simulated S7 reads of DB9 take 0.4 s; other DBs answer at once."""
    real = SimulatedConnection.read

    def read(self, area, db_number, start, size):
        if db_number == 9:
            time.sleep(0.4)
        return real(self, area, db_number, start, size)

    SimulatedConnection.reset()
    monkeypatch.setattr(SimulatedConnection, "read", read)


def test_history_sample_is_not_skipped_while_a_screen_reads_the_plc(app, db, slowish_db9):
    """A screen refresh of the same PLC must not make the history logger skip its sample."""
    from app.history import logger as hl
    from app.models import TagSample

    plc, screen_tag = _plc_with_tag(db, "Slow", "DB9.DBW0")
    logged = DataPoint(plc=plc, label="logged", widget="value", unit="", scale=1.0, offset=0.0, writable=False,
                       width=1, decimals=0, log_enabled=True, log_interval=10)
    logged.set_address(parse_address("DB9.DBW2", "INT"))
    db.session.add(logged)
    db.session.commit()
    hl.reset_schedule()
    with app.test_request_context("/"):
        read_datapoints([screen_tag], max_wait=0)     # a screen poll is now running (other tags)
    assert _running(plc)
    stats = hl.sample_due_tags(now=1_800_000_000)
    assert stats["saved"] == 1 and stats["skipped"] == 0
    assert TagSample.query.filter_by(datapoint_id=logged.id).count() == 1


def test_write_read_back_is_not_the_value_before_the_write(app, db, slowish_db9, monkeypatch):
    """A screen poll that starts right after a write must not answer the write's read-back."""
    from app.plc.service import write_datapoint

    plc, tag = _plc_with_tag(db, "Slow", "DB9.DBW0")
    tag.writable = True
    db.session.commit()
    manager = get_manager()
    with app.test_request_context("/"):
        assert read_datapoints([tag], max_wait=None)[0][tag.id]["quality"] == "good"   # value cached
        real_write = manager.write

        def write_then_poll(plc_, address, raw):
            real_write(plc_, address, raw)
            read_plcs({plc.id: (plc, {tag.id: tag.s7_address})}, max_wait=0)   # another browser polls

        monkeypatch.setattr(manager, "write", write_then_poll)
        result = write_datapoint(tag, 42)
    assert result["value"] == 42 and not result["stale"]


def test_alarm_scan_reads_plcs_in_parallel(app, db, slowish_db9):
    """The alarm scan no longer reads one PLC after the other."""
    from app.alarms.engine import scan_alarms
    from app.models import Alarm

    _, a = _plc_with_tag(db, "A", "DB9.DBW0")
    _, b = _plc_with_tag(db, "B", "DB9.DBW0")
    _, c = _plc_with_tag(db, "C", "DB9.DBW0")
    db.session.add_all([Alarm(name=f"al{dp.id}", datapoint=dp, operator="ge", setpoint="0") for dp in (a, b, c)])
    db.session.commit()
    started = time.monotonic()
    stats = scan_alarms()
    assert time.monotonic() - started < 1.0       # 3 × 0.4 s one after the other would be 1.2 s
    assert stats["raised"] == 3 and stats["unreadable"] == 0


def test_finished_reads_are_forgotten(app, db):
    """Finished reads do not stay in the bookkeeping of running reads."""
    from app.plc import service

    plc, tag = _plc_with_tag(db, "P", "DB1.DBW0")
    read_datapoints([tag])
    assert _wait(lambda: plc.id not in service._running)
