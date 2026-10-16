"""WTForms classes for alarm definitions."""

from app.i18n.forms import FlaskForm
from wtforms import BooleanField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.extensions import db
from app.models import PLC, Alarm, Dashboard, DataPoint
from app.models.alarm import KINDS, OPERATORS, SEVERITIES, parse_setpoint, value_kind
from app.i18n.catalog import gettext


class AlarmForm(FlaskForm):
    """Create or edit an alarm: name, description, trigger, severity, screen.

    The trigger is either a tag condition (``kind="tag"``: tag, operator,
    value) or the loss of communication with a PLC (``kind="comm"``: PLC; the
    delay is the PLC's communication timeout).
    """

    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=500)])
    kind = SelectField("Trigger", choices=list(KINDS.items()), default="tag")
    datapoint_id = SelectField("Tag", coerce=int, validators=[Optional()])
    operator = SelectField("Condition", choices=[(k, f"{sym}  {text}") for k, (sym, text) in OPERATORS.items()], default="gt")
    setpoint = StringField("Value", validators=[Optional(), Length(max=100)],
                           description="Number for numeric tags, true/false for BOOL tags, text for STRING tags.")
    plc_id = SelectField("PLC / device", coerce=int, validators=[Optional()],
                         description="Active when the PLC has been unreachable for longer than its communication "
                                     "timeout (Admin → PLCs → edit).")
    severity = SelectField("Severity", choices=[(k, v[0]) for k, v in SEVERITIES.items()], default="high")
    screen_id = SelectField("Screen to open", coerce=int, default=0)
    enabled = BooleanField("Enabled (evaluate this alarm)", default=True)
    submit = SubmitField("Save")

    def __init__(self, *args, alarm: Alarm | None = None, **kwargs):
        """Build the form with tag and screen choices.

        Args:
            *args: Passed to :class:`FlaskForm`.
            alarm: The alarm being edited, or ``None`` when creating.
            **kwargs: Passed to :class:`FlaskForm`.
        """
        super().__init__(*args, **kwargs)
        self._alarm = alarm
        tags = DataPoint.query.order_by(DataPoint.label).all()
        self.datapoint_id.choices = [(0, gettext("— choose a tag —"))] + [
            (t.id, f"{t.label} — {t.plc.name} {t.address} ({t.data_type})"
                   + (f" · {t.screen_names}" if t.screen_links else "")
                   + (gettext(" · local dummy tag – cannot be used") if t.is_local else "")) for t in tags]
        self.screen_id.choices = [(0, gettext("— none —"))] + [(s.id, s.name) for s in Dashboard.query.order_by(Dashboard.name)]
        if self.screen_id.data is None:
            self.screen_id.data = 0
        self.plc_id.choices = [(0, gettext("— choose a PLC —"))] + [
            (p.id, f"{p.name} ({gettext(p.endpoint) if p.is_dummy else p.endpoint}) · "
                   + gettext("timeout {0} s", f"{p.offline_delay:g}")) for p in PLC.query.order_by(PLC.name)]
        if self.plc_id.data is None:
            self.plc_id.data = 0
        if self.datapoint_id.data is None:
            self.datapoint_id.data = 0
        #: Tag id → ``bool`` / ``number`` / ``text`` (used by the form's JavaScript).
        self.tag_kinds = {t.id: value_kind(t) for t in tags}

    def validate_name(self, field) -> None:
        """Alarm names must be unique.

        Args:
            field: The name field.

        Raises:
            ValidationError: If another alarm uses the name.
        """
        other = Alarm.query.filter_by(name=field.data.strip()).first()
        if other is not None and (self._alarm is None or other.id != self._alarm.id):
            raise ValidationError(gettext('An alarm with this name already exists.'))

    def validate(self, extra_validators=None) -> bool:
        """Check the fields of the chosen trigger.

        * Tag condition: a tag and a value are required.
        * Communication: a PLC is required, and only one such alarm per PLC.

        Args:
            extra_validators: Passed to :meth:`FlaskForm.validate`.

        Returns:
            ``True`` if valid.
        """
        ok = super().validate(extra_validators=extra_validators)
        if self.kind.data == "comm":
            plc = db.session.get(PLC, self.plc_id.data) if self.plc_id.data else None
            if plc is None:
                self.plc_id.errors = list(self.plc_id.errors) + [gettext('Choose a PLC.')]
                return False
            other = plc.comm_alarm
            if other is not None and (self._alarm is None or other.id != self._alarm.id):
                self.plc_id.errors = list(self.plc_id.errors) + [
                    gettext("'{0}' already has a communication alarm ('{1}').", plc.name, other.name)]
                return False
            return ok
        if not self.datapoint_id.data:
            self.datapoint_id.errors = list(self.datapoint_id.errors) + [gettext('Choose a tag.')]
            ok = False
        else:
            tag = db.session.get(DataPoint, self.datapoint_id.data)
            if tag is not None and tag.is_local:
                self.datapoint_id.errors = list(self.datapoint_id.errors) + [
                    gettext("'{0}' is a local dummy tag: its value exists only in each browser window, so it cannot raise alarms.", tag.label)]
                ok = False
        if not (self.setpoint.data or "").strip():
            self.setpoint.errors = list(self.setpoint.errors) + [gettext('Enter the value to compare with.')]
            ok = False
        return ok

    def validate_setpoint(self, field) -> None:
        """The value must fit the tag's type (number / bool / text).

        Args:
            field: The setpoint field.

        Raises:
            ValidationError: If it cannot be converted.
        """
        if self.kind.data == "comm" or not (field.data or "").strip():
            return
        tag = db.session.get(DataPoint, self.datapoint_id.data) if self.datapoint_id.data else None
        if tag is None:
            return
        kind = value_kind(tag)
        try:
            parse_setpoint(field.data, kind)
        except ValueError as exc:
            raise ValidationError(str(exc) if kind == "bool" else "Enter a number for this numeric tag.") from exc
        if kind == "bool" and self.operator.data not in ("eq", "ne"):
            self.operator.errors = list(self.operator.errors) + [gettext('BOOL tags only support = and ≠.')]
            raise ValidationError(gettext('Use = or ≠ for BOOL tags.'))

    def populate(self, alarm: Alarm) -> None:
        """Copy validated data into an alarm.

        Args:
            alarm: Target model.
        """
        alarm.name = self.name.data.strip()
        alarm.description = (self.description.data or "").strip()
        alarm.kind = self.kind.data
        if alarm.kind == "comm":
            alarm.plc_id, alarm.datapoint_id = self.plc_id.data, None
            alarm.operator, alarm.setpoint = "eq", ""
        else:
            alarm.plc_id, alarm.datapoint_id = None, self.datapoint_id.data
            alarm.operator = self.operator.data
            alarm.setpoint = self.setpoint.data.strip()
        alarm.severity = self.severity.data
        alarm.screen_id = self.screen_id.data or None
        alarm.enabled = bool(self.enabled.data)
