"""History logger: saves samples of the logged tags every *N* seconds.

A tag is logged when ``DataPoint.log_enabled`` is set; its own
``log_interval`` (seconds) decides how often.  The background thread
(:class:`HistoryLogger`) wakes up every ``HISTORY_TICK`` seconds and calls
:func:`sample_due_tags`, which

1. finds the logged tags whose next sample time has come,
2. reads them – one PLC at a time, so a failing PLC only affects its own
   tags – and
3. stores one :class:`~app.models.history.TagSample` per tag with a good
   value (unreadable values are skipped; the gap is visible in the chart).

Sample times are aligned to the interval (a 10 s tag is sampled at :00,
:10, :20 …), so tags with the same interval share timestamps, which makes the
"one column per tag" Excel export line up.  Once an hour samples older than
the retention period (System settings) are deleted.

Local dummy tags have no value on the server and are never logged.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import DataPoint, TagSample
from app.models.setting import get_setting

log = logging.getLogger(__name__)

#: Setting key of the retention period in days (0 = keep forever).
RETENTION_SETTING = "history_retention_days"
#: Seconds between two clean-ups of old samples.
PURGE_EVERY = 3600
#: Shortest and longest sample interval (seconds).
MIN_INTERVAL, MAX_INTERVAL = 1, 86400

_lock = threading.Lock()
#: tag id -> (interval, next due epoch seconds)
_due: dict[int, tuple[int, float]] = {}
_status: dict = {"last_run": None, "last_samples": 0, "total_samples": 0, "errors": [], "last_purge": None,
                 "purged": 0, "failure": None}
_last_purge_mono: float | None = None


def _next_boundary(now: float, interval: int) -> float:
    """The next multiple of ``interval`` seconds after ``now``.

    Args:
        now: Epoch seconds.
        interval: Interval in seconds.

    Returns:
        Epoch seconds of the next aligned sample time.
    """
    return (math.floor(now / interval) + 1) * interval


def reset_schedule() -> None:
    """Forget the next sample times (all logged tags are sampled on the next run)."""
    with _lock:
        _due.clear()


def retention_days() -> int:
    """Days samples are kept (System settings, default ``HISTORY_RETENTION_DAYS``).

    Returns:
        Number of days; ``0`` = forever.
    """
    from flask import current_app  # noqa: PLC0415

    default = int(current_app.config.get("HISTORY_RETENTION_DAYS", 90))
    try:
        return max(0, int(get_setting(RETENTION_SETTING, default)))
    except (TypeError, ValueError):
        return default


def logged_tags() -> list[DataPoint]:
    """All tags with history logging switched on (local dummy tags excluded).

    Returns:
        The tags.
    """
    return [dp for dp in DataPoint.query.filter_by(log_enabled=True).all() if not dp.is_local]


def _read(points: list[DataPoint], errors: list) -> dict:
    """Read tags one PLC at a time.

    Args:
        points: Tags to read.
        errors: Receives ``"PLC: message"`` texts of failed PLCs.

    Returns:
        ``tag id -> value dict`` (see :func:`app.plc.service.read_datapoints`).
    """
    from app.plc.service import read_datapoints  # noqa: PLC0415

    by_plc: dict = {}
    for dp in points:
        by_plc.setdefault(dp.plc_id, []).append(dp)
    values: dict = {}
    for group in by_plc.values():
        try:
            values.update(read_datapoints(group)[0])
        except Exception as exc:  # noqa: BLE001 - isolate one PLC's failure
            name = group[0].plc.name if group[0].plc else "?"
            log.exception("history: reading PLC %s failed", name)
            errors.append(f"{name}: {exc}")
    return values


def sample_due_tags(now: float | None = None, force: bool = False) -> dict:
    """Save one sample for every logged tag that is due.

    Must run inside an application context.

    Args:
        now: Current epoch seconds (tests pass fixed times).
        force: Sample every logged tag, due or not.

    Returns:
        Counters ``{"due", "saved", "skipped", "errors"}``.
    """
    now = time.time() if now is None else now
    stats = {"due": 0, "saved": 0, "skipped": 0, "errors": []}
    tags = logged_tags()
    due: list[DataPoint] = []
    with _lock:
        active = {dp.id for dp in tags}
        for tag_id in list(_due):
            if tag_id not in active:
                del _due[tag_id]
        for dp in tags:
            interval = min(MAX_INTERVAL, max(MIN_INTERVAL, int(dp.log_interval or 60)))
            known = _due.get(dp.id)
            if force or known is None or known[0] != interval or now >= known[1] - 0.001:
                due.append(dp)
                _due[dp.id] = (interval, _next_boundary(now, interval))
    stats["due"] = len(due)
    if due:
        ts = datetime.fromtimestamp(math.floor(now), tz=timezone.utc)
        values = _read(due, stats["errors"])
        for dp in due:
            v = values.get(dp.id)
            if not v or v.get("quality") != "good" or v.get("stale"):
                stats["skipped"] += 1
                continue
            db.session.add(TagSample.from_value(dp.id, ts, v.get("value")))
            stats["saved"] += 1
        db.session.commit()
    _status.update(last_run=datetime.now(timezone.utc), last_samples=stats["saved"], errors=stats["errors"],
                   failure=None)
    _status["total_samples"] += stats["saved"]
    return stats


def purge_old_samples(days: int | None = None) -> int:
    """Delete samples older than the retention period.

    Args:
        days: Days to keep (default :func:`retention_days`); ``0`` keeps everything.

    Returns:
        Number of deleted samples.
    """
    days = retention_days() if days is None else days
    if days <= 0:
        return 0
    limit = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = TagSample.query.filter(TagSample.ts < limit).delete(synchronize_session=False)
    db.session.commit()
    _status.update(last_purge=datetime.now(timezone.utc), purged=deleted)
    if deleted:
        log.info("history: deleted %d samples older than %d days", deleted, days)
    return deleted


def logger_status() -> dict:
    """State of the logger for the History page.

    Returns:
        A copy of the status plus ``thread`` (``"running"``, ``"stopped"`` or
        ``"off"`` when ``HISTORY_LOGGER`` is disabled) and ``age`` (seconds
        since the last run, ``None`` before the first one).
    """
    from flask import current_app

    data = dict(_status)
    if not current_app.config.get("HISTORY_LOGGER", True):
        data["thread"] = "off"
    elif HistoryLogger.thread is not None and HistoryLogger.thread.is_alive():
        data["thread"] = "running"
    else:
        data["thread"] = "stopped"
    last = data.get("last_run")
    data["age"] = round((datetime.now(timezone.utc) - last).total_seconds()) if last else None
    return data


def _record_failure(exc: Exception) -> None:
    """Remember an error that stopped a logger run.

    Args:
        exc: The exception.
    """
    _status.update(last_run=datetime.now(timezone.utc), failure=f"{type(exc).__name__}: {exc}")


def run_once() -> None:
    """One logger cycle: sample the due tags and purge old samples when it is time."""
    global _last_purge_mono  # noqa: PLW0603
    sample_due_tags()
    mono = time.monotonic()
    if _last_purge_mono is None or mono - _last_purge_mono >= PURGE_EVERY:
        _last_purge_mono = mono
        purge_old_samples()


class HistoryLogger:
    """Background thread calling :func:`run_once` every ``HISTORY_TICK`` seconds."""

    #: The logger thread of this process (``None`` before the first request).
    thread: threading.Thread | None = None
    _lock = threading.Lock()
    _stop = threading.Event()

    @classmethod
    def stop(cls, timeout: float = 5.0) -> None:
        """Stop the thread (tests and orderly shutdown).

        Args:
            timeout: Seconds to wait for the thread to end.
        """
        thread = cls.thread
        cls._stop.set()
        if thread is not None:
            thread.join(timeout)
        cls.thread = None

    @classmethod
    def start(cls, app) -> bool:
        """Start the logger thread, or restart it if it has stopped.

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
            tick = max(0.2, float(app.config.get("HISTORY_TICK", 1.0)))
            cls._stop = threading.Event()
            cls.thread = threading.Thread(target=cls._run, args=(app, tick, cls._stop), name="history-logger", daemon=True)
            cls.thread.start()
        if restarted:
            log.warning("History logger thread had stopped – restarted")
        else:
            log.info("History logger started")
        return True

    @staticmethod
    def _run(app, tick: float, stop: threading.Event) -> None:
        """Thread body: run until stopped, logging (not raising) errors.

        Args:
            app: The Flask application.
            tick: Seconds between two checks.
            stop: Set to end the loop.
        """
        while not stop.is_set():
            started = time.monotonic()
            try:
                with app.app_context():
                    try:
                        run_once()
                    except Exception as exc:  # noqa: BLE001
                        _record_failure(exc)
                        log.exception("history logging failed")
                        try:
                            db.session.rollback()
                        except Exception:  # noqa: BLE001
                            log.exception("rollback after failed history logging failed")
                    finally:
                        db.session.remove()
            except Exception:  # noqa: BLE001 - never let the thread die
                log.exception("history logger error")
            # wake up just after the next whole second so aligned samples get the right timestamp
            elapsed = time.monotonic() - started
            delay = max(0.05, tick - elapsed)
            if tick >= 1:
                delay = max(0.05, min(delay, 1.0 - (time.time() % 1.0) + 0.02 + (tick - 1)))
            stop.wait(delay)
