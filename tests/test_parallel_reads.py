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

