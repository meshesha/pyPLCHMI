"""High-level PLC operations used by the views and the JSON API.

These functions translate between database models (dashboards, data points)
and the low-level :class:`~app.plc.driver.PLCConnectionManager`.
"""

import contextvars
import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone

from flask import current_app, has_request_context

from app.plc.datatypes import json_safe
from app.plc.driver import PLCError, get_manager
from app.i18n.catalog import gettext

log = logging.getLogger(__name__)

#: Threads that read the PLCs of one call in parallel (one per PLC; shared by all requests).
READ_WORKERS = 16
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.RLock()  # re-entrant: read_plcs() holds it while _read_pool() runs
#: Reads still running per PLC id (a slow device is never read twice at the same time).
_running: dict = {}
#: Marker for the default ``max_wait`` of :func:`read_datapoints`.
_DEFAULT = object()


def _read_pool() -> ThreadPoolExecutor:
    """The shared thread pool for PLC reads (created on first use)."""
    global _pool  # noqa: PLW0603 - one pool per process
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=READ_WORKERS, thread_name_prefix="plc-read")
        return _pool


def _default_wait():
    """Default time limit: ``PLC_PAGE_READ_WAIT`` for web requests, no limit for background workers."""
    if not has_request_context():
        return None
    return float(current_app.config.get("PLC_PAGE_READ_WAIT", 1.5))


# ---------------------------------------------------------------------------
# Shared reads: every caller (screens, alarms, history, automations, Modbus
# server) uses the same reads of a PLC instead of polling it on its own.
# ---------------------------------------------------------------------------
def _state(manager) -> dict:
    """The shared-read book-keeping of one connection manager (one per application).

    * ``interest``: tags asked for recently per PLC,
      ``plc_id -> {key: (address, last asked (monotonic))}``.  A read of a PLC
      covers all of them, so the next caller usually finds its values ready.
    * ``results``: latest results per PLC,
      ``plc_id -> (connection signature, {key: (ReadResult, address repr, read started)})``.
    """
    return manager.__dict__.setdefault("shared_reads", {"interest": {}, "results": {}})


def _settings() -> tuple[float, float]:
    """``(PLC_SHARE_AGE, PLC_INTEREST_TTL)`` of the current application."""
    cfg = current_app.config
    return float(cfg.get("PLC_SHARE_AGE", 0.5)), float(cfg.get("PLC_INTEREST_TTL", 10.0))


def _note_interest(state: dict, plc_id, addresses: dict, now: float, ttl: float) -> dict:
    """Remember the tags a caller asked for and return every tag of the PLC still in use (lock held).

    Args:
        plc_id: PLC id.
        addresses: ``key -> address`` of this caller.
        now: Monotonic time.
        ttl: Seconds a tag stays in the shared read after it was last asked for.

    Returns:
        ``key -> address`` to read: this caller's tags plus those other
        callers asked for in the last ``ttl`` seconds.
    """
    known = state["interest"].setdefault(plc_id, {})
    for key, addr in addresses.items():
        known[key] = (addr, now)
    for key in [k for k, (_a, t) in known.items() if now - t > ttl]:
        del known[key]
    return {key: addr for key, (addr, _t) in known.items()}


def _from_shared(state: dict, plc, addresses: dict, not_before: float):
    """This caller's results from a shared read that started at ``not_before`` or later (lock held).

    Returns:
        ``key -> ReadResult``, or ``None`` if any tag has no such result.
    """
    signature, results = state["results"].get(plc.id, (None, None))
    if not results or signature != plc.connection_signature:
        return None
    out = {}
    for key, addr in addresses.items():
        entry = results.get(key)
        if entry is None or entry[2] < not_before or entry[1] != repr(addr):
            return None
        out[key] = entry[0]
    return out


def _store(plc_id, future) -> None:
    """Keep the results of a finished read for the other callers (lock held)."""
    if future.cancelled() or future.exception() is not None:
        return
    state = future.state
    signature, results = state["results"].get(plc_id, (None, {}))
    if signature != future.signature:
        results = {}
    state["results"][plc_id] = (future.signature, results)
    wanted = state["interest"].get(plc_id, {})
    for key, res in future.result().items():
        if res.stale or not res.ok or key not in future.addresses:   # only new good values are shared
            continue
        results[key] = (res, repr(future.addresses[key]), future.started)
    for key in [k for k in results if k not in wanted]:
        del results[key]


