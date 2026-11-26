"""High-level PLC operations used by the views and the JSON API.

These functions translate between database models (dashboards, data points)
and the low-level :class:`~app.plc.driver.PLCConnectionManager`.
"""

import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, wait
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


def read_plcs(groups: dict, max_wait=_DEFAULT) -> dict:
    """Read several PLCs in parallel, each in its own thread.

    A slow PLC or device does not delay the others: the call returns after
    ``max_wait`` seconds at the latest, with the last good values (``stale``)
    of the PLCs that have not answered yet.  Their reads keep running in the
    background and refresh the values for the next call; while a PLC's read
    is still running no second read of it is started.

    Args:
        groups: ``plc_id -> (plc, {key: address})``.
        max_wait: Time limit in seconds (``None`` = wait for every PLC).
            Default: ``PLC_PAGE_READ_WAIT`` (1.5 s) during a web request,
            no limit in background workers (alarms, history, automations).

    Returns:
        ``plc_id -> {key: ReadResult}``.
    """
    manager = get_manager()
    if max_wait is _DEFAULT:
        max_wait = _default_wait()
    if not groups:
        return {}
    futures = {}
    waiting = {}
    for plc_id, (plc, addresses) in groups.items():
        with _pool_lock:
            running = _running.get(plc_id)
            if running is not None and not running.done():
                waiting[plc_id] = running       # wait for the read in progress, do not start another
                continue
            # Load the PLC's settings here: the worker must not refresh the row through the
            # (thread-bound) database session.
            plc.connection_signature  # noqa: B018
            # Copy the context: the worker keeps the request's language for error messages.
            future = _read_pool().submit(contextvars.copy_context().run, manager.read, plc, addresses)
            _running[plc_id] = future
        futures[plc_id] = future
    wait(list(futures.values()) + list(waiting.values()), timeout=max_wait)
    out = {}
    for plc_id, (plc, addresses) in groups.items():
        future = futures.get(plc_id)
        if future is not None and future.done():
            try:
                out[plc_id] = future.result()
                continue
            except Exception as exc:  # noqa: BLE001 - a broken read must not break the page
                log.exception("Reading PLC %s failed", plc.name)
                out[plc_id] = {key: _error_result(str(exc)) for key in addresses}
                continue
        out[plc_id] = manager.last_values(plc, addresses)   # still reading (or another read was running)
    return out


def _error_result(message: str):
    """A failed :class:`~app.plc.driver.ReadResult` with this message."""
    from app.plc.driver import ReadResult  # noqa: PLC0415

    return ReadResult(error=message)


def read_datapoints(datapoints, max_wait=_DEFAULT) -> tuple[dict, dict]:
    """Read the current value of many data points, grouped per PLC.

    Each PLC is contacted once; its tags are batched by the manager.  The
    PLCs are read in parallel (:func:`read_plcs`), so a slow device only
    delays its own tags.

    Args:
        datapoints: Iterable of :class:`~app.models.dashboard.DataPoint`.
        max_wait: See :func:`read_plcs` (default: 1.5 s in web requests,
            no limit in background workers).

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
                      for plc_id, (plc, points) in by_plc.items()}, max_wait=max_wait)
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
    values, _ = read_datapoints([datapoint])
    return values[datapoint.id]
