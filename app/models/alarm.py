"""Alarm definitions and alarm events (occurrences).

* :class:`Alarm` – configuration: a condition on one PLC tag (``tag > 80``),
  a severity, an optional screen and an enabled flag.  Alarms of kind
  ``comm`` watch the communication with a PLC instead of a tag: they are
  active while the PLC is *offline* (unreachable for longer than its
  ``offline_delay``).
* :class:`AlarmEvent` – one occurrence: when it started, when and by whom it
  was acknowledged, and when/how it was closed (``auto`` when the condition
  cleared, ``manual`` when an administrator removed it).

Events keep a copy of the alarm's name, description and severity so the
history stays readable after an alarm is edited or deleted.
"""

from datetime import datetime, timezone

from sqlalchemy import or_

from app.extensions import db
from app.i18n.catalog import gettext
from app.models.user import utcnow

#: Comparison operators: code -> (symbol, description).
OPERATORS = {
    "eq": ("=", "equal to"),
    "ne": ("≠", "not equal to"),
    "gt": (">", "greater than"),
    "ge": ("≥", "greater than or equal to"),
    "lt": ("<", "less than"),
    "le": ("≤", "less than or equal to"),
}

#: Alarm kinds: ``tag`` (condition on a tag) or ``comm`` (PLC communication lost).
KINDS = {"tag": "Tag condition", "comm": "PLC communication lost"}

#: Severity levels: code -> (label, colour, rank – higher is more severe).
SEVERITIES = {
    "critical": ("Critical", "#dc3545", 3),
    "high": ("High", "#fd7e14", 2),
    "low": ("Low", "#198754", 1),
}

_TRUE = {"1", "true", "on", "yes", "active"}
_FALSE = {"0", "false", "off", "no", "inactive"}


