"""The alarm engine: evaluates enabled alarms and maintains alarm events.

:func:`scan_alarms` reads every tag used by an enabled alarm (one request
per PLC, batched by the connection manager), evaluates the conditions and:

* opens an :class:`~app.models.alarm.AlarmEvent` when a condition becomes true;
* closes the open event automatically (``close_type="auto"``) when the
  condition is no longer true;
* leaves events unchanged while a tag cannot be read (bad quality), so a
  communication hiccup neither raises nor clears alarms;
* after a manual removal (``suppressed``) waits until the condition has gone
  false once before the alarm can fire again.

Communication alarms (``kind="comm"``) are active while their PLC is
*offline* – unreachable for longer than its ``offline_delay`` (see
:meth:`app.plc.driver.PLCConnectionManager.status`).  So that this is
noticed even when no screen is open, every scan contacts each watched PLC
(by reading one of its tags, or by opening the connection if it has none).

:class:`AlarmScanner` runs :func:`scan_alarms` every ``ALARM_SCAN_INTERVAL``
seconds in a background thread so alarms are detected even when no browser is
open.  :func:`scan_if_due` lets the events API trigger a scan as a fallback
(throttled, and serialised with the thread by a lock).
"""

from __future__ import annotations

import logging
import threading
import time

from flask import current_app

from app.audit.recorder import paused as audit_paused
from app.extensions import db
from app.models.alarm import Alarm, AlarmEvent
from app.models.dashboard import DataPoint

log = logging.getLogger(__name__)

_scan_lock = threading.Lock()
_last_scan = {"t": 0.0}


def _apply(alarm: Alarm, active: bool, value_text: str, stats: dict) -> None:
    """Open or close the alarm's event according to its current condition.

    Args:
        alarm: The alarm.
        active: Whether the condition is true now.
        value_text: Value shown in the event (tag value or communication error).
        stats: Counters to update.
    """
    event = alarm.open_event
    if active:
        if event is None and not alarm.suppressed:
            db.session.add(AlarmEvent(
                alarm=alarm, name=alarm.name, description=alarm.description, severity=alarm.severity,
                condition=alarm.condition_text[:200], value=str(value_text)[:100], screen_id=alarm.screen_id,
            ))
            stats["raised"] += 1
    else:
        alarm.suppressed = False
        if event is not None:
            event.close("auto")
            stats["cleared"] += 1


def _read_values(points: dict, stats: dict) -> dict:
    """Read the tags of the alarms, one PLC at a time.

    A PLC whose read fails with an unexpected error only makes *its* tags
    unreadable; the alarms of every other PLC are still evaluated.

    Args:
        points: ``datapoint id -> DataPoint``.
        stats: Counters; ``errors`` collects ``"PLC: message"`` texts.

    Returns:
        ``datapoint id -> value dict`` (see :func:`~app.plc.service.read_datapoints`).
    """
    from app.plc.service import read_datapoints  # noqa: PLC0415

    by_plc: dict = {}
    for dp in points.values():
        by_plc.setdefault(dp.plc_id, []).append(dp)
    values: dict = {}
    for group in by_plc.values():
        try:
            values.update(read_datapoints(group)[0])
        except Exception as exc:  # noqa: BLE001 - isolate one PLC's failure
            name = group[0].plc.name if group[0].plc else "?"
            log.exception("alarm scan: reading PLC %s failed", name)
            stats["errors"].append(f"{name}: {exc}")
    return values


def scan_alarms() -> dict:
    """Evaluate all enabled alarms once and update their events.

    Must run inside an application context.

    Returns:
        Counters ``{"checked", "raised", "cleared", "unreadable", "errors"}``.
    """
    from app.plc.driver import get_manager  # noqa: PLC0415

    stats = {"checked": 0, "raised": 0, "cleared": 0, "unreadable": 0, "errors": []}
    started = time.monotonic()
    with _scan_lock, audit_paused():
        manager = get_manager()
        alarms = Alarm.query.filter_by(enabled=True).all()
        # Local dummy tags have no server-side value: their alarms are never evaluated.
        tag_alarms = [a for a in alarms if not a.is_comm and a.datapoint is not None and not a.datapoint.is_local]
        comm_alarms = [a for a in alarms if a.is_comm and a.plc is not None]
        points = {a.datapoint_id: a.datapoint for a in tag_alarms}

        # Make sure every PLC with a communication alarm is contacted.
        covered = {dp.plc_id for dp in points.values()}
        for alarm in comm_alarms:
            plc = alarm.plc
            if not plc.enabled or plc.id in covered:
                continue
            covered.add(plc.id)
            probe = plc.datapoints.order_by(DataPoint.id).first()
            if probe is not None:
                points[probe.id] = probe
            else:
                try:
                    manager.probe(plc)
                except Exception as exc:  # noqa: BLE001
                    stats["errors"].append(f"{plc.name}: {exc}")

        values = _read_values(points, stats) if points else {}
        for alarm in tag_alarms:
            stats["checked"] += 1
            reading = values.get(alarm.datapoint_id)
            if reading is None or reading["quality"] != "good":
                stats["unreadable"] += 1
                continue
            try:
                _apply(alarm, alarm.evaluate(reading["value"]), reading["display"], stats)
            except Exception as exc:  # noqa: BLE001
                log.exception("alarm scan: alarm %s failed", alarm.name)
                stats["errors"].append(f"{alarm.name}: {exc}")
        for alarm in comm_alarms:
            stats["checked"] += 1
            try:
                status = manager.status(alarm.plc)
                _apply(alarm, status["state"] == "offline", status["error"] or "no answer", stats)
            except Exception as exc:  # noqa: BLE001
                log.exception("alarm scan: alarm %s failed", alarm.name)
                stats["errors"].append(f"{alarm.name}: {exc}")
        db.session.commit()
        _last_scan["t"] = time.monotonic()
        _record(stats, time.monotonic() - started)
    if stats["raised"] or stats["cleared"]:
        log.info("Alarm scan: %d raised, %d cleared", stats["raised"], stats["cleared"])
    return stats


