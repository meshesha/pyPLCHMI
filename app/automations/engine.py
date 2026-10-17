"""The automation engine: watches the triggers and runs the actions.

* :func:`tick` is called every ``AUTOMATION_TICK`` seconds (1 s) by the
  :class:`AutomationEngine` thread.  It reads the tags used by tag triggers
  (one request per PLC), follows alarm events and the PLCs' communication
  state, checks the clock, and fires the triggers whose condition has just
  become true.
* :func:`trigger` starts a run according to the automation's *mode*
  (single / restart / queued / parallel).  Each run executes in its own
  thread (or in the calling thread when ``AUTOMATION_SYNC`` is set, as in the
  tests), checks the conditions, executes the actions and stores an
  :class:`~app.models.automation.AutomationRun` with a step-by-step trace.

Safety of PLC writes: a *Write tag* action only runs when the automation has
been approved for writing by an administrator (``allow_writes``) and the tag
is writable.  Every write is recorded in the audit log as
``Automation: <name>``.

Trigger semantics follow Home Assistant: a trigger fires on a *change* –
a tag already above a limit when the server starts does not fire until it has
left the range and crossed into it again.
"""

from __future__ import annotations

import collections
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import current_app

from app.audit.recorder import paused as audit_paused
from app.automations.schema import format_duration, time_seconds
from app.extensions import db
from app.i18n.catalog import gettext

log = logging.getLogger(__name__)

#: Words of the run traces (step kinds and results), listed for the translations.
TRACE_WORDS = ["trigger", "condition", "action", "fired", "true", "false", "ok", "error", "started", "disabled",
               "timeout", "then", "else", "stopped", "finished", "skipped", "queued", "restarted"]
#: Runs kept per automation (older ones are deleted).
KEEP_RUNS = 30
#: Poll interval of "wait until" actions, in seconds.
WAIT_POLL = 0.5
#: Most nested "run another automation" calls (stops loops).
MAX_CHAIN = 5
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class StopRun(Exception):
    """Ends a run early (``status`` = ``stopped``, ``condition`` or ``finished``)."""

    def __init__(self, status: str, reason: str = ""):
        super().__init__(reason)
        self.status = status
        self.reason = reason


# ============================================================================ values
def read_values(points) -> dict:
    """Read tags, one PLC at a time (a failing PLC only affects its own tags).

    Args:
        points: Iterable of :class:`~app.models.dashboard.DataPoint`.

    Returns:
        ``datapoint id -> value dict`` (see :func:`~app.plc.service.read_datapoints`).
    """
    from app.plc.service import read_datapoints  # noqa: PLC0415

    by_plc: dict = {}
    for dp in points:
        if not dp.is_local:
            by_plc.setdefault(dp.plc_id, []).append(dp)
    values: dict = {}
    for group in by_plc.values():
        try:
            values.update(read_datapoints(group)[0])
        except Exception:  # noqa: BLE001 - isolate one PLC's failure
            log.exception("automations: reading PLC %s failed", group[0].plc.name if group[0].plc else "?")
    return values


def _truthy(text: str, dp=None) -> bool | None:
    """Interpret a text as a boolean (``on``/``off``, ``1``/``0``, the tag's ON/OFF texts…), else ``None``."""
    t = str(text).strip().lower()
    on = {"1", "true", "on", "yes"} | ({dp.true_text.strip().lower()} if dp is not None else set())
    off = {"0", "false", "off", "no"} | ({dp.false_text.strip().lower()} if dp is not None else set())
    if t in on:
        return True
    if t in off:
        return False
    return None


def value_matches(dp, value, expected) -> bool:
    """Whether a tag value equals a value written in a trigger or condition.

    Booleans accept ``on/off``, ``true/false``, ``1/0`` and the tag's own
    ON/OFF texts; numbers compare numerically; texts compare exactly.
    An empty ``expected`` matches everything.

    Args:
        dp: The tag.
        value: Engineering value read from the PLC.
        expected: Text from the configuration (or ``None``).

    Returns:
        ``True`` on a match.
    """
    if expected is None or str(expected).strip() == "":
        return True
    if isinstance(value, bool) or (dp is not None and dp.is_bool):
        wanted = _truthy(expected, dp)
        return wanted is not None and bool(value) == wanted
    if isinstance(value, (int, float)):
        try:
            return abs(float(value) - float(expected)) < 1e-9
        except (TypeError, ValueError):
            return False
    return str(value) == str(expected)


