"""Building blocks of automations and the validation of their configuration.

An automation's configuration is plain JSON (also edited as YAML)::

    {"triggers":   [{"type": "numeric_state", "tag": 4, "above": 80, "for": 30}],
     "conditions": [{"type": "time", "after": "06:00:00", "before": "22:00:00"}],
     "actions":    [{"type": "write", "tag": 7, "value": "false"},
                    {"type": "notify", "message": "Pump stopped: tank too hot", "severity": "high"}]}

:data:`TRIGGERS`, :data:`CONDITIONS` and :data:`ACTIONS` describe every block
type with its fields.  The same description is sent to the browser, where the
visual editor builds its forms from it, so the editor and the validation can
never disagree.

Every block may also have ``alias`` (a name shown in the editor and the run
traces) and ``enabled`` (``false`` = skipped).

References to tags, alarms, PLCs and automations are stored as ids.  In YAML
they may also be written as names (``tag: Pump running``); :func:`validate`
resolves them.
"""

from __future__ import annotations

import re

from app.i18n.catalog import gettext

#: Week days (``mon`` … ``sun``) in Python's ``weekday()`` order.
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_LABELS = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}
#: Deepest nesting of blocks (``if`` inside ``repeat`` inside …).
MAX_DEPTH = 8
#: Most blocks in one automation.
MAX_BLOCKS = 500
#: Severities of notifications and alarm filters.
SEVERITY_OPTIONS = [("critical", "Critical"), ("high", "High"), ("low", "Low")]


def _f(key, kind, label, required=False, **extra) -> dict:
    """Field description (see the module docstring)."""
    field = {"key": key, "kind": kind, "label": label, "required": required}
    field.update(extra)
    return field


#: Trigger types: type -> {"label", "help", "icon", "fields"}.
TRIGGERS: dict[str, dict] = {
    "state": {"label": "Tag value changed", "icon": "arrow-left-right",
              "help": "Fires when the value of a tag changes (optionally from / to a given value).",
              "fields": [_f("tag", "tag", "Tag", True), _f("from", "text", "From (any if empty)"),
                         _f("to", "text", "To (any if empty)"), _f("for", "duration", "For at least")]},
    "numeric_state": {"label": "Tag above / below a limit", "icon": "graph-up-arrow",
                      "help": "Fires when a number crosses into the range: above and/or below the limits.",
                      "fields": [_f("tag", "tag", "Tag", True, numeric=True), _f("above", "number", "Above"),
                                 _f("below", "number", "Below"), _f("for", "duration", "For at least")]},
    "time": {"label": "Time of day", "icon": "clock",
             "help": "Fires every day (or on the chosen week days) at a fixed time.",
             "fields": [_f("at", "time", "At", True), _f("weekdays", "weekdays", "Week days (every day if none)")]},
    "time_pattern": {"label": "Every … (interval)", "icon": "arrow-repeat",
                     "help": "Fires at a fixed interval, e.g. every 15 minutes.",
                     "fields": [_f("every", "number", "Every", True, min=1),
                                _f("unit", "select", "Unit", True, default="minutes",
                                   options=[["seconds", "seconds"], ["minutes", "minutes"], ["hours", "hours"]])]},
    "alarm": {"label": "Alarm event", "icon": "bell",
              "help": "Fires when an alarm is raised, acknowledged or cleared.",
              "fields": [_f("event", "select", "When the alarm is", True, default="raised",
                            options=[["raised", "raised"], ["acknowledged", "acknowledged"], ["cleared", "cleared"]]),
                         _f("alarm", "alarm", "Alarm (any if empty)"),
                         _f("severity", "select", "Severity (any if empty)", options=SEVERITY_OPTIONS)]},
    "plc": {"label": "PLC communication", "icon": "hdd-network",
            "help": "Fires when a PLC goes offline or comes back online.",
            "fields": [_f("plc", "plc", "PLC", True),
                       _f("event", "select", "When the PLC is", True, default="offline",
                          options=[["offline", "offline"], ["online", "back online"]])]},
    "startup": {"label": "Server started", "icon": "power",
                "help": "Fires once when pyPLCHMI starts.", "fields": []},
}

