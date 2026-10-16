"""Audit log: who changed what, when, with the values before and after.

Rows are written by :mod:`app.audit.recorder` – automatically for every
change of an audited model made during a web request (create / update /
delete), and explicitly for actions that are not database changes (log in,
failed log in, log out, writing a value to a PLC).
"""

from __future__ import annotations

import json

from app.extensions import db
from app.models.user import utcnow

#: Actions shown in the filter: code -> label.
ACTIONS = {
    "create": "Created",
    "update": "Changed",
    "delete": "Deleted",
    "write": "Value written",
    "login": "Logged in",
    "login_failed": "Login failed",
    "logout": "Logged out",
}


class AuditLog(db.Model):
    """One audited user action."""

    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    #: User name at the time of the action (kept when the user is deleted).
    username = db.Column(db.String(64), nullable=False, default="", server_default="")
    #: IP address of the browser.
    ip = db.Column(db.String(45), nullable=False, default="", server_default="")
    #: An :data:`ACTIONS` code.
    action = db.Column(db.String(20), nullable=False, index=True)
    #: Kind of object (``User``, ``PLC``, ``Tag`` …).
    object_type = db.Column(db.String(40), nullable=False, default="", server_default="", index=True)
    object_id = db.Column(db.Integer, nullable=True)
    #: Name of the object at the time of the action.
    object_label = db.Column(db.String(200), nullable=False, default="", server_default="")
    #: JSON list of ``{"field", "before", "after"}``.
    changes_json = db.Column(db.Text, nullable=False, default="[]", server_default="[]")

    user = db.relationship("User")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<AuditLog update PLC 3>``."""
        return f"<AuditLog {self.action} {self.object_type} {self.object_id}>"

    @property
    def changes(self) -> list[dict]:
        """The changed fields (empty list for invalid JSON)."""
        try:
            data = json.loads(self.changes_json or "[]")
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    @property
    def action_label(self) -> str:
        """English label of the action (translated in the templates)."""
        return ACTIONS.get(self.action, self.action)