def forget_values(plc_id) -> None:
    """Drop the shared results of a PLC (after a write: the next read must ask the PLC).

    Args:
        plc_id: PLC id.
    """
    with _pool_lock:
        _state(get_manager())["results"].pop(plc_id, None)


def _covers(future, addresses: dict) -> bool:
    """``True`` if a running read includes every tag of ``addresses`` (same address)."""
    return all(key in future.addresses and repr(future.addresses[key]) == repr(addr)
               for key, addr in addresses.items())


def _forget(plc_id, future) -> None:
    """Store a finished read's results and drop it from :data:`_running`."""
    with _pool_lock:
        _store(plc_id, future)
        if _running.get(plc_id) is future:
            del _running[plc_id]


def _submit(manager, plc_id, plc, addresses):
    """Start a read of one PLC in the pool and register it in :data:`_running` (lock held)."""
    # Load the PLC's settings here: the worker must not refresh the row through the
    # (thread-bound) database session.
    signature = plc.connection_signature
    # Copy the context: the worker keeps the request's language for error messages.
    future = _read_pool().submit(contextvars.copy_context().run, manager.read, plc, addresses)
    future.addresses, future.started = dict(addresses), time.monotonic()
    future.state, future.signature = _state(manager), signature
    _running[plc_id] = future
    future.add_done_callback(lambda f, pid=plc_id: _forget(pid, f))
    return future


def _remaining(deadline):
    """Seconds left until ``deadline`` (``None`` = no limit), never negative."""
    return None if deadline is None else max(0.0, deadline - time.monotonic())


def read_plcs(groups: dict, max_wait=_DEFAULT, fresh=None, errors: list | None = None) -> dict:
    """Read several PLCs in parallel, each in its own thread.

    A slow PLC or device does not delay the others: the call returns after
    ``max_wait`` seconds at the latest, with the last good values (``stale``)
    of the PLCs that have not answered yet.  Their reads keep running in the
    background and refresh the values for the next call; while a PLC's read
    is still running no second read of it is started.

    Reads are shared by all callers.  A read of a PLC includes every tag of
    that PLC that any caller asked for in the last ``PLC_INTEREST_TTL``
    seconds, and its results are kept: a caller whose tags were all read
    less than ``PLC_SHARE_AGE`` seconds ago gets them without contacting the
    PLC, and a caller that finds a read with its tags already running waits
    for that read instead of starting another one.  So five wall displays,
    the alarm scanner and the history logger on the same PLC cause about one
    read per refresh instead of one each.

    A *fresh* read only takes results of a read that started after the call:
    it never answers from the cache or from a read that may have started
    before a write.  Background workers always read fresh (a history sample
    must not be skipped because a screen was refreshing), and so does the
    read-back after a write.

    Args:
        groups: ``plc_id -> (plc, {key: address})``.
        max_wait: Time limit in seconds (``None`` = wait for every PLC).
            Default: ``PLC_PAGE_READ_WAIT`` (1.5 s) during a web request,
            no limit in background workers (alarms, history, automations).
        fresh: Read fresh values (see above).  Default: ``True`` when there
            is no time limit (background workers), else ``False``.
        errors: Receives ``"PLC: message"`` for every PLC whose read raised.

    Returns:
        ``plc_id -> {key: ReadResult}``.
    """
    manager = get_manager()
    if max_wait is _DEFAULT:
        max_wait = _default_wait()
    if fresh is None:
        fresh = max_wait is None
    if not groups:
        return {}
    share_age, ttl = _settings()
    state = _state(manager)
    started = time.monotonic()
    not_before = started if fresh else started - share_age
    deadline = None if max_wait is None else started + max_wait
    out = {}
    futures = {}   # plc_id -> a read whose results this call uses
    waiting = {}   # plc_id -> a read of someone else that is still running (without our tags or too old)
    for plc_id, (plc, addresses) in groups.items():
        with _pool_lock:
            wanted = _note_interest(state, plc_id, addresses, started, ttl)
            ready = _from_shared(state, plc, addresses, not_before) if share_age > 0 or fresh else None
            if ready is not None:
                out[plc_id] = ready
                continue
            running = _running.get(plc_id)
            if running is not None and not running.done():
                if running.started >= not_before and _covers(running, addresses):
                    futures[plc_id] = running   # that read is recent enough and has our tags
                else:
                    waiting[plc_id] = running   # do not start a second read of the same PLC now
                continue
            futures[plc_id] = _submit(manager, plc_id, plc, wanted if share_age > 0 else addresses)
    if fresh and waiting:
        # Wait for the reads in progress, then read each of these PLCs ourselves.
        pending = dict(waiting)
        while pending:
            remaining = _remaining(deadline)
            if remaining == 0:
                break
            done, _ = wait(list(pending.values()), timeout=remaining, return_when=FIRST_COMPLETED)
            for plc_id, running in list(pending.items()):
                if running in done:
                    del pending[plc_id]
                    del waiting[plc_id]
                    plc, addresses = groups[plc_id]
                    with _pool_lock:
                        current = _running.get(plc_id)
                        if (current is not None and not current.done() and current.started >= not_before
                                and _covers(current, addresses)):
                            futures[plc_id] = current     # another caller already started a new read
                        else:
                            wanted = _note_interest(state, plc_id, addresses, time.monotonic(), ttl)
                            futures[plc_id] = _submit(manager, plc_id, plc, wanted if share_age > 0 else addresses)
    wait(list(futures.values()) + list(waiting.values()), timeout=_remaining(deadline))
    for plc_id, (plc, addresses) in groups.items():
        if plc_id in out:
            continue
        future = futures.get(plc_id)
        if future is not None and future.done():
            try:
                result = future.result()
                out[plc_id] = {key: result[key] for key in addresses}
                continue
            except Exception as exc:  # noqa: BLE001 - a broken read must not break the page
                log.exception("Reading PLC %s failed", plc.name)
                if errors is not None:
                    errors.append(f"{plc.name}: {exc}")
                out[plc_id] = {key: _error_result(str(exc)) for key in addresses}
                continue
        out[plc_id] = manager.last_values(plc, addresses)   # still reading (or another read was running)
    return out


