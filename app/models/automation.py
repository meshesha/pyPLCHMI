"""Automations ("when … and if … then do …") and their run history.

* :class:`Automation` – name, mode and the configuration: ``triggers``,
  ``conditions`` and ``actions`` (JSON, validated by
  :mod:`app.automations.schema`).  ``allow_writes`` is the administrator's
  approval for writing PLC tags; without it every *Write tag* action is
  refused at run time.
* :class:`AutomationRun` – one run: what triggered it, when it started and
  ended, its result and a step-by-step trace.
"""

from __future__ import annotations

import json

from app.extensions import db
from app.models.user import utcnow

#: Run modes (what happens when the automation is triggered while it is still running).
MODES = {
    "single": "Single – ignore new triggers while running",
    "restart": "Restart – stop the running actions and start again",
    "queued": "Queued – run again after the current run",
    "parallel": "Parallel – start another run at the same time",
}

#: Result of a run: code -> label.
RUN_STATUSES = {
    "running": "Running",
    "finished": "Finished",
    "condition": "Conditions not met",
    "stopped": "Stopped",
    "error": "Error",
    "skipped": "Skipped (already running)",
}


class Automation(db.Model):
    """An automation: triggers, conditions and actions."""

    __tablename__ = "automations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(500), nullable=False, default="", server_default="")
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    mode = db.Column(db.String(10), nullable=False, default="single", server_default="single")
    #: Most runs at the same time / waiting in the queue (modes ``queued`` and ``parallel``).
    max_runs = db.Column(db.Integer, nullable=False, default=10, server_default="10")
    #: JSON ``{"triggers": [...], "conditions": [...], "actions": [...]}``.
    config_json = db.Column(db.Text, nullable=False, default="{}")
    #: Administrator's approval: the automation may write PLC tags (writable tags only).
    allow_writes = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    last_triggered_at = db.Column(db.DateTime(timezone=True), nullable=True)

    runs = db.relationship("AutomationRun", back_populates="automation", cascade="all, delete-orphan",
                           lazy="dynamic", order_by="AutomationRun.id.desc()")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<Automation 3 Night mode>``."""
        return f"<Automation {self.id} {self.name}>"

    @property
    def config(self) -> dict:
        """The configuration as a dict (empty lists when unset)."""
        try:
            data = json.loads(self.config_json or "{}")
        except ValueError:
            data = {}
        return {"triggers": data.get("triggers") or [], "conditions": data.get("conditions") or [],
                "actions": data.get("actions") or []}

    @config.setter
    def config(self, value: dict) -> None:
        """Store a (validated) configuration."""
        self.config_json = json.dumps(value, ensure_ascii=False, sort_keys=False)

    @property
    def mode_label(self) -> str:
        """Short name of the mode (``Single``, ``Restart`` …)."""
        return MODES.get(self.mode, self.mode).split(" – ")[0]

    def writes_tags(self) -> bool:
        """Whether any action (also nested ones) writes a PLC tag (Write tag or Calculate)."""
        from app.automations.schema import walk_actions  # noqa: PLC0415

        return any(a.get("type") in ("write", "calculate") for a in walk_actions(self.config["actions"]))

    def to_document(self) -> dict:
        """Export form (used for YAML export/import and the text editor).

        Returns:
            ``{"name", "description", "enabled", "mode", "max_runs", "triggers", "conditions", "actions"}``.
        """
        doc = {"name": self.name, "description": self.description, "enabled": bool(self.enabled),
               "mode": self.mode}
        if self.mode in ("queued", "parallel"):
            doc["max_runs"] = self.max_runs
        doc.update(self.config)
        return doc


class AutomationRun(db.Model):
    """One run of an automation, with its trace."""

    __tablename__ = "automation_runs"

    id = db.Column(db.Integer, primary_key=True)
    automation_id = db.Column(db.Integer, db.ForeignKey("automations.id", ondelete="CASCADE"), nullable=False,
                              index=True)
    #: What started it, e.g. ``Tag Pump running changed to ON`` or ``Run manually by admin``.
    trigger = db.Column(db.String(300), nullable=False, default="")
    status = db.Column(db.String(12), nullable=False, default="running")
    error = db.Column(db.String(500), nullable=False, default="")
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    #: JSON list of steps ``{"path", "kind", "text", "result", "at", "error"}``.
    trace_json = db.Column(db.Text, nullable=False, default="[]")

    automation = db.relationship("Automation", back_populates="runs")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<AutomationRun 12 finished>``."""
        return f"<AutomationRun {self.id} {self.status}>"

    @property
    def trace(self) -> list:
        """The steps of the run."""
        try:
            return json.loads(self.trace_json or "[]")
        except ValueError:
            return []

    @property
    def status_label(self) -> str:
        """Readable result."""
        return RUN_STATUSES.get(self.status, self.status)

    @property
    def duration(self) -> float | None:
        """Seconds from start to end (``None`` while running)."""
        if self.finished_at is None or self.started_at is None:
            return None
        start, end = self.started_at, self.finished_at
        if (start.tzinfo is None) != (end.tzinfo is None):
            start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
        return max(0.0, (end - start).total_seconds())