#: Condition types.
CONDITIONS: dict[str, dict] = {
    "state": {"label": "Tag value is", "icon": "check2-square",
              "help": "True while the tag has the given value.",
              "fields": [_f("tag", "tag", "Tag", True), _f("state", "text", "Value", True)]},
    "numeric_state": {"label": "Tag above / below", "icon": "graph-up",
                      "help": "True while the number is above and/or below the limits.",
                      "fields": [_f("tag", "tag", "Tag", True, numeric=True), _f("above", "number", "Above"),
                                 _f("below", "number", "Below")]},
    "time": {"label": "Time window", "icon": "clock-history",
             "help": "True between the times (may pass midnight) and on the chosen week days.",
             "fields": [_f("after", "time", "After"), _f("before", "time", "Before"),
                        _f("weekdays", "weekdays", "Week days (every day if none)")]},
    "alarm_active": {"label": "Alarm is active", "icon": "bell-fill",
                     "help": "True while an alarm (or any alarm of a severity) is active.",
                     "fields": [_f("alarm", "alarm", "Alarm (any if empty)"),
                                _f("severity", "select", "Severity (any if empty)", options=SEVERITY_OPTIONS)]},
    "and": {"label": "And – all of", "icon": "diagram-3", "help": "True when every condition is true.",
            "fields": [_f("conditions", "conditions", "Conditions", True)]},
    "or": {"label": "Or – any of", "icon": "diagram-2", "help": "True when at least one condition is true.",
           "fields": [_f("conditions", "conditions", "Conditions", True)]},
    "not": {"label": "Not – none of", "icon": "slash-circle", "help": "True when no condition is true.",
            "fields": [_f("conditions", "conditions", "Conditions", True)]},
}

#: Action types.
ACTIONS: dict[str, dict] = {
    "write": {"label": "Write tag", "icon": "pencil-square",
              "help": "Writes a value to a writable PLC tag. The automation must be approved for writing "
                      "by an administrator.",
              "fields": [_f("tag", "tag_writable", "Tag", True), _f("value", "text", "Value", True)]},
    "delay": {"label": "Wait for a time", "icon": "hourglass-split", "help": "Pauses the actions.",
              "fields": [_f("duration", "duration", "Duration", True)]},
    "wait": {"label": "Wait until", "icon": "pause-circle",
             "help": "Waits until all conditions are true (or the timeout passes).",
             "fields": [_f("conditions", "conditions", "Conditions", True), _f("timeout", "duration", "Timeout"),
                        _f("continue_on_timeout", "bool", "Continue after the timeout")]},
    "condition": {"label": "Condition", "icon": "funnel",
                  "help": "Stops the run here unless all conditions are true.",
                  "fields": [_f("conditions", "conditions", "Conditions", True)]},
    "if": {"label": "If – then – else", "icon": "signpost-split",
           "help": "Runs the 'then' actions when all conditions are true, otherwise the 'else' actions.",
           "fields": [_f("conditions", "conditions", "If", True), _f("then", "actions", "Then", True),
                      _f("else", "actions", "Else")]},
    "repeat": {"label": "Repeat", "icon": "repeat", "help": "Runs the actions a number of times.",
               "fields": [_f("count", "number", "Times", True, min=1, max=1000), _f("actions", "actions", "Actions", True)]},
    "notify": {"label": "Notification", "icon": "chat-left-text",
               "help": "Shows a message to the operators like an alarm (closed when acknowledged), or only "
                       "writes it to the run trace. {trigger} is replaced by what started the run.",
               "fields": [_f("message", "textarea", "Message", True),
                          _f("severity", "select", "Severity", True, default="low", options=SEVERITY_OPTIONS),
                          _f("target", "select", "Show", True, default="event",
                             options=[["event", "to the operators (alarm list)"], ["log", "only in the run trace"]])]},
    "automation": {"label": "Other automation", "icon": "diagram-3",
                   "help": "Runs, enables or disables another automation.",
                   "fields": [_f("automation", "automation", "Automation", True),
                              _f("action", "select", "Do", True, default="run",
                                 options=[["run", "run its actions"], ["enable", "enable"], ["disable", "disable"]])]},
    "stop": {"label": "Stop", "icon": "stop-circle", "help": "Ends the run.",
             "fields": [_f("reason", "text", "Reason")]},
}

KINDS = {"triggers": TRIGGERS, "conditions": CONDITIONS, "actions": ACTIONS}