def convert_for_write(dp, value):
    """Turn the text of a *Write tag* action into the value for the tag's data type.

    Raises:
        ValueError: If the text does not fit the data type.
    """
    if dp.is_bool:
        b = _truthy(value, dp)
        if b is None:
            raise ValueError(gettext("'{0}' is not ON/OFF (use on/off, true/false or 1/0)", value))
        return b
    if dp.is_numeric:
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(gettext("'{0}' is not a number", value)) from None
    return str(value)


def utc_now() -> datetime:
    """The current time (UTC).  The only clock of the engine, so tests can replace it."""
    return datetime.now(timezone.utc)


def local_now(now: datetime | None = None) -> datetime:
    """Current time in the plant's zone (``DISPLAY_TIMEZONE`` or the server clock).

    Args:
        now: Aware UTC time (default: now).
    """
    from app.utils.timezones import server_zone, zone_by_name  # noqa: PLC0415

    now = now or utc_now()
    zone = zone_by_name(current_app.config.get("DISPLAY_TIMEZONE") or "") or server_zone()
    return now.astimezone(zone)


# ============================================================================ conditions
class Evaluator:
    """Evaluates conditions, reading each tag at most once per evaluation.

    Args:
        now: Aware UTC time (tests pass a fixed time).
    """

    def __init__(self, now: datetime | None = None):
        self.now = now
        self.values: dict = {}

    def _value(self, tag_id: int):
        """``(DataPoint, value dict)`` of a tag (read on first use)."""
        from app.models import DataPoint  # noqa: PLC0415

        dp = db.session.get(DataPoint, tag_id)
        if dp is None:
            raise ValueError(gettext("tag #{0} no longer exists", tag_id))
        if tag_id not in self.values:
            self.values.update(read_values([dp]))
        return dp, self.values.get(tag_id) or {"quality": "bad", "value": None, "display": "—"}

    def check_all(self, conditions: list, steps: list | None = None, path: str = "condition") -> bool:
        """``True`` when every enabled condition is true (an empty list is true)."""
        ok = True
        for i, cond in enumerate(conditions or []):
            if cond.get("enabled") is False:
                continue
            result, text = self.check(cond)
            if steps is not None:
                steps.append(_step(f"{path}/{i}", "condition", cond.get("alias") or text, "true" if result else "false"))
            if not result:
                ok = False
                break
        return ok

    def check(self, cond: dict) -> tuple[bool, str]:
        """Evaluate one condition.

        Returns:
            ``(result, explanation)``.
        """
        t = cond["type"]
        if t in ("and", "or", "not"):
            results = [self.check(c)[0] for c in cond.get("conditions", []) if c.get("enabled") is not False]
            value = all(results) if t == "and" else any(results) if t == "or" else not any(results)
            return value, {"and": "And", "or": "Or", "not": "Not"}[t] + f" ({len(results)})"
        if t in ("state", "numeric_state"):
            dp, v = self._value(cond["tag"])
            if v.get("quality") != "good":
                return False, gettext("'{0}' cannot be read", dp.label)
            if t == "state":
                ok = value_matches(dp, v["value"], cond["state"])
                return ok, f"{dp.label} = {v.get('display')} ({gettext('expected')} {cond['state']})"
            ok = _in_range(v["value"], cond.get("above"), cond.get("below"))
            return ok, f"{dp.label} = {v.get('display')} ({_range_text(cond)})"
        if t == "time":
            now = local_now(self.now)
            secs = now.hour * 3600 + now.minute * 60 + now.second
            ok = True
            if cond.get("weekdays") and _DAYS[now.weekday()] not in cond["weekdays"]:
                ok = False
            after = time_seconds(cond["after"]) if cond.get("after") else None
            before = time_seconds(cond["before"]) if cond.get("before") else None
            if after is not None and before is not None and after > before:   # passes midnight
                ok = ok and (secs >= after or secs < before)
            else:
                if after is not None:
                    ok = ok and secs >= after
                if before is not None:
                    ok = ok and secs < before
            return ok, f"{gettext('time')} {now.strftime('%H:%M:%S')}"
        if t == "alarm_active":
            from app.models import AlarmEvent  # noqa: PLC0415

            q = AlarmEvent.query.filter(AlarmEvent.cleared_at.is_(None), AlarmEvent.source == "alarm")
            if cond.get("alarm"):
                q = q.filter(AlarmEvent.alarm_id == cond["alarm"])
            if cond.get("severity"):
                q = q.filter(AlarmEvent.severity == cond["severity"])
            n = q.count()
            return n > 0, gettext("{0} active alarm(s)", n)
        raise ValueError(f"unknown condition {t}")


