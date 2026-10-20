"""Faceplates: one template screen shown for many identical devices.

* :class:`Faceplate` – a *device type* (e.g. "Electricity meter"): a Plane
  screen drawn with the tags of one **reference device**, and that device's
  tag-name prefix (``Panel_01.MeterA.``).  Every tag of the screen whose name
  starts with the prefix is a **parameter**; the rest of the name
  (``voltage_l1``) identifies it.
* :class:`FaceplateInstance` – one device (e.g. "Panel 37 – meter B") with its
  own prefix (``Panel_37.MeterB.``).  When the faceplate is opened for it,
  every parameter tag is replaced by the tag with the same name after the
  instance prefix (``Panel_37.MeterB.voltage_l1``).  Single parameters can be
  bound to other tags (:attr:`FaceplateInstance.overrides`).

The binding logic lives in :mod:`app.faceplates.binding`.
"""

from __future__ import annotations

import json

from app.extensions import db
from app.models.user import utcnow


class Faceplate(db.Model):
    """A device type: template screen + reference tag prefix."""

    __tablename__ = "faceplates"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(500), nullable=False, default="", server_default="")
    #: The template: a Plane screen drawn with the reference device's tags (``None`` after it was deleted).
    screen_id = db.Column(db.Integer, db.ForeignKey("dashboards.id", ondelete="SET NULL"), nullable=True)
    #: Tag-name prefix of the reference device, e.g. ``Panel_01.MeterA.``.
    prefix = db.Column(db.String(100), nullable=False, default="", server_default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    screen = db.relationship("Dashboard", lazy="joined")
    instances = db.relationship("FaceplateInstance", back_populates="faceplate", cascade="all, delete-orphan",
                                order_by="FaceplateInstance.name", lazy="select")

    def __repr__(self) -> str:
        """Debug text, e.g. ``<Faceplate Electricity meter Panel_01.MeterA.>``."""
        return f"<Faceplate {self.name} {self.prefix}>"


class FaceplateInstance(db.Model):
    """One device shown with a faceplate."""

    __tablename__ = "faceplate_instances"
    __table_args__ = (db.UniqueConstraint("faceplate_id", "name", name="uq_faceplate_instances_name"),)

    id = db.Column(db.Integer, primary_key=True)
    faceplate_id = db.Column(db.Integer, db.ForeignKey("faceplates.id", ondelete="CASCADE"), nullable=False,
                             index=True)
    #: Name shown as the window title, e.g. ``Panel 37 – meter B``.
    name = db.Column(db.String(100), nullable=False)
    #: Tag-name prefix of this device, e.g. ``Panel_37.MeterB.``.
    prefix = db.Column(db.String(100), nullable=False, default="", server_default="")
    description = db.Column(db.String(500), nullable=False, default="", server_default="")
    #: Parameters bound to other tags than the prefix gives: JSON ``{"voltage_l1": <tag id>}``.
    overrides_json = db.Column(db.Text, nullable=False, default="{}", server_default="{}")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    faceplate = db.relationship("Faceplate", back_populates="instances", lazy="joined")

    def __repr__(self) -> str:
        """Debug text, e.g. ``<FaceplateInstance Panel 37 – meter B>``."""
        return f"<FaceplateInstance {self.name}>"

    @property
    def overrides(self) -> dict[str, int]:
        """Parameter name (lower case) → tag id of the parameters bound by hand."""
        try:
            data = json.loads(self.overrides_json or "{}")
        except ValueError:
            return {}
        out = {}
        for key, value in (data.items() if isinstance(data, dict) else []):
            try:
                out[str(key).lower()] = int(value)
            except (TypeError, ValueError):
                continue
        return out

    @overrides.setter
    def overrides(self, value: dict) -> None:
        """Store the hand-made bindings (parameter → tag id; empty values are dropped)."""
        clean = {str(k).lower(): int(v) for k, v in (value or {}).items() if v not in (None, "", 0)}
        self.overrides_json = json.dumps(clean, sort_keys=True)

    @property
    def title(self) -> str:
        """Window title: ``Electricity meter – Panel 37 – meter B``."""
        return f"{self.faceplate.name} – {self.name}" if self.faceplate else self.name
