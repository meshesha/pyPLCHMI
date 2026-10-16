"""High-level PLC operations used by the views and the JSON API.

These functions translate between database models (dashboards, data points)
and the low-level :class:`~app.plc.driver.PLCConnectionManager`.
"""

from datetime import datetime, timezone

from app.plc.datatypes import json_safe
from app.plc.driver import PLCError, get_manager
from app.i18n.catalog import gettext


def read_datapoints(datapoints) -> tuple[dict, dict]:
    """Read the current value of many data points, grouped per PLC.

    Each PLC is contacted once; its tags are batched by the manager.

    Args:
        datapoints: Iterable of :class:`~app.models.dashboard.DataPoint`.

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

    for plc_id, (plc, points) in by_plc.items():
        results = manager.read(plc, {dp.id: dp.s7_address for dp in points})
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