def _in_range(value, above, below) -> bool:
    """``above < value < below`` (missing limits are ignored); non-numbers are never in range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if above is not None and not value > above:
        return False
    if below is not None and not value < below:
        return False
    return True


def _range_text(block: dict) -> str:
    """``> 80``, ``< 10`` or ``> 10 and < 80``."""
    parts = []
    if block.get("above") is not None:
        parts.append(f"> {block['above']}")
    if block.get("below") is not None:
        parts.append(f"< {block['below']}")
    return " & ".join(parts)


def _step(path: str, kind: str, text: str, result: str, error: str = "") -> dict:
    """One trace entry."""
    return {"path": path, "kind": kind, "text": str(text)[:300], "result": result, "error": str(error)[:300],
            "at": datetime.now(timezone.utc).isoformat()}


# ============================================================================ running
class RunHandle:
    """A run in progress (used for the modes and for stopping)."""

    def __init__(self, automation_id: int):
        self.automation_id = automation_id
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None


_lock = threading.RLock()
_running: dict[int, list[RunHandle]] = collections.defaultdict(list)
_queued: dict[int, collections.deque] = collections.defaultdict(collections.deque)


def running_count(automation_id: int) -> int:
    """Runs of an automation in progress (in this server process)."""
    with _lock:
        return len([h for h in _running.get(automation_id, []) if h.thread is None or h.thread.is_alive()])


def trigger(automation, text: str, skip_conditions: bool = False, chain: int = 0,
            variables: dict | None = None) -> str:
    """Start a run of an automation, following its mode.

    Args:
        automation: The :class:`~app.models.automation.Automation`.
        text: What triggered it (shown in the run list).
        skip_conditions: Run the actions directly (*Run actions* button, other automations).
        chain: Depth of "run another automation" calls.
        variables: Values for the message placeholders (``trigger``).

    Returns:
        ``started``, ``queued``, ``restarted`` or ``skipped``.
    """
    app = current_app._get_current_object()
    aid = automation.id
    request = {"text": text, "skip": skip_conditions, "chain": chain, "vars": dict(variables or {}, trigger=text)}
    with _lock:
        active = [h for h in _running[aid] if h.thread is None or h.thread.is_alive()]
        _running[aid] = active
        mode = automation.mode
        result = "started"
        if active:
            if mode == "single":
                _record_skipped(automation, text)
                return "skipped"
            if mode == "restart":
                for h in active:
                    h.stop.set()
                result = "restarted"
            elif mode == "queued":
                if len(_queued[aid]) >= max(0, automation.max_runs - 1):
                    _record_skipped(automation, text)
                    return "skipped"
                _queued[aid].append(request)
                return "queued"
            elif mode == "parallel" and len(active) >= automation.max_runs:
                _record_skipped(automation, text)
                return "skipped"
        handle = RunHandle(aid)
        _running[aid].append(handle)
    _launch(app, handle, request)
    return result


def _launch(app, handle: RunHandle, request: dict) -> None:
    """Run in a new thread (or right here when ``AUTOMATION_SYNC`` is set)."""
    if app.config.get("AUTOMATION_SYNC"):
        _execute(app, handle, request)
        return
    handle.thread = threading.Thread(target=_execute, args=(app, handle, request),
                                     name=f"automation-{handle.automation_id}", daemon=True)
    handle.thread.start()


def _record_skipped(automation, text: str) -> None:
    """Store a *skipped* run (the automation was already running)."""
    from app.models import AutomationRun  # noqa: PLC0415

    with audit_paused():
        now = datetime.now(timezone.utc)
        db.session.add(AutomationRun(automation_id=automation.id, trigger=text[:300], status="skipped",
                                     started_at=now, finished_at=now))
        db.session.commit()


def _finish(app, handle: RunHandle) -> None:
    """Remove a finished run and start the next queued one."""
    nxt = None
    with _lock:
        if handle in _running[handle.automation_id]:
            _running[handle.automation_id].remove(handle)
        if _queued[handle.automation_id]:
            nxt = _queued[handle.automation_id].popleft()
            new = RunHandle(handle.automation_id)
            _running[handle.automation_id].append(new)
    if nxt is not None:
        _launch(app, new, nxt)


def _execute(app, handle: RunHandle, request: dict) -> None:
    """Body of a run: conditions, actions, trace."""
    from app.models import Automation, AutomationRun  # noqa: PLC0415

    with app.app_context(), audit_paused():
        automation = db.session.get(Automation, handle.automation_id)
        if automation is None:
            _finish(app, handle)
            return
        run = AutomationRun(automation_id=automation.id, trigger=request["text"][:300], status="running")
        automation.last_triggered_at = datetime.now(timezone.utc)
        db.session.add(run)
        db.session.commit()
        steps: list = [_step("trigger", "trigger", request["text"], "fired")]
        script = Script(automation, handle, steps, request)
        status, error = "finished", ""
        try:
            config = automation.config
            if not request["skip"] and not Evaluator().check_all(config["conditions"], steps):
                status = "condition"
            else:
                script.run(config["actions"], "action")
                if handle.stop.is_set():
                    status = "stopped"
        except StopRun as stop:
            status = stop.status
            if status == "error":
                error = stop.reason
            elif stop.reason:
                steps.append(_step("stop", "action", stop.reason, status))
        except Exception as exc:  # noqa: BLE001 - recorded in the run
            log.exception("automation %s failed", automation.name)
            db.session.rollback()
            status, error = "error", str(exc)
        try:
            run = db.session.get(AutomationRun, run.id)
            run.status, run.error = status, error[:500]
            run.finished_at = datetime.now(timezone.utc)
            run.trace_json = json.dumps(steps, ensure_ascii=False)
            db.session.commit()
            _prune(automation.id)
        except Exception:  # noqa: BLE001
            log.exception("storing the run of automation %s failed", automation.id)
            db.session.rollback()
        finally:
            db.session.remove()
    _finish(app, handle)


def _prune(automation_id: int) -> None:
    """Keep only the newest :data:`KEEP_RUNS` runs of an automation."""
    from app.models import AutomationRun  # noqa: PLC0415

    old = (AutomationRun.query.filter_by(automation_id=automation_id).order_by(AutomationRun.id.desc())
           .offset(KEEP_RUNS).all())
    for run in old:
        db.session.delete(run)
    if old:
        db.session.commit()


class Script:
    """Executes a list of actions, writing the trace.

    Args:
        automation: The automation.
        handle: Its run handle (stop flag).
        steps: Trace list to append to.
        request: Run request (``chain``, ``vars``).
    """

    def __init__(self, automation, handle: RunHandle, steps: list, request: dict):
        self.automation = automation
        self.name = automation.name
        self.allow_writes = bool(automation.allow_writes)
        self.handle = handle
        self.steps = steps
        self.request = request

    def _sleep(self, seconds: float) -> None:
        """Wait, ending early when the run is stopped."""
        if self.handle.stop.wait(max(0.0, seconds)):
            raise StopRun("stopped", gettext("Stopped (the automation was triggered again or disabled)"))

    def run(self, actions: list, path: str) -> None:
        """Execute actions in order."""
        for i, action in enumerate(actions or []):
            if self.handle.stop.is_set():
                raise StopRun("stopped", gettext("Stopped (the automation was triggered again or disabled)"))
            here = f"{path}/{i}"
            label = action.get("alias") or ""
            if action.get("enabled") is False:
                self.steps.append(_step(here, "action", label or action["type"], "disabled"))
                continue
            getattr(self, "do_" + action["type"])(action, here, label)

    # ------------------------------------------------------------------ actions
    def do_write(self, a: dict, path: str, label: str) -> None:
        """Write a tag (approved automations and writable tags only)."""
        from app.audit.recorder import record  # noqa: PLC0415
        from app.models import DataPoint  # noqa: PLC0415
        from app.plc.service import write_datapoint  # noqa: PLC0415

        dp = db.session.get(DataPoint, a["tag"])
        if dp is None:
            raise ValueError(gettext("tag #{0} no longer exists", a["tag"]))
        text = label or f"{dp.label} ← {a['value']}"
        if not self.allow_writes:
            self.steps.append(_step(path, "action", text, "error",
                                    gettext("This automation is not approved for writing tags.")))
            raise StopRun("error", gettext("This automation is not approved for writing tags (an administrator "
                                           "must allow it in the automation's settings)."))
        if not dp.writable or dp.is_local:
            self.steps.append(_step(path, "action", text, "error", gettext("tag '{0}' is read-only", dp.label)))
            raise StopRun("error", gettext("tag '{0}' is read-only", dp.label))
        value = convert_for_write(dp, a["value"])
        before = (read_values([dp]).get(dp.id) or {}).get("value")
        result = write_datapoint(dp, value)
        record("write", "Value", dp.id, f"{dp.label} ({dp.plc.name if dp.plc else ''} {dp.address})",
               [{"field": "value", "before": before, "after": result.get("value", value)}],
               username=f"Automation: {self.name}")
        self.steps.append(_step(path, "action", text, "ok", "" if result.get("quality") == "good" else result.get("error") or ""))

    def do_calculate(self, a: dict, path: str, label: str) -> None:
        """Calculate tags (see :mod:`app.automations.calc`): all lines first, then the writes."""
        from app.audit.recorder import record  # noqa: PLC0415
        from app.automations.calc import CalcError, _fmt, plan  # noqa: PLC0415
        from app.plc.service import write_datapoint  # noqa: PLC0415

        text = label or gettext("Calculate")
        if not self.allow_writes:
            self.steps.append(_step(path, "action", text, "error",
                                    gettext("This automation is not approved for writing tags.")))
            raise StopRun("error", gettext("This automation is not approved for writing tags (an administrator "
                                           "must allow it in the automation's settings)."))
        try:
            writes = plan(a["expression"], lambda dp: read_values([dp]).get(dp.id),
                          clamp=a.get("on_range") == "clamp")
        except CalcError as exc:
            self.steps.append(_step(path, "action", text, "error", str(exc)))
            raise StopRun("error", gettext("Calculation not written: {0}", exc)) from None
        for i, w in enumerate(writes):
            dp, line = w["tag"], w["line"]
            here = f"{path}/line{line.number}"
            desc = f"{dp.label} = {_fmt(w['value'])}" + (f" ({w['note']})" if w["note"] else "")
            try:
                result = write_datapoint(dp, w["value"])
            except Exception as exc:  # noqa: BLE001 - PLC error / value refused by the driver
                self.steps.append(_step(here, "action", desc, "error", str(exc)))
                done = gettext("{0} of {1} line(s) were written before the error.", i, len(writes)) if i else ""
                raise StopRun("error", gettext("Writing '{0}' failed: {1}", dp.label, exc) + (" " + done if done else ""))
            record("write", "Value", dp.id, f"{dp.label} ({dp.plc.name if dp.plc else ''} {dp.address})",
                   [{"field": "value", "before": w["before"], "after": result.get("value", w["value"])}],
                   username=f"Automation: {self.name}")
            self.steps.append(_step(here, "action", f"{line.source}  →  {desc}", "ok"))

    def do_delay(self, a: dict, path: str, label: str) -> None:
        """Pause."""
        self.steps.append(_step(path, "action", label or gettext("Wait {0}", format_duration(a["duration"])), "started"))
        self._sleep(a["duration"])

    def do_wait(self, a: dict, path: str, label: str) -> None:
        """Wait until the conditions are true or the timeout passes."""
        timeout = a.get("timeout")
        deadline = time.monotonic() + timeout if timeout else None
        text = label or gettext("Wait until the conditions are true")
        while True:
            if Evaluator().check_all(a["conditions"]):
                self.steps.append(_step(path, "action", text, "true"))
                return
            if deadline is not None and time.monotonic() >= deadline:
                self.steps.append(_step(path, "action", text, "timeout"))
                if a.get("continue_on_timeout"):
                    return
                raise StopRun("stopped", gettext("Timeout while waiting"))
            self._sleep(WAIT_POLL)

    def do_condition(self, a: dict, path: str, label: str) -> None:
        """Stop unless all conditions are true."""
        if not Evaluator().check_all(a["conditions"], self.steps, path):
            raise StopRun("condition", label or gettext("A condition in the actions is not met"))

    def do_if(self, a: dict, path: str, label: str) -> None:
        """If – then – else."""
        ok = Evaluator().check_all(a["conditions"], self.steps, path + "/if")
        self.steps.append(_step(path, "action", label or gettext("If"), "then" if ok else "else"))
        self.run(a.get("then") if ok else a.get("else"), path + ("/then" if ok else "/else"))

    def do_repeat(self, a: dict, path: str, label: str) -> None:
        """Repeat actions *count* times."""
        count = int(a["count"])
        for n in range(count):
            self.steps.append(_step(f"{path}/{n}", "action", label or gettext("Repeat {0} of {1}", n + 1, count), "started"))
            self.run(a["actions"], f"{path}/{n}")

    def do_notify(self, a: dict, path: str, label: str) -> None:
        """Notification: alarm-list event or trace only."""
        from app.models import AlarmEvent  # noqa: PLC0415

        message = str(a["message"])
        for key, value in self.request.get("vars", {}).items():
            message = message.replace("{" + key + "}", str(value))
        if a.get("target", "event") == "event":
            db.session.add(AlarmEvent(alarm_id=None, name=self.name[:100], description=message[:500],
                                      severity=a.get("severity", "low"), condition=gettext("Automation")[:200],
                                      value="", source="automation"))
            db.session.commit()
        self.steps.append(_step(path, "action", label or message, "ok"))

    def do_automation(self, a: dict, path: str, label: str) -> None:
        """Run, enable or disable another automation."""
        from app.models import Automation  # noqa: PLC0415

        other = db.session.get(Automation, a["automation"])
        if other is None:
            raise ValueError(gettext("automation #{0} no longer exists", a["automation"]))
        what = a.get("action", "run")
        if what == "run":
            if self.request.get("chain", 0) >= MAX_CHAIN:
                raise StopRun("error", gettext("Too many automations calling each other (at most {0}).", MAX_CHAIN))
            result = trigger(other, gettext("Called by automation '{0}'", self.name), skip_conditions=True,
                             chain=self.request.get("chain", 0) + 1)
            self.steps.append(_step(path, "action", label or gettext("Run '{0}'", other.name), result))
        else:
            other.enabled = what == "enable"
            db.session.commit()
            if not other.enabled:
                stop_runs(other.id)
            self.steps.append(_step(path, "action", label or f"{other.name}: {what}", "ok"))

    def do_stop(self, a: dict, path: str, label: str) -> None:
        """End the run."""
        raise StopRun("finished", label or a.get("reason") or gettext("Stopped by a Stop action"))


def stop_runs(automation_id: int) -> int:
    """Stop the running and queued runs of an automation (e.g. when it is disabled or deleted).

    Returns:
        Number of runs signalled.
    """
    with _lock:
        _queued.pop(automation_id, None)
        handles = list(_running.get(automation_id, []))
        for h in handles:
            h.stop.set()
    return len(handles)


# ============================================================================ triggers
class _TriggerState:
    """Memory of the trigger evaluation between ticks (per automation and trigger)."""

    def __init__(self):
        self.data: dict = {}
        self.alarm_known: dict | None = None    # event id -> (acked, cleared)
        self.alarm_max_id = 0
        self.plc_state: dict = {}
        self.started = False
        self.last_tick: datetime | None = None

    def reset(self) -> None:
        """Forget everything (tests)."""
        self.__init__()


STATE = _TriggerState()


def _key(automation, index: int, block: dict) -> tuple:
    """Memory key of one trigger (changes when the trigger or the automation is edited)."""
    return automation.id, index, json.dumps(block, sort_keys=True)


def tick(now: datetime | None = None) -> list:
    """Evaluate every trigger of the enabled automations once and fire those that are due.

    Must run inside an application context.

    Args:
        now: Aware UTC time (tests pass their own clock).

    Returns:
        ``[(automation name, trigger text)]`` fired in this tick.
    """
    from app.models import PLC, Automation, DataPoint  # noqa: PLC0415
    from app.plc.driver import get_manager  # noqa: PLC0415

    now = now or utc_now()
    t = now.timestamp()
    fired: list = []
    automations = Automation.query.filter_by(enabled=True).order_by(Automation.id).all()
    jobs = []   # (automation, index, block)
    for a in automations:
        for i, block in enumerate(a.config["triggers"]):
            if block.get("enabled") is not False:
                jobs.append((a, i, block))
    alive = {_key(a, i, b) for a, i, b in jobs}
    for key in list(STATE.data):
        if key not in alive:
            del STATE.data[key]

    # ---- read the tags of tag triggers (and probe the PLCs of PLC triggers)
    tag_ids = {b["tag"] for _a, _i, b in jobs if b["type"] in ("state", "numeric_state")}
    points = {dp.id: dp for dp in DataPoint.query.filter(DataPoint.id.in_(tag_ids)).all()} if tag_ids else {}
    plc_ids = {b["plc"] for _a, _i, b in jobs if b["type"] == "plc"}
    covered = {dp.plc_id for dp in points.values()}
    manager = get_manager()
    plcs = {p.id: p for p in PLC.query.filter(PLC.id.in_(plc_ids)).all()} if plc_ids else {}
    probes = {}
    for plc in plcs.values():
        if plc.enabled and plc.id not in covered:
            probe = next((dp for dp in plc.datapoints.order_by(DataPoint.id).limit(20) if not dp.is_local), None)
            if probe is not None:
                probes[probe.id] = probe
            else:
                try:
                    manager.probe(plc)
                except Exception:  # noqa: BLE001
                    pass
    values = read_values(list(points.values()) + list(probes.values())) if (points or probes) else {}

    alarm_changes = _alarm_changes() if any(b["type"] == "alarm" for _a, _i, b in jobs) else []
    plc_changes = {}
    for plc in plcs.values():
        state = manager.status(plc)["state"] if plc.enabled else "disabled"
        prev = STATE.plc_state.get(plc.id)
        STATE.plc_state[plc.id] = state
        if prev is not None and prev != state:
            if state == "offline":
                plc_changes[plc.id] = "offline"
            elif prev == "offline" and state in ("online", "unstable"):
                plc_changes[plc.id] = "online"

    local = local_now(now)
    last_local = local_now(STATE.last_tick) if STATE.last_tick else None
    first_tick = not STATE.started
    STATE.started = True

    for a, i, b in jobs:
        key = _key(a, i, b)
        st = STATE.data.setdefault(key, {})
        texts = []
        typ = b["type"]
        if typ in ("state", "numeric_state"):
            dp = points.get(b["tag"])
            v = values.get(b["tag"])
            if dp is None or v is None or v.get("quality") != "good":
                continue
            value = v["value"]
            if typ == "state":
                texts = _state_trigger(st, dp, b, value, v.get("display"), t)
            else:
                texts = _numeric_trigger(st, dp, b, value, v.get("display"), t)
        elif typ == "time":
            texts = _time_trigger(b, local, last_local)
        elif typ == "time_pattern":
            seconds = b["every"] * {"seconds": 1, "minutes": 60, "hours": 3600}[b.get("unit", "minutes")]
            if "next" not in st:
                st["next"] = t + seconds
            elif t >= st["next"]:
                st["next"] = t + seconds
                texts = [gettext("Every {0} {1}", b["every"], gettext(b.get("unit", "minutes")))]
        elif typ == "alarm":
            for event, change in alarm_changes:
                if change != b.get("event", "raised"):
                    continue
                if b.get("alarm") and event["alarm_id"] != b["alarm"]:
                    continue
                if b.get("severity") and event["severity"] != b["severity"]:
                    continue
                texts.append(gettext("Alarm '{0}' {1}", event["name"], gettext(change)))
        elif typ == "plc":
            change = plc_changes.get(b["plc"])
            if change and change == b.get("event", "offline"):
                plc = plcs[b["plc"]]
                texts = [gettext("PLC '{0}' is offline", plc.name) if change == "offline"
                         else gettext("PLC '{0}' is back online", plc.name)]
        elif typ == "startup" and first_tick:
            texts = [gettext("Server started")]
        for text in texts:
            text = (b.get("alias") + ": " + text) if b.get("alias") else text
            fired.append((a.name, text))
            trigger(a, text)
    STATE.last_tick = now
    return fired


def _state_trigger(st: dict, dp, b: dict, value, display, t: float) -> list:
    """Tag value changed (from / to / for)."""
    if "prev" not in st:
        st["prev"], st["pending"] = value, None
        return []
    prev = st["prev"]
    if value != prev:
        st["prev"] = value
        if value_matches(dp, value, b.get("to")) and value_matches(dp, prev, b.get("from")):
            if not b.get("for"):
                st["pending"] = None
                return [gettext("'{0}' changed to {1}", dp.label, display)]
            st["pending"] = t
        else:
            st["pending"] = None
        return []
    if st.get("pending") is not None and t - st["pending"] >= b["for"]:
        st["pending"] = None
        return [gettext("'{0}' is {1} for {2}", dp.label, display, format_duration(b["for"]))]
    return []


def _numeric_trigger(st: dict, dp, b: dict, value, display, t: float) -> list:
    """Tag crossed into the range above / below (for)."""
    inside = _in_range(value, b.get("above"), b.get("below"))
    if "armed" not in st:
        st["armed"], st["since"] = not inside, None     # already in range at start: wait for a new crossing
        return []
    if not inside:
        st["armed"], st["since"] = True, None
        return []
    if not st["armed"]:
        return []
    if st["since"] is None:
        st["since"] = t
    if t - st["since"] >= (b.get("for") or 0):
        st["armed"], st["since"] = False, None
        return [gettext("'{0}' = {1} ({2})", dp.label, display, _range_text(b))]
    return []


def _time_trigger(b: dict, local: datetime, last_local: datetime | None) -> list:
    """Time of day reached since the previous tick (no catching up after long pauses)."""
    if last_local is None or local <= last_local or (local - last_local) > timedelta(minutes=5):
        return []
    target_secs = time_seconds(b["at"])
    day = last_local.date()
    while day <= local.date():
        target = datetime(day.year, day.month, day.day, tzinfo=local.tzinfo) + timedelta(seconds=target_secs)
        if last_local < target <= local and (not b.get("weekdays") or _DAYS[target.weekday()] in b["weekdays"]):
            return [gettext("Time {0}", b["at"])]
        day += timedelta(days=1)
    return []


def _alarm_changes() -> list:
    """Alarm events raised / acknowledged / cleared since the previous tick.

    Returns:
        ``[(event dict, "raised" | "acknowledged" | "cleared")]`` (alarm events only, not notifications).
    """
    from app.models import AlarmEvent  # noqa: PLC0415

    first = STATE.alarm_known is None
    known = STATE.alarm_known or {}
    q = AlarmEvent.query.filter(AlarmEvent.source == "alarm").filter(db.or_(
        AlarmEvent.cleared_at.is_(None), AlarmEvent.id > STATE.alarm_max_id,
        AlarmEvent.id.in_(list(known) or [0])))
    changes = []
    seen = {}
    for e in q.all():
        info = {"id": e.id, "alarm_id": e.alarm_id, "name": e.name, "severity": e.severity}
        acked, cleared = e.acked_at is not None, e.cleared_at is not None
        if not first:
            prev = known.get(e.id)
            if prev is None and e.id > STATE.alarm_max_id:
                changes.append((info, "raised"))
                prev = (False, False)
            if prev is not None:
                if acked and not prev[0]:
                    changes.append((info, "acknowledged"))
                if cleared and not prev[1]:
                    changes.append((info, "cleared"))
        STATE.alarm_max_id = max(STATE.alarm_max_id, e.id)
        if not cleared:
            seen[e.id] = (acked, cleared)
    STATE.alarm_known = seen
    return changes


# ============================================================================ thread
class AutomationEngine:
    """Background thread calling :func:`tick` every ``AUTOMATION_TICK`` seconds."""

    thread: threading.Thread | None = None
    _lock = threading.Lock()

    @classmethod
    def start(cls, app) -> bool:
        """Start the engine thread (or restart it if it died).

        Returns:
            ``True`` if this call started a thread.
        """
        if cls.thread is not None and cls.thread.is_alive():
            return False
        with cls._lock:
            if cls.thread is not None and cls.thread.is_alive():
                return False
            restarted = cls.thread is not None
            interval = float(app.config.get("AUTOMATION_TICK", 1.0))
            cls.thread = threading.Thread(target=cls._run, args=(app, interval), name="automation-engine",
                                          daemon=True)
            cls.thread.start()
        log.warning("Automation engine thread had stopped – restarted") if restarted else \
            log.info("Automation engine started (every %.1f s)", interval)
        return True

    @staticmethod
    def _run(app, interval: float) -> None:
        """Thread body: tick forever, logging (not raising) errors."""
        while True:
            started = time.monotonic()
            try:
                with app.app_context():
                    try:
                        with audit_paused():
                            tick()
                    except Exception:  # noqa: BLE001
                        log.exception("automation tick failed")
                        db.session.rollback()
                    finally:
                        db.session.remove()
            except Exception:  # noqa: BLE001 - never let the thread die
                log.exception("automation engine error")
            time.sleep(max(0.2, interval - (time.monotonic() - started)))