def iso(dt) -> str | None:
    """Format a datetime as ISO 8601 UTC (naive values are treated as UTC).

    SQLite returns naive datetimes even for timezone-aware columns.

    Args:
        dt: ``datetime`` or ``None``.

    Returns:
        ISO string such as ``2026-10-15T01:02:03+00:00`` or ``None``.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def parse_setpoint(text: str, kind: str):
    """Convert a setpoint text for comparison with a tag value.

    Args:
        text: Setpoint as typed by the administrator.
        kind: ``"bool"``, ``"number"`` or ``"text"``.

    Returns:
        ``bool``, ``float`` or ``str``.

    Raises:
        ValueError: If the text does not fit the kind.
    """
    raw = str(text).strip()
    if kind == "bool":
        if raw.lower() in _TRUE:
            return True
        if raw.lower() in _FALSE:
            return False
        raise ValueError("Use true/false, 1/0 or on/off for a BOOL tag")
    if kind == "number":
        return float(raw)
    return raw


def value_kind(datapoint) -> str:
    """Classify a tag for alarm comparisons.

    Args:
        datapoint: The tag.

    Returns:
        ``"bool"``, ``"number"`` or ``"text"``.
    """
    if datapoint.is_bool:
        return "bool"
    return "number" if datapoint.is_numeric else "text"


class Alarm(db.Model):
    """An alarm definition watching one tag (``kind="tag"``) or one PLC's communication (``kind="comm"``)."""

    __tablename__ = "alarms"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(500), nullable=False, default="")
    #: ``tag`` or ``comm`` (see :data:`KINDS`).
    kind = db.Column(db.String(10), nullable=False, default="tag", server_default="tag")
    #: Watched tag (``kind="tag"`` only).
    datapoint_id = db.Column(db.Integer, db.ForeignKey("datapoints.id", ondelete="CASCADE"), nullable=True)
    #: Watched PLC (``kind="comm"`` only).
    plc_id = db.Column(db.Integer, db.ForeignKey("plcs.id", ondelete="CASCADE"), nullable=True, index=True)
    operator = db.Column(db.String(2), nullable=False, default="gt")
    setpoint = db.Column(db.String(100), nullable=False, default="0")
    severity = db.Column(db.String(10), nullable=False, default="high")
    #: Screen opened by "Open Screen" (optional).
    screen_id = db.Column(db.Integer, db.ForeignKey("dashboards.id", ondelete="SET NULL"), nullable=True)
    #: Disabled alarms are not evaluated.
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    #: Set after a manual removal while the condition was still true; cleared
    #: when the condition goes false, so the alarm does not re-fire immediately.
    suppressed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    datapoint = db.relationship("DataPoint", backref=db.backref("alarms", cascade="all, delete-orphan", lazy="dynamic"))
    plc = db.relationship("PLC", back_populates="comm_alarms")
    screen = db.relationship("Dashboard")
    events = db.relationship("AlarmEvent", back_populates="alarm", lazy="dynamic")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<Alarm High temp>``."""
        return f"<Alarm {self.name}>"

    # --- presentation --------------------------------------------------------
    @property
    def severity_label(self) -> str:
        """``Critical``, ``High`` or ``Low``."""
        return SEVERITIES.get(self.severity, SEVERITIES["high"])[0]

    @property
    def severity_color(self) -> str:
        """Hex colour of the severity (red, orange, green)."""
        return SEVERITIES.get(self.severity, SEVERITIES["high"])[1]

    @property
    def is_comm(self) -> bool:
        """``True`` for a PLC communication alarm."""
        return self.kind == "comm"

    @property
    def source_text(self) -> str:
        """Where the alarm comes from: ``PLC · address`` or ``PLC (endpoint)``."""
        if self.is_comm:
            return f"{self.plc.name} ({self.plc.endpoint})" if self.plc else gettext("deleted PLC")
        dp = self.datapoint
        return f"{dp.plc.name} · {dp.address_text or dp.address}" if dp else gettext("deleted tag")

    @property
    def condition_text(self) -> str:
        """Human readable condition, e.g. ``Tank temp > 80 °C`` or ``No communication with PLC1 > 10 s``."""
        if self.is_comm:
            delay = f"{self.plc.offline_delay:g}" if self.plc else "?"
            return gettext("No communication with {0} for {1} s", self.plc.name if self.plc else "?", delay)
        dp = self.datapoint
        symbol = OPERATORS.get(self.operator, ("?",))[0]
        unit = f" {dp.unit}" if dp is not None and dp.unit else ""
        return f"{dp.label if dp else '?'} {symbol} {self.setpoint}{unit}"

    @property
    def open_event(self):
        """The currently open (not closed) event, or ``None``."""
        return self.events.filter(AlarmEvent.cleared_at.is_(None)).order_by(AlarmEvent.id.desc()).first()

    @property
    def is_active(self) -> bool:
        """``True`` while an event of this alarm is open."""
        return self.open_event is not None

    # --- logic -----------------------------------------------------------------
    def evaluate(self, value) -> bool:
        """Test the condition against a tag value (engineering units).

        Args:
            value: Current tag value (bool, number or text).

        Returns:
            ``True`` when the alarm condition is met.  Values that cannot be
            compared (e.g. ``None``) never trigger.
        """
        if value is None or self.datapoint is None:
            return False
        kind = value_kind(self.datapoint)
        try:
            target = parse_setpoint(self.setpoint, kind)
            current = bool(value) if kind == "bool" else float(value) if kind == "number" else str(value)
        except (TypeError, ValueError):
            return False
        op = self.operator
        if op == "eq":
            return current == target
        if op == "ne":
            return current != target
        if op == "gt":
            return current > target
        if op == "ge":
            return current >= target
        if op == "lt":
            return current < target
        if op == "le":
            return current <= target
        return False

    @classmethod
    def default_comm_name(cls, plc) -> str:
        """A free alarm name for a PLC's communication alarm.

        Args:
            plc: The PLC.

        Returns:
            ``"<PLC> communication lost"`` (with `` (2)``… if taken).
        """
        base = f"{plc.name} communication lost"[:96]
        name, n = base, 2
        while cls.query.filter(cls.name == name, or_(cls.plc_id.is_(None), cls.plc_id != plc.id)).first() is not None:
            name, n = f"{base} ({n})", n + 1
        return name

    @classmethod
    def sync_comm_alarm(cls, plc, enabled: bool, severity: str, old_name: str | None = None) -> "Alarm | None":
        """Create, update or disable the communication alarm of a PLC (PLC form).

        A PLC whose box is unticked keeps its alarm, disabled, so its
        settings and history link survive.

        Args:
            plc: The PLC (flushed, so it has an id).
            enabled: Whether communication loss should raise an alarm.
            severity: Severity code.
            old_name: The PLC's previous name; a default alarm name that
                contains it is renamed along with the PLC.

        Returns:
            The alarm, or ``None`` if none exists and none is wanted.
        """
        alarm = plc.comm_alarm
        if alarm is None:
            if not enabled:
                return None
            alarm = cls(kind="comm", plc=plc, name=cls.default_comm_name(plc), operator="eq", setpoint="",
                        description=f"No communication with PLC '{plc.name}'. Check the network, "
                                    "the PLC power and the PLC's connection settings.")
            db.session.add(alarm)
        elif old_name and old_name != plc.name and alarm.name.startswith(f"{old_name} communication lost"):
            alarm.name = cls.default_comm_name(plc)
            alarm.description = alarm.description.replace(f"'{old_name}'", f"'{plc.name}'")
        if alarm.enabled != enabled:
            alarm.suppressed = False
        alarm.enabled = enabled
        alarm.severity = severity if severity in SEVERITIES else "high"
        return alarm

    @staticmethod
    def release_events(alarms, user=None) -> None:
        """Prepare alarms for deletion while keeping their history.

        Open events are closed manually (by ``user``) and every event is
        detached (``alarm_id = NULL``) – done explicitly because SQLite does
        not enforce ``ON DELETE SET NULL``.

        Args:
            alarms: Alarms about to be deleted.
            user: The user deleting them (or ``None``).
        """
        for alarm in alarms:
            event = alarm.open_event
            if event is not None:
                event.close("manual", user)
            AlarmEvent.query.filter_by(alarm_id=alarm.id).update({"alarm_id": None})

    def to_dict(self) -> dict:
        """Serialise the definition.

        Returns:
            JSON-ready dict.
        """
        return {"id": self.id, "name": self.name, "description": self.description, "kind": self.kind,
                "severity": self.severity,
                "severity_label": self.severity_label, "color": self.severity_color,
                "condition": self.condition_text, "enabled": self.enabled}


class AlarmEvent(db.Model):
    """One alarm occurrence."""

    __tablename__ = "alarm_events"

    id = db.Column(db.Integer, primary_key=True)
    alarm_id = db.Column(db.Integer, db.ForeignKey("alarms.id", ondelete="SET NULL"), nullable=True, index=True)
    # Snapshot of the alarm at the time it occurred.
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=False, default="")
    severity = db.Column(db.String(10), nullable=False, default="high")
    condition = db.Column(db.String(200), nullable=False, default="")
    value = db.Column(db.String(100), nullable=False, default="")
    screen_id = db.Column(db.Integer, db.ForeignKey("dashboards.id", ondelete="SET NULL"), nullable=True)

    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    acked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    acked_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cleared_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    #: ``auto`` (condition no longer true) or ``manual`` (removed by a user).
    close_type = db.Column(db.String(10), nullable=True)
    closed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    alarm = db.relationship("Alarm", back_populates="events")
    screen = db.relationship("Dashboard")
    acked_by = db.relationship("User", foreign_keys=[acked_by_id])
    closed_by = db.relationship("User", foreign_keys=[closed_by_id])

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<AlarmEvent 5 High temp>``."""
        return f"<AlarmEvent {self.id} {self.name}>"

    @property
    def is_open(self) -> bool:
        """``True`` until the event is closed (automatically or manually)."""
        return self.cleared_at is None

    @property
    def severity_label(self) -> str:
        """``Critical``, ``High`` or ``Low``."""
        return SEVERITIES.get(self.severity, SEVERITIES["high"])[0]

    @property
    def severity_color(self) -> str:
        """Hex colour of the severity."""
        return SEVERITIES.get(self.severity, SEVERITIES["high"])[1]

    @property
    def close_text(self) -> str:
        """``Automatic``, ``Manual (username)`` or ``""`` while open."""
        if self.close_type == "auto":
            return gettext("Automatic")
        if self.close_type == "manual":
            return gettext("Manual ({0})", self.closed_by.username if self.closed_by else gettext("deleted user"))
        return ""

    def acknowledge(self, user) -> bool:
        """Acknowledge the event (only the first acknowledgement counts).

        Args:
            user: The acknowledging user.

        Returns:
            ``True`` if this call acknowledged it.
        """
        if self.acked_at is not None:
            return False
        self.acked_at = utcnow()
        self.acked_by_id = getattr(user, "id", None)
        return True

    def close(self, close_type: str, user=None) -> None:
        """Close the event.

        Args:
            close_type: ``"auto"`` or ``"manual"``.
            user: The user for manual closes.
        """
        if self.cleared_at is None:
            self.cleared_at = utcnow()
            self.close_type = close_type
            self.closed_by_id = getattr(user, "id", None) if close_type == "manual" else None

    def to_dict(self, screen_url: str | None = None) -> dict:
        """Serialise for the alarm popup / events list.

        Args:
            screen_url: URL of the screen with the alarm banner, if any.

        Returns:
            JSON-ready dict (times are ISO 8601 UTC).
        """
        return {
            "id": self.id, "alarm_id": self.alarm_id, "name": self.name, "description": self.description,
            "severity": self.severity, "severity_label": self.severity_label, "color": self.severity_color,
            "condition": self.condition, "value": self.value,
            "occurred_at": iso(self.occurred_at), "acked_at": iso(self.acked_at),
            "acked_by": self.acked_by.username if self.acked_by else None,
            "cleared_at": iso(self.cleared_at), "close_type": self.close_type, "close_text": self.close_text,
            "screen_url": screen_url,
        }


def now_utc() -> datetime:
    """Current UTC time (shortcut used by the alarm engine).

    Returns:
        Timezone-aware ``datetime``.
    """
    return datetime.now(timezone.utc)