def _error_result(message: str):
    """A failed :class:`~app.plc.driver.ReadResult` with this message."""
    from app.plc.driver import ReadResult  # noqa: PLC0415

    return ReadResult(error=message)


def read_datapoints(datapoints, max_wait=_DEFAULT, fresh=None, errors: list | None = None) -> tuple[dict, dict]:
    """Read the current value of many data points, grouped per PLC.

    Each PLC is contacted once; its tags are batched by the manager.  The
    PLCs are read in parallel (:func:`read_plcs`), so a slow device only
    delays its own tags.

    Args:
        datapoints: Iterable of :class:`~app.models.dashboard.DataPoint`.
        max_wait: See :func:`read_plcs` (default: 1.5 s in web requests,
            no limit in background workers).
        fresh: See :func:`read_plcs` (default: fresh without a time limit).
        errors: Receives ``"PLC: message"`` for every PLC whose read failed
            unexpectedly (its tags get ``quality`` ``bad``).

    Returns:
        A tuple ``(values, plcs)`` where ``values`` maps data point id to a
        dict ``{"value", "display", "raw", "quality", "error", "stale"}`` and
        ``plcs`` maps PLC id to ``{"name", "connected", "state", "error",
        "down_for", "offline_delay"}``.  ``stale`` is ``True`` for the last
        good value returned while the PLC is briefly unreachable (quality
        stays ``good`` until the PLC's communication timeout has passed).
        Local dummy tags are not read: they get :func:`local_value` (quality
        ``local``), the browser shows its own value instead.
    """
    manager = get_manager()
    by_plc: dict = {}
    values, plcs = {}, {}
    for dp in datapoints:
        if dp.is_local:
            values[dp.id] = local_value(dp)
            continue
        by_plc.setdefault(dp.plc_id, (dp.plc, []))[1].append(dp)

    read = read_plcs({plc_id: (plc, {dp.id: dp.s7_address for dp in points})
                      for plc_id, (plc, points) in by_plc.items()}, max_wait=max_wait, fresh=fresh, errors=errors)
    for plc_id, (plc, points) in by_plc.items():
        results = read[plc_id]
        for dp in points:
            res = results[dp.id]
            if res.ok:
                eng = dp.to_engineering(res.value)
                values[dp.id] = {
                    "value": json_safe(eng),
                    "raw": json_safe(res.value),
                    "display": dp.format_value(eng),
                    "quality": "good",
                    "error": None,
                    "stale": res.stale,
                }
            else:
                values[dp.id] = {"value": None, "raw": None, "display": "—", "quality": "bad", "error": res.error,
                                 "stale": False}
        plcs[plc_id] = plc_status(plc, manager)
    return values, plcs


