"""WTForms classes of the tag history: trend chart definition."""

from __future__ import annotations

import json

from wtforms import HiddenField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.extensions import db
from app.history.data import chart_tags
from app.i18n.catalog import gettext
from app.i18n.forms import FlaskForm
from app.models import DataPoint, TrendChart
from app.models.history import AXES, MAX_SERIES, RANGES, valid_color


class ChartForm(FlaskForm):
    """Create or edit a trend chart.

    The series (tag, colour, axis) are edited in the browser and posted as
    JSON in :attr:`series`; only tags with history logging enabled (numbers
    and BOOL) can be used.
    """

    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    description = StringField("Description", validators=[Optional(), Length(max=255)])
    range_key = SelectField("Default time range", choices=[(k, v[1]) for k, v in RANGES.items()], default="1h")
    series = HiddenField("Series")
    submit = SubmitField("Save")

    def __init__(self, *args, chart: TrendChart | None = None, **kwargs):
        """Build the form.

        Args:
            *args: Passed to :class:`FlaskForm`.
            chart: The chart being edited (for the unique-name check).
            **kwargs: Passed to :class:`FlaskForm`.
        """
        super().__init__(*args, **kwargs)
        self.chart = chart
        self.parsed_series: list[dict] = []

    def validate_name(self, field) -> None:
        """Chart names must be unique.

        Args:
            field: The name field.

        Raises:
            ValidationError: Another chart has this name.
        """
        other = TrendChart.query.filter(TrendChart.name == (field.data or "").strip()).first()
        if other is not None and (self.chart is None or other.id != self.chart.id):
            raise ValidationError(gettext("A chart with this name already exists."))

    def validate_series(self, field) -> None:
        """Parse and check the series JSON.

        Args:
            field: The hidden series field.

        Raises:
            ValidationError: No series, too many, unknown or unlogged tags, bad colours.
        """
        try:
            items = json.loads(field.data or "[]")
        except ValueError as exc:
            raise ValidationError(gettext("Invalid series data.")) from exc
        if not isinstance(items, list) or not items:
            raise ValidationError(gettext("Add at least one tag."))
        if len(items) > MAX_SERIES:
            raise ValidationError(gettext("At most {0} tags per chart.", MAX_SERIES))
        allowed = {dp.id for dp in chart_tags()}
        seen, parsed = set(), []
        for item in items:
            if not isinstance(item, dict):
                raise ValidationError(gettext("Invalid series data."))
            try:
                tag_id = int(item.get("tag_id"))
            except (TypeError, ValueError) as exc:
                raise ValidationError(gettext("Choose a tag for every row.")) from exc
            dp = db.session.get(DataPoint, tag_id)
            if dp is None:
                raise ValidationError(gettext("Choose a tag for every row."))
            if tag_id not in allowed:
                raise ValidationError(gettext("Tag '{0}' has no history logging – enable it in the tag settings first.",
                                              dp.label))
            if tag_id in seen:
                raise ValidationError(gettext("Tag '{0}' is in the chart twice.", dp.label))
            color = item.get("color")
            if not valid_color(color):
                raise ValidationError(gettext("Invalid colour for tag '{0}'.", dp.label))
            axis = item.get("axis") if item.get("axis") in AXES else "left"
            seen.add(tag_id)
            parsed.append({"tag_id": tag_id, "color": color.lower(), "axis": axis})
        self.parsed_series = parsed

    def populate_chart(self, chart: TrendChart) -> None:
        """Copy the validated form into a chart.

        Args:
            chart: Target chart.
        """
        chart.name = self.name.data.strip()
        chart.description = (self.description.data or "").strip()
        chart.range_key = self.range_key.data if self.range_key.data in RANGES else "1h"
        chart.series = self.parsed_series

    def load_chart(self, chart: TrendChart) -> None:
        """Pre-fill the form from a chart.

        Args:
            chart: The chart.
        """
        self.name.data = chart.name
        self.description.data = chart.description
        self.range_key.data = chart.range_key
        self.series.data = json.dumps(chart.series)