class ConfigError(ValueError):
    """The configuration is invalid; ``errors`` lists ``"path: message"`` texts."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def editor_schema() -> dict:
    """Block descriptions for the browser editor, with translated texts.

    Returns:
        ``{"triggers": {type: spec}, "conditions": {...}, "actions": {...}, "weekdays": [[code, label]]}``.
    """

    def tr(spec: dict) -> dict:
        """Translate the texts of one block description."""
        fields = []
        for f in spec["fields"]:
            g = dict(f, label=gettext(f["label"]))
            if "options" in g:
                g["options"] = [[v, gettext(label)] for v, label in g["options"]]
            fields.append(g)
        return {"label": gettext(spec["label"]), "help": gettext(spec["help"]), "icon": spec["icon"], "fields": fields}

    out = {kind: {t: tr(spec) for t, spec in types.items()} for kind, types in KINDS.items()}
    out["weekdays"] = [[d, gettext(WEEKDAY_LABELS[d])] for d in WEEKDAYS]
    return out


# --------------------------------------------------------------------------- value parsing
def parse_duration(value) -> float | None:
    """Seconds from ``30``, ``"30"``, ``"1:30"`` (m:s), ``"01:02:03"`` or ``{"hours", "minutes", "seconds"}``.

    Args:
        value: Duration in one of the accepted forms; empty → ``None``.

    Returns:
        Seconds (≥ 0) or ``None``.

    Raises:
        ValueError: For other texts or negative values.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(gettext("not a duration"))
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, dict):
        seconds = (float(value.get("hours") or 0) * 3600 + float(value.get("minutes") or 0) * 60
                   + float(value.get("seconds") or 0))
    else:
        text = str(value).strip()
        if re.fullmatch(r"\d+(\.\d+)?", text):
            seconds = float(text)
        elif re.fullmatch(r"\d+:\d{1,2}(:\d{1,2}(\.\d+)?)?", text):
            parts = [float(p) for p in text.split(":")]
            if len(parts) == 2:
                parts = [0.0] + parts
            seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            raise ValueError(gettext("'{0}' is not a duration (seconds or HH:MM:SS)", text))
    if seconds < 0:
        raise ValueError(gettext("a duration cannot be negative"))
    return round(seconds, 3)


def format_duration(seconds) -> str:
    """``90`` → ``"00:01:30"`` (fractions of a second are kept)."""
    if seconds is None:
        return ""
    seconds = float(seconds)
    whole = int(seconds)
    text = f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"
    frac = round(seconds - whole, 3)
    return text + (f"{frac:.3f}"[1:].rstrip("0") if frac else "")