def read_values(points, errors: list | None = None, what: str = "read") -> dict:
    """Read the tags of many PLCs for a background worker (alarms, history, automations).

    All PLCs are read in parallel with fresh values (:func:`read_datapoints`),
    so one slow or offline PLC no longer delays the tags of the others.  A
    PLC whose read fails only makes its own tags unreadable.  If something
    unexpected breaks the combined call, the PLCs are read again one at a
    time so the healthy ones still get their values.

    Args:
        points: Iterable of :class:`~app.models.dashboard.DataPoint`.
        errors: Receives ``"PLC: message"`` texts of failed PLCs.
        what: Name of the caller for the log (``"alarm scan"``, ``"history"``...).

    Returns:
        ``datapoint id -> value dict`` (see :func:`read_datapoints`).
    """
    points = [dp for dp in points if not dp.is_local]
    if not points:
        return {}
    errors = [] if errors is None else errors
    try:
        return read_datapoints(points, fresh=True, errors=errors)[0]
    except Exception:  # noqa: BLE001 - fall back to one PLC at a time below
        log.exception("%s: reading the PLCs together failed, reading them one by one", what)
    by_plc: dict = {}
    for dp in points:
        by_plc.setdefault(dp.plc_id, []).append(dp)
    values: dict = {}
    for group in by_plc.values():
        try:
            values.update(read_datapoints(group, fresh=True)[0])
        except Exception as exc:  # noqa: BLE001 - isolate one PLC's failure
            name = group[0].plc.name if group[0].plc else "?"
            log.exception("%s: reading PLC %s failed", what, name)
            errors.append(f"{name}: {exc}")
    return values


def local_value(dp) -> dict:
    """Value entry of a local dummy tag (the server does not know the browser's value).

    Args:
        dp: The local dummy tag.

    Returns:
        Dict like the entries of :func:`read_datapoints` with the start value,
        ``quality`` ``"local"`` and ``local`` = :meth:`DataPoint.local_spec`
        (used by ``hmi-local.js`` to keep and display the window's value).
    """
    spec = dp.local_spec()
    return {"value": spec["initial"], "raw": spec["initial"], "display": dp.format_value(spec["initial"]),
            "quality": "local", "error": None, "stale": False, "local": spec}


def plc_status(plc, manager=None) -> dict:
    """Communication status of one PLC for the screens and the PLC list.

    Args:
        plc: The PLC model.
        manager: Connection manager (default: the application's).

    Returns:
        ``{"name", "state", "connected", "error", "down_for", "offline_delay"}``
        (see :meth:`~app.plc.driver.PLCConnectionManager.status`).
    """
    status = (manager or get_manager()).status(plc)
    return {"name": plc.name, "state": status["state"], "connected": status["connected"], "error": status["error"],
            "down_for": status["down_for"], "offline_delay": status["offline_delay"]}


def read_dashboard(dashboard) -> dict:
    """Read every data point of a dashboard.

    Args:
        dashboard: A :class:`~app.models.dashboard.Dashboard`.

    Returns:
        JSON-ready dict with ``timestamp``, ``values`` and ``plcs`` (see
        :func:`read_datapoints`).  Dict keys are strings for JSON.
    """
    values, plcs = read_datapoints(dashboard.datapoints)
    return {
        "dashboard": dashboard.id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "values": {str(k): v for k, v in values.items()},
        "plcs": {str(k): v for k, v in plcs.items()},
    }


def write_datapoint(datapoint, value) -> dict:
    """Write an engineering value to the PLC location of a data point.

    The value is converted to raw units with ``DataPoint.to_raw`` (reverse
    scaling), written, and read back for confirmation.

    Args:
        datapoint: The target :class:`~app.models.dashboard.DataPoint`.
        value: Engineering value from the operator.

    Returns:
        The fresh value dict (same shape as in :func:`read_datapoints`).

    Raises:
        PLCError: On communication failure, if the point is not writable, or
            for a local dummy tag (its value lives in the browser).
        ValueError: If the value is invalid for the data type.
    """
    if not datapoint.writable:
        raise PLCError(gettext('This data point is read-only'))
    if datapoint.is_local:
        raise PLCError(gettext('Local dummy tag: the value is kept by each browser window, not by the server'))
    raw = datapoint.to_raw(value)
    manager = get_manager()
    manager.write(datapoint.plc, datapoint.s7_address, raw)
    forget_values(datapoint.plc_id)   # screens must not show the value from before the write
    # Fresh: a screen refresh that was already running must not answer with the value before the write.
    values, _ = read_datapoints([datapoint], fresh=True)
    return values[datapoint.id]
