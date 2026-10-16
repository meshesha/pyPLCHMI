"""Tag history (samples) and saved trend charts.

* :class:`TagSample` – one saved value of a tag with its full date and time
  (UTC in the database).  Samples are written by the history logger
  (:mod:`app.history.logger`) every ``DataPoint.log_interval`` seconds for
  tags with ``DataPoint.log_enabled``.  Numbers and BOOL values are kept in
  :attr:`TagSample.value_num` (BOOL as 0 / 1), STRING values in
  :attr:`TagSample.value_text`.
* :class:`TrendChart` – a named chart: a list of series (tag, colour, axis)
  and a default time range.
"""

from __future__ import annotations

import json
import re

from app.extensions import db
from app.models.user import utcnow

#: Time ranges offered by the chart viewer: key -> (seconds, label).
RANGES = {
    "15m": (15 * 60, "15 minutes"),
    "1h": (3600, "1 hour"),
    "8h": (8 * 3600, "8 hours"),
    "24h": (24 * 3600, "24 hours"),
    "7d": (7 * 86400, "7 days"),
    "30d": (30 * 86400, "30 days"),
}

#: Y axes a series can be drawn on.
AXES = {"left": "Left axis", "right": "Right axis"}

#: Colours given to new series, in order.
PALETTE = ["#0d6efd", "#dc3545", "#20c997", "#ffc107", "#6f42c1", "#fd7e14",
           "#0dcaf0", "#d63384", "#198754", "#adb5bd", "#6610f2", "#e9ecef"]

#: Most series in one chart.
MAX_SERIES = 12

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def valid_color(text) -> bool:
    """Check a ``#rrggbb`` colour.

    Args:
        text: Colour text.

    Returns:
        ``True`` if the text is a six-digit hex colour.
    """
    return isinstance(text, str) and bool(_COLOR_RE.match(text))


