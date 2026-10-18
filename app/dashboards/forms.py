"""WTForms classes for dashboards and data points."""

from app.i18n.forms import FlaskForm
from wtforms import (
    BooleanField, FloatField, IntegerField, RadioField, SelectField, StringField, SubmitField, TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from app.extensions import db
from app.admin.forms import MultiCheckboxField
from app.models import PLC, Dashboard, DataPoint, Role
from app.models.dashboard import WRITE_ACCESS
from app.plc.addressing import AddressError
from app.plc.dummy import DummyAddress, next_slot
from app.plc.protocols import parse_tag_address
from app.plc.datatypes import DATA_TYPE_CHOICES, DATA_TYPES
from app.i18n.catalog import gettext


def enabled_plcs() -> list:
    """Return the PLCs that may be used for new data points.

    Returns:
        Enabled :class:`~app.models.plc.PLC` rows ordered by name.
    """
    return PLC.query.filter_by(enabled=True).order_by(PLC.name).all()


def plc_choices(current_plc_id: int | None = None) -> list[tuple[int, str]]:
    """Build ``(id, label)`` choices of enabled PLCs for a select field.

    Args:
        current_plc_id: PLC already used by the edited data point; it is
            included (marked "(disabled)") even when disabled.

    Returns:
        List of ``(plc_id, "Name (ip)")`` tuples.
    """
    def endpoint(plc) -> str:
        """Device address for the label (translated for dummy PLCs)."""
        return gettext(plc.endpoint) if plc.is_dummy else plc.endpoint

    choices = [(p.id, f"{p.name} ({endpoint(p)})") for p in enabled_plcs()]
    if current_plc_id and all(pid != current_plc_id for pid, _ in choices):
        current = db.session.get(PLC, current_plc_id)
        if current is not None:
            choices.append((current.id, f"{current.name} ({endpoint(current)}) " + gettext("(disabled)")))
    return choices


class DashboardForm(FlaskForm):
    """Create or edit a dashboard (HMI screen)."""

    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    kind = SelectField("Type", choices=list(Dashboard.KINDS.items()), default="grid",
                       description="Grid = automatic tiles. Plane = free drawing with symbols, images and buttons.")
    description = TextAreaField("Description", validators=[Optional(), Length(max=500)])
    is_public = BooleanField("Public (visible to Guest role)")
    refresh_ms = IntegerField(
        "Refresh interval (ms)", default=1000, validators=[DataRequired(), NumberRange(min=250, max=60000)]
    )
    submit = SubmitField("Save")


class DataPointForm(FlaskForm):
    """Configure one data point: PLC, address, type and presentation.

    The address can be entered in Siemens notation (``DB1.DBD4``, ``M10.5``,
    ``IW64``...).  A ``:TYPE`` suffix such as ``DB1.DBD4:REAL`` overrides the
    *Data type* drop-down.  After validation the parsed
    :class:`~app.plc.addressing.S7Address` is available as
    ``form.parsed_address``.
    """

    label = StringField("Tag name", validators=[DataRequired(), Length(max=100)],
                        description="Unique; letters, digits, _ and . only – no spaces or symbols, not starting "
                                    "with a digit (e.g. Tank_1.level).")
    plc_id = SelectField("PLC", coerce=int, validators=[DataRequired()])
    address = StringField(
        "Address",
        validators=[Optional(), Length(max=40)],
        description="S7: DB1.DBD4, DB1.DBX0.0, M10.5, MW20, IW64, Q0.1, T5 · "
                    "Modbus: HR100, HR100:REAL, HR10.3, IR5, C12, DI3, 40001 · BACnet: AV3, BO12, MV2, AI1.units · "
                    "Dummy PLC: no address",
    )
    data_type = SelectField("Data type", choices=DATA_TYPE_CHOICES, default="INT")
    dummy_scope = SelectField(
        "Dummy tag kind", choices=list(DataPoint.DUMMY_SCOPES.items()), default="general",
        description="General: one value for every browser and computer, stored in the database. "
                    "Local: each browser window keeps its own value (e.g. to show/hide details on one "
                    "operator panel only); local tags cannot be used by alarms.")
    string_length = IntegerField("STRING length", default=20, validators=[Optional(), NumberRange(min=1, max=254)])
    widget = SelectField("Widget", choices=list(DataPoint.WIDGETS.items()), default="value")
    unit = StringField("Unit", validators=[Optional(), Length(max=20)])
    scale = FloatField("Scale factor", default=1.0, validators=[Optional()])
    offset = FloatField("Offset", default=0.0, validators=[Optional()])
    decimals = IntegerField("Decimals", default=2, validators=[Optional(), NumberRange(min=0, max=6)])
    min_value = FloatField("Min (gauge)", validators=[Optional()])
    max_value = FloatField("Max (gauge)", validators=[Optional()])
    true_text = StringField("Text when TRUE", default="ON", validators=[Optional(), Length(max=30)])
    false_text = StringField("Text when FALSE", default="OFF", validators=[Optional(), Length(max=30)])
    writable = BooleanField("Operators may write this value")
    write_access = RadioField("Who may write this value", choices=list(WRITE_ACCESS.items()), default="all",
                              description="Everyone = every user whose role has the 'write values' permission.")
    write_roles = MultiCheckboxField("Roles allowed to write", coerce=int,
                                     description="Users of other roles see the value but cannot change it.")
    width = SelectField("Tile width", coerce=int, choices=[(1, "1 column"), (2, "2 columns"), (3, "3 columns"), (4, "Full row")], default=1)
    #: Order on the screen being edited (stored on the screen–tag link, not on the tag).
    position = IntegerField("Order", default=0, validators=[Optional()])
    submit = SubmitField("Save")

    def __init__(self, *args, current_plc_id: int | None = None, datapoint: DataPoint | None = None, **kwargs):
        """Build the form and fill the PLC choices with *enabled* PLCs only.

        Disabled PLCs are not offered.  The one exception is the PLC that an
        existing data point already uses (``current_plc_id``): it stays in the
        list, marked "(disabled)", so that editing such a point does not
        silently move it to another PLC.  Because WTForms only accepts listed
        choices, a disabled PLC id posted by hand is rejected.

        Args:
            *args: Passed to :class:`FlaskForm`.
            current_plc_id: PLC of the data point being edited, if any.
            **kwargs: Passed to :class:`FlaskForm`.
        """
        super().__init__(*args, **kwargs)
        self.datapoint = datapoint
        self.plc_id.choices = plc_choices(current_plc_id)
        self.write_roles.choices = [(r.id, r.name) for r in Role.query.order_by(Role.name)]
        if self.write_roles.data is None:
            self.write_roles.data = []
        if self.write_access.data not in WRITE_ACCESS:
            self.write_access.data = "all"
        self.parsed_address = None

    def validate_label(self, field) -> None:
        """Check the tag name (see :mod:`app.tags.naming`): unique, no spaces or symbols, not starting with a digit.

        Args:
            field: The label field.

        Raises:
            ValidationError: With instructions for every problem.
        """
        from app.tags.naming import check  # noqa: PLC0415

        field.data = (field.data or "").strip()
        issues = check(field.data, self.datapoint)
        if issues:
            raise ValidationError(" ".join(issues))

    def validate_address(self, field) -> None:
        """Parse the address with the syntax of the selected PLC's protocol.

        Siemens S7 PLCs use S7 notation (``DB1.DBD4``), Modbus devices Modbus
        notation (``HR100``, ``40001``, ``C5``...).  Dummy PLCs need no
        address (any text is ignored; :meth:`validate` builds the slot).

        Args:
            field: The address field.

        Raises:
            ValidationError: If the address is invalid or inconsistent.
        """
        plc = db.session.get(PLC, self.plc_id.data) if self.plc_id.data else None
        protocol = (plc.protocol if plc is not None else None) or "s7"
        if protocol == "dummy":
            return  # dummy tags ignore the address; see validate()
        try:
            self.parsed_address = parse_tag_address(field.data, self.data_type.data, self.string_length.data, protocol)
        except AddressError as exc:
            raise ValidationError(str(exc)) from exc
        # A ':TYPE' suffix in the address wins over the drop-down.
        self.data_type.data = self.parsed_address.data_type
        if self.parsed_address.data_type == "STRING":
            self.string_length.data = self.parsed_address.string_length

    def validate(self, extra_validators=None) -> bool:
        """Run field validation, then cross-field checks.

        Checks that BOOL-only widgets get BOOL tags, gauges get numeric tags
        with a sensible range, and read-only types are not marked writable.

        Args:
            extra_validators: Passed to :meth:`FlaskForm.validate`.

        Returns:
            ``True`` if the whole form is valid.
        """
        if not super().validate(extra_validators=extra_validators):
            return False
        if self.parsed_address is None:  # no parsed address: fine only for dummy PLCs
            plc = db.session.get(PLC, self.plc_id.data) if self.plc_id.data else None
            if plc is None or not plc.is_dummy:
                self.address.errors = list(self.address.errors) + [gettext('Enter the address.')]
                return False
            try:
                self.parsed_address = parse_tag_address("", self.data_type.data, self.string_length.data, "dummy")
            except AddressError as exc:
                self.data_type.errors = list(self.data_type.errors) + [str(exc)]
                return False
        addr = self.parsed_address
        ok = True
        if self.widget.data in ("indicator", "switch") and addr.data_type != "BOOL":
            self.widget.errors.append(gettext('Indicator and switch widgets need a BOOL data point.'))
            ok = False
        if self.widget.data == "switch" and not self.writable.data:
            self.writable.errors.append(gettext('A switch must be writable.'))
            ok = False
        if self.widget.data == "gauge":
            if not DATA_TYPES[addr.data_type].numeric:
                self.widget.errors.append(gettext('Gauges need a numeric data type.'))
                ok = False
            elif self.min_value.data is None or self.max_value.data is None or self.min_value.data >= self.max_value.data:
                self.max_value.errors.append(gettext('Gauges need Min < Max.'))
                ok = False
        if self.writable.data and (not DATA_TYPES[addr.data_type].writable or addr.is_timer_or_counter
                                   or not getattr(addr, "writable_area", True)):
            self.writable.errors.append(gettext('{0} cannot be written.', addr.format(True)))
            ok = False
        if self.writable.data and self.write_access.data == "roles" and not self.write_roles.data:
            self.write_roles.errors = list(self.write_roles.errors) + [gettext('Select at least one role.')]
            ok = False
        if self.scale.data == 0:
            self.scale.errors.append(gettext('Scale cannot be 0.'))
            ok = False
        if isinstance(addr, DummyAddress) and self.dummy_scope.data == "local" and self.datapoint is not None \
                and self.datapoint.id is not None:
            alarms = [a.name for a in self.datapoint.alarms]
            if alarms:
                self.dummy_scope.errors = list(self.dummy_scope.errors) + [
                    gettext('Used by alarm(s) {0} – a local tag has no value on the server, so it cannot raise alarms.', ', '.join(alarms))]
                ok = False
        return ok

    def populate_datapoint(self, dp: DataPoint) -> None:
        """Copy the validated form into a :class:`DataPoint`.

        Args:
            dp: The data point to update.
        """
        if isinstance(self.parsed_address, DummyAddress):
            # Keep the tag's slot on the same dummy PLC; otherwise take the next free one.
            same = dp.id is not None and dp.plc_id == self.plc_id.data and dp.area == "D" and (dp.start or 0) > 0
            with db.session.no_autoflush:
                self.parsed_address.start = dp.start if same else next_slot(self.plc_id.data)
        dp.label = self.label.data.strip()
        dp.plc_id = self.plc_id.data
        dp.set_address(self.parsed_address)
        dp.widget = self.widget.data
        dp.unit = (self.unit.data or "").strip()
        dp.scale = self.scale.data if self.scale.data is not None else 1.0
        dp.offset = self.offset.data or 0.0
        dp.decimals = self.decimals.data if self.decimals.data is not None else 2
        dp.min_value = self.min_value.data
        dp.max_value = self.max_value.data
        dp.true_text = self.true_text.data or "ON"
        dp.false_text = self.false_text.data or "OFF"
        dp.writable = bool(self.writable.data)
        dp.write_access = self.write_access.data if self.write_access.data in WRITE_ACCESS else "all"
        wanted = set(self.write_roles.data or []) if dp.write_access == "roles" else set()
        with db.session.no_autoflush:  # one flush for the whole tag = one audit entry
            dp.write_roles = Role.query.filter(Role.id.in_(wanted)).order_by(Role.name).all() if wanted else []
        dp.width = self.width.data or 1
        dp.dummy_scope = (self.dummy_scope.data or "general") if isinstance(self.parsed_address, DummyAddress) \
            else "general"

    def load_datapoint(self, dp: DataPoint) -> None:
        """Pre-fill the form from an existing data point (GET requests).

        Args:
            dp: The data point being edited.
        """
        for name in ("label", "plc_id", "data_type", "widget", "unit", "scale", "offset", "decimals",
                     "min_value", "max_value", "true_text", "false_text", "writable", "width", "dummy_scope"):
            getattr(self, name).data = getattr(dp, name)
        self.write_access.data = dp.write_access or "all"
        self.write_roles.data = [role.id for role in dp.write_roles]
        # Dummy tags have no user address (so moving them to a real PLC starts empty).
        self.address.data = "" if dp.protocol == "dummy" else dp.address
        self.string_length.data = dp.string_length or 20