def parse_time(value) -> str:
    """Normalise a time of day to ``HH:MM:SS``.

    Args:
        value: ``"7:30"``, ``"07:30:15"`` (YAML may also give seconds since midnight as int).

    Returns:
        ``HH:MM:SS``.

    Raises:
        ValueError: For invalid times.
    """
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 86400:
        return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"
    text = str(value or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59 or int(m.group(3) or 0) > 59:
        raise ValueError(gettext("'{0}' is not a time of day (HH:MM or HH:MM:SS)", text))
    return f"{int(m.group(1)):02d}:{m.group(2)}:{int(m.group(3) or 0):02d}"


def time_seconds(text: str) -> int:
    """Seconds since midnight of a normalised ``HH:MM:SS``."""
    h, m, s = (int(p) for p in text.split(":"))
    return h * 3600 + m * 60 + s


# --------------------------------------------------------------------------- reference lookup
class Lookup:
    """Resolves tag / alarm / PLC / automation references (id or name) and checks they exist.

    Args:
        exclude_automation: Id of the automation being edited (it may not call itself).
    """

    def __init__(self, exclude_automation: int | None = None):
        from app.models import PLC, Alarm, Automation, DataPoint  # noqa: PLC0415

        self.models = {"tag": DataPoint, "tag_writable": DataPoint, "alarm": Alarm, "plc": PLC,
                       "automation": Automation}
        self.exclude = exclude_automation
        self._cache: dict = {}

    def resolve(self, kind: str, ref):
        """Find the referenced object.

        Args:
            kind: Field kind (``tag``, ``tag_writable``, ``alarm``, ``plc``, ``automation``).
            ref: Id (int or digits) or name.

        Returns:
            The model object.

        Raises:
            ValueError: If nothing (or more than one object) matches.
        """
        from app.extensions import db  # noqa: PLC0415

        model = self.models[kind]
        key = (kind, ref)
        if key in self._cache:
            return self._cache[key]
        obj = None
        if isinstance(ref, int) and not isinstance(ref, bool) or (isinstance(ref, str) and ref.strip().isdigit()):
            obj = db.session.get(model, int(ref))
        elif isinstance(ref, str) and ref.strip():
            column = model.label if kind.startswith("tag") else model.name
            found = model.query.filter(column == ref.strip()).limit(2).all()
            if len(found) > 1:
                raise ValueError(gettext("'{0}' is ambiguous – use the id", ref))
            obj = found[0] if found else None
        if obj is None:
            raise ValueError(gettext("'{0}' does not exist", ref))
        if kind == "tag_writable" and not obj.writable:
            raise ValueError(gettext("tag '{0}' is read-only", obj.label))
        if kind == "tag_writable" and obj.is_local:
            raise ValueError(gettext("tag '{0}' is a local dummy tag (its value lives in each browser window)", obj.label))
        if kind == "tag" and obj.is_local:
            raise ValueError(gettext("tag '{0}' is a local dummy tag (its value lives in each browser window)", obj.label))
        if kind == "automation" and self.exclude is not None and obj.id == self.exclude:
            raise ValueError(gettext("an automation cannot call itself"))
        self._cache[key] = obj
        return obj


# --------------------------------------------------------------------------- validation
def validate(data, lookup: Lookup | None = None) -> dict:
    """Check and normalise a configuration.

    Args:
        data: ``{"triggers", "conditions", "actions"}`` (from the editor or YAML).
        lookup: Reference resolver (a new one when omitted).

    Returns:
        The cleaned configuration (ids for references, numbers as numbers,
        durations in seconds, times as ``HH:MM:SS``).

    Raises:
        ConfigError: With every problem found.
    """
    lookup = lookup or Lookup()
    errors: list[str] = []
    counter = {"n": 0}
    if not isinstance(data, dict):
        raise ConfigError([gettext("The automation must be an object with triggers, conditions and actions.")])
    out = {}
    for kind in ("triggers", "conditions", "actions"):
        out[kind] = _blocks(data.get(kind), kind, kind, 0, lookup, errors, counter)
    if not out["actions"]:
        errors.append(gettext("Add at least one action."))
    if counter["n"] > MAX_BLOCKS:
        errors.append(gettext("Too many blocks (at most {0}).", MAX_BLOCKS))
    if errors:
        raise ConfigError(errors)
    return out


def _blocks(items, kind: str, path: str, depth: int, lookup: Lookup, errors: list, counter: dict) -> list:
    """Validate a list of blocks of one kind."""
    if items in (None, ""):
        return []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        errors.append(f"{path}: " + gettext("must be a list"))
        return []
    if depth > MAX_DEPTH:
        errors.append(f"{path}: " + gettext("nested too deeply (at most {0} levels)", MAX_DEPTH))
        return []
    out = []
    for i, item in enumerate(items):
        counter["n"] += 1
        here = f"{path}[{i + 1}]"
        if not isinstance(item, dict) or not item.get("type"):
            errors.append(f"{here}: " + gettext("each block needs a 'type'"))
            continue
        spec = KINDS[kind].get(item["type"])
        if spec is None:
            errors.append(f"{here}: " + gettext("unknown type '{0}' (allowed: {1})", item["type"], ", ".join(KINDS[kind])))
            continue
        block = {"type": item["type"]}
        if item.get("alias"):
            block["alias"] = str(item["alias"])[:120]
        if item.get("enabled") is False:
            block["enabled"] = False
        known = {"type", "alias", "enabled"} | {f["key"] for f in spec["fields"]}
        for extra in sorted(set(item) - known):
            errors.append(f"{here}: " + gettext("unknown field '{0}'", extra))
        for field in spec["fields"]:
            value = item.get(field["key"])
            try:
                cleaned = _field(field, value, kind, f"{here}.{field['key']}", depth, lookup, errors, counter)
            except ValueError as exc:
                errors.append(f"{here}.{field['key']}: {exc}")
                continue
            if cleaned in (None, "", []):
                if field["required"]:
                    errors.append(f"{here}.{field['key']}: " + gettext("is required"))
                continue
            block[field["key"]] = cleaned
        _cross_check(block, here, errors)
        out.append(block)
    return out


def _field(field: dict, value, kind: str, path: str, depth: int, lookup: Lookup, errors: list, counter: dict):
    """Validate one field value (raises ValueError for simple problems)."""
    fk = field["kind"]
    if fk in ("conditions", "actions"):
        return _blocks(value, fk, path, depth + 1, lookup, errors, counter)
    if value is None or value == "":
        return field.get("default") if field["required"] else None
    if fk in ("tag", "tag_writable", "alarm", "plc", "automation"):
        obj = lookup.resolve(fk, value)
        if field.get("numeric") and not obj.is_numeric:
            raise ValueError(gettext("tag '{0}' is not a number ({1})", obj.label, obj.data_type))
        return obj.id
    if fk == "number":
        if isinstance(value, bool):
            raise ValueError(gettext("must be a number"))
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(gettext("must be a number")) from None
        if "min" in field and number < field["min"]:
            raise ValueError(gettext("must be at least {0}", field["min"]))
        if "max" in field and number > field["max"]:
            raise ValueError(gettext("must be at most {0}", field["max"]))
        return int(number) if number.is_integer() else number
    if fk == "duration":
        return parse_duration(value)
    if fk == "time":
        return parse_time(value)
    if fk == "weekdays":
        days = value if isinstance(value, list) else [value]
        days = [str(d).strip().lower()[:3] for d in days if str(d).strip()]
        bad = [d for d in days if d not in WEEKDAYS]
        if bad:
            raise ValueError(gettext("unknown week day(s): {0} (use mon … sun)", ", ".join(bad)))
        return [d for d in WEEKDAYS if d in days]
    if fk == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if fk == "select":
        allowed = [v for v, _ in field["options"]]
        if str(value) not in allowed:
            raise ValueError(gettext("must be one of: {0}", ", ".join(allowed)))
        return str(value)
    if fk in ("text", "textarea"):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)[:1000]
    raise ValueError(f"unsupported field kind {fk}")