class TagSample(db.Model):
    """One history sample of a tag."""

    __tablename__ = "tag_samples"
    __table_args__ = (db.Index("ix_tag_samples_tag_ts", "datapoint_id", "ts"),)

    id = db.Column(db.Integer, primary_key=True)
    datapoint_id = db.Column(db.Integer, db.ForeignKey("datapoints.id", ondelete="CASCADE"), nullable=False)
    #: Date and time of the sample (UTC).
    ts = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    #: Engineering value of numeric and BOOL tags (BOOL as 0 / 1).
    value_num = db.Column(db.Float, nullable=True)
    #: Value of STRING tags.
    value_text = db.Column(db.String(255), nullable=True)

    datapoint = db.relationship("DataPoint", lazy="joined",
                                backref=db.backref("samples", lazy="dynamic", passive_deletes=True))

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<TagSample 3 2026-10-16 08:00:00 21.5>``."""
        return f"<TagSample {self.datapoint_id} {self.ts:%Y-%m-%d %H:%M:%S} {self.value}>"

    @property
    def value(self):
        """The stored value: text for STRING tags, otherwise the number."""
        return self.value_text if self.value_text is not None else self.value_num

    @staticmethod
    def from_value(datapoint_id: int, ts, value) -> "TagSample":
        """Build a sample from a tag's engineering value.

        Args:
            datapoint_id: Tag id.
            ts: Sample time (aware UTC datetime).
            value: Number, bool or text.

        Returns:
            A new, unsaved :class:`TagSample`.
        """
        sample = TagSample(datapoint_id=datapoint_id, ts=ts)
        if isinstance(value, bool):
            sample.value_num = 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            sample.value_num = float(value)
        else:
            sample.value_text = "" if value is None else str(value)[:255]
        return sample

    def display(self, datapoint=None) -> str:
        """Format the value like the tag shows it (decimals, TRUE/FALSE texts).

        Args:
            datapoint: The tag (defaults to :attr:`datapoint`).

        Returns:
            The value as text (without the unit).
        """
        dp = datapoint or self.datapoint
        if self.value_text is not None:
            return self.value_text
        if self.value_num is None:
            return ""
        if dp is not None and dp.is_bool:
            return dp.true_text if self.value_num else dp.false_text
        decimals = dp.decimals if dp is not None else 2
        return f"{self.value_num:.{decimals}f}"


class TrendChart(db.Model):
    """A saved chart of one or more logged tags."""

    __tablename__ = "trend_charts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=False, default="", server_default="")
    #: JSON list of ``{"tag_id", "color", "axis"}``.
    series_json = db.Column(db.Text, nullable=False, default="[]", server_default="[]")
    #: Default time range (a :data:`RANGES` key).
    range_key = db.Column(db.String(10), nullable=False, default="1h", server_default="1h")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<TrendChart Boiler>``."""
        return f"<TrendChart {self.name}>"

    @property
    def series(self) -> list[dict]:
        """The chart's series (invalid JSON gives an empty list)."""
        try:
            data = json.loads(self.series_json or "[]")
        except ValueError:
            return []
        return [s for s in data if isinstance(s, dict) and isinstance(s.get("tag_id"), int)]

    @series.setter
    def series(self, items: list[dict]) -> None:
        """Store the series list.

        Args:
            items: ``{"tag_id", "color", "axis"}`` dicts.
        """
        self.series_json = json.dumps([{"tag_id": int(s["tag_id"]), "color": s["color"],
                                        "axis": s.get("axis", "left")} for s in items])

    def resolved_series(self) -> list[dict]:
        """Series joined with their tags; series whose tag was deleted are skipped.

        Returns:
            Dicts ``{"tag_id", "color", "axis", "datapoint"}``.
        """
        from app.models.dashboard import DataPoint  # noqa: PLC0415

        items = self.series
        ids = [s["tag_id"] for s in items]
        tags = {dp.id: dp for dp in DataPoint.query.filter(DataPoint.id.in_(ids)).all()} if ids else {}
        return [dict(s, datapoint=tags[s["tag_id"]]) for s in items if s["tag_id"] in tags]

    def uses_tag(self, tag_id: int) -> bool:
        """Check whether the chart shows a tag.

        Args:
            tag_id: Tag id.

        Returns:
            ``True`` if one of the series is that tag.
        """
        return any(s["tag_id"] == tag_id for s in self.series)


def _forget_tag(mapper, connection, target) -> None:
    """Remove a deleted tag's samples and chart series (``before_delete`` of DataPoint).

    SQLite does not enforce ``ON DELETE CASCADE`` unless foreign keys are
    switched on, and it can give a new tag the id of a deleted one – so the
    samples are deleted explicitly and the tag is taken out of every chart.

    Args:
        mapper: SQLAlchemy mapper (unused).
        connection: The connection of the flush.
        target: The :class:`~app.models.dashboard.DataPoint` being deleted.
    """
    if target.id is None:
        return
    connection.execute(db.delete(TagSample.__table__).where(TagSample.__table__.c.datapoint_id == target.id))
    charts = TrendChart.__table__
    for chart_id, raw in connection.execute(db.select(charts.c.id, charts.c.series_json)).fetchall():
        try:
            items = json.loads(raw or "[]")
        except ValueError:
            continue
        kept = [s for s in items if not (isinstance(s, dict) and s.get("tag_id") == target.id)]
        if len(kept) != len(items):
            connection.execute(db.update(charts).where(charts.c.id == chart_id).values(series_json=json.dumps(kept)))


def _register_events() -> None:
    """Attach :func:`_forget_tag` to DataPoint deletions."""
    from sqlalchemy import event  # noqa: PLC0415

    from app.models.dashboard import DataPoint  # noqa: PLC0415

    if not event.contains(DataPoint, "before_delete", _forget_tag):
        event.listen(DataPoint, "before_delete", _forget_tag)


_register_events()