#: State of the alarm scanning shown on the Alarms page (see :func:`scanner_status`).
_status = {"at": None, "duration": 0.0, "stats": None, "error": None, "error_at": None}


def _record(stats: dict, duration: float) -> None:
    """Remember the result of a finished scan.

    Args:
        stats: Scan counters.
        duration: Seconds the scan took.
    """
    _status.update(at=time.time(), duration=duration, stats=dict(stats))
    if stats["errors"]:
        _status.update(error="; ".join(stats["errors"])[:500], error_at=time.time())


def _record_failure(exc: BaseException) -> None:
    """Remember a scan that failed completely.

    Args:
        exc: The exception.
    """
    _status.update(error=f"{type(exc).__name__}: {exc}"[:500], error_at=time.time())


def scanner_status() -> dict:
    """Health of the alarm scanning for the Alarms page.

    Returns:
        Dict with ``thread`` (``running`` / ``stopped`` / ``off``), ``age``
        (seconds since the last finished scan, ``None`` if none), ``duration``,
        ``stats`` of the last scan, ``error`` and ``error_age`` (last problem,
        if any), and ``interval``.
    """
    now = time.time()
    enabled = current_app.config.get("ALARM_SCANNER", True)
    thread = AlarmScanner.thread
    state = "off" if not enabled else ("running" if thread is not None and thread.is_alive() else "stopped")
    return {
        "thread": state,
        "age": None if _status["at"] is None else round(now - _status["at"], 1),
        "duration": round(_status["duration"], 2),
        "stats": _status["stats"],
        "error": _status["error"],
        "error_age": None if _status["error_at"] is None else round(now - _status["error_at"]),
        "interval": float(current_app.config.get("ALARM_SCAN_INTERVAL", 2.0)),
    }


def scan_if_due() -> None:
    """Run a scan when the last one is older than the scan interval.

    Used by the events API as a fallback when the background scanner is not
    running (e.g. in tests or with several worker processes).  Errors are
    logged, never raised to the caller.
    """
    interval = current_app.config.get("ALARM_SCAN_INTERVAL", 2.0)
    if time.monotonic() - _last_scan["t"] < interval or _scan_lock.locked():
        return  # recent enough, or a scan is running right now (never block the request)
    try:
        scan_alarms()
    except Exception as exc:  # noqa: BLE001
        _record_failure(exc)
        db.session.rollback()
        log.exception("alarm scan failed")


class AlarmScanner:
    """Background thread calling :func:`scan_alarms` periodically."""

    #: The scanner thread of this process (``None`` before the first request).
    thread: threading.Thread | None = None
    _lock = threading.Lock()

    @classmethod
    def start(cls, app) -> bool:
        """Start the scanner thread, or restart it if it has stopped.

        Called before every request, so a thread that died is replaced
        automatically.

        Args:
            app: The Flask application (used for the app context).

        Returns:
            ``True`` if this call started a thread.
        """
        if cls.thread is not None and cls.thread.is_alive():
            return False
        with cls._lock:
            if cls.thread is not None and cls.thread.is_alive():
                return False
            restarted = cls.thread is not None
            interval = float(app.config.get("ALARM_SCAN_INTERVAL", 2.0))
            cls.thread = threading.Thread(target=cls._run, args=(app, interval), name="alarm-scanner", daemon=True)
            cls.thread.start()
        if restarted:
            log.warning("Alarm scanner thread had stopped – restarted")
        else:
            log.info("Alarm scanner started (every %.1f s)", interval)
        return True

    @staticmethod
    def _run(app, interval: float) -> None:
        """Thread body: scan forever, logging (not raising) errors.

        Args:
            app: The Flask application.
            interval: Seconds between scans.
        """
        while True:
            started = time.monotonic()
            try:
                with app.app_context():
                    try:
                        scan_alarms()
                    except Exception as exc:  # noqa: BLE001
                        _record_failure(exc)
                        log.exception("alarm scan failed")
                        try:
                            db.session.rollback()
                        except Exception:  # noqa: BLE001
                            log.exception("rollback after a failed alarm scan failed")
                    finally:
                        db.session.remove()
            except Exception:  # noqa: BLE001 - never let the thread die
                log.exception("alarm scanner error")
            time.sleep(max(0.2, interval - (time.monotonic() - started)))