def _cross_check(block: dict, path: str, errors: list) -> None:
    """Rules between fields of one block."""
    if block["type"] == "numeric_state":
        above, below = block.get("above"), block.get("below")
        if above is None and below is None:
            errors.append(f"{path}: " + gettext("set 'above', 'below' or both"))
        elif above is not None and below is not None and above >= below:
            errors.append(f"{path}: " + gettext("'above' must be smaller than 'below'"))
    if block["type"] == "time_pattern":
        seconds = block.get("every", 0) * {"seconds": 1, "minutes": 60, "hours": 3600}[block.get("unit", "minutes")]
        if seconds < 1:
            errors.append(f"{path}: " + gettext("the interval must be at least 1 second"))
    if block["type"] == "time" and "after" not in block and "before" not in block and "weekdays" not in block \
            and "at" not in block:
        errors.append(f"{path}: " + gettext("set a time window or week days"))


# --------------------------------------------------------------------------- helpers
def walk_actions(actions: list):
    """Yield every action, including those nested in if / repeat blocks."""
    for action in actions or []:
        yield action
        for key in ("then", "else", "actions"):
            if isinstance(action.get(key), list):
                yield from walk_actions(action[key])


def references(config: dict) -> dict:
    """Ids referenced by a configuration: ``{"tag": set, "alarm": set, "plc": set, "automation": set}``."""
    found = {"tag": set(), "alarm": set(), "plc": set(), "automation": set()}

    def visit(blocks, kind):
        for b in blocks or []:
            spec = KINDS[kind].get(b.get("type"), {"fields": []})
            for f in spec["fields"]:
                v = b.get(f["key"])
                if v is None:
                    continue
                if f["kind"] in ("conditions", "actions"):
                    visit(v, f["kind"])
                elif f["kind"] in ("tag", "tag_writable"):
                    found["tag"].add(v)
                elif f["kind"] in found:
                    found[f["kind"]].add(v)

    for kind in ("triggers", "conditions", "actions"):
        visit(config.get(kind), kind)
    return found
