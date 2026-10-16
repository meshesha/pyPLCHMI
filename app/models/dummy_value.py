"""Stored values of *general* dummy tags."""

from app.extensions import db
from app.models.user import utcnow


class DummyValue(db.Model):
    """The current value of one slot of a Dummy PLC.

    General dummy tags keep their value here, so every browser and every
    computer sees the same value and it survives a server restart.  Rows are
    keyed by PLC and slot number (``D1``, ``D2``…); the value is stored as JSON
    together with the data type it was written as.  When the tag's data type
    is changed, the stored value is converted to the new type if it fits
    (otherwise the tag starts again at its default).
    Local dummy tags never use this table – their value lives in the browser.
    """

    __tablename__ = "dummy_values"
    __table_args__ = (db.UniqueConstraint("plc_id", "slot", name="uq_dummy_values_plc_slot"),)

    id = db.Column(db.Integer, primary_key=True)
    plc_id = db.Column(db.Integer, db.ForeignKey("plcs.id", ondelete="CASCADE"), nullable=False, index=True)
    slot = db.Column(db.Integer, nullable=False)
    data_type = db.Column(db.String(10), nullable=False)
    #: JSON encoded value (``true``, ``12``, ``3.5``, ``"text"``).
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<DummyValue 2:D3=12>``."""
        return f"<DummyValue {self.plc_id}:D{self.slot}={self.value}>"
