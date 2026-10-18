"""WTForms classes for the administration blueprint."""

import re

from app.i18n.forms import FlaskForm
from wtforms import (
    BooleanField, FloatField, IntegerField, PasswordField, SelectField, SelectMultipleField, StringField, SubmitField,
    TextAreaField,
)
from wtforms.widgets import CheckboxInput, ListWidget
from wtforms.validators import DataRequired, EqualTo, Length, NumberRange, Optional, Regexp, ValidationError

from app.extensions import db
from app.i18n.catalog import available_languages, default_language, gettext
from app.models import PLC, Dashboard, Role, User
from app.models.user import screen_visible
from app.plc.modbus import ORDER_PRESETS
from app.models.alarm import SEVERITIES
from app.plc.protocols import DEFAULT_PORTS, MODBUS_LIBRARIES, PROTOCOLS
from app.plc.web import (DEFAULT_CONTENT_TYPE, HTTP_MODES, MAX_BODY, MAX_HEADERS_TEXT, MAX_INTERVAL, MAX_MESSAGE,
                         MIN_INTERVAL, WebSettings, is_web)
from app.plc.web_data import FORMATS

_HOST_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$"  # IPv4
    r"|^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"  # host name
)


class MultiCheckboxField(SelectMultipleField):
    """A multiple-choice field rendered as a list of checkboxes."""

    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()

    def pre_validate(self, form) -> None:
        """Accept an empty selection and ignore stale ids of deleted screens.

        Args:
            form: The parent form.
        """
        valid = {value for value, _ in self.choices}
        self.data = [v for v in (self.data or []) if v in valid]


class UserForm(FlaskForm):
    """Create or edit a user account (password optional when editing)."""

    username = StringField(
        "Username",
        validators=[
            DataRequired(),
            Length(min=3, max=64),
            Regexp(r"^[A-Za-z0-9_.-]+$", message="Letters, digits, '.', '_' and '-' only"),
        ],
    )
    email = StringField(
        "E-mail",
        validators=[Optional(), Length(max=120), Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Invalid e-mail")],
    )
    role_id = SelectField("Role", coerce=int)
    active = BooleanField("Account active", default=True)
    password = PasswordField("Password", validators=[Optional(), Length(min=8, max=128)])
    confirm = PasswordField("Repeat password", validators=[EqualTo("password", message="Passwords must match")])
    restrict_screens = BooleanField("Limit this user to the selected screens")
    allowed_screens = MultiCheckboxField("Allowed screens", coerce=int)
    home_screen_id = SelectField("Home screen (opens after login)", coerce=int, default=0,
                                 description="Must be a screen this user is allowed to open.")
    language = SelectField("Language", default="", description="The user can change it later with the language menu.")
    submit = SubmitField("Save")

    def __init__(self, *args, user: User | None = None, **kwargs):
        """Build the form and fill the role and screen choices.

        Args:
            *args: Passed to :class:`FlaskForm`.
            user: The user being edited, or ``None`` when creating.
            **kwargs: Passed to :class:`FlaskForm`.
        """
        super().__init__(*args, **kwargs)
        self._user = user
        self.role_id.choices = [(r.id, gettext(r.name)) for r in Role.query.order_by(Role.id).all()]
        screens = Dashboard.query.order_by(Dashboard.name).all()
        self._screens = {s.id: s for s in screens}
        public = gettext(" (public)")
        label = lambda s: s.name + (public if s.is_public else "")  # noqa: E731
        self.allowed_screens.choices = [(s.id, label(s)) for s in screens]
        self.home_screen_id.choices = [(0, gettext("— screen list —"))] + [(s.id, label(s)) for s in screens]
        langs = available_languages()
        self.language.choices = [("", gettext("Default ({0})", langs[default_language()].name))] + [
            (code, lang.name if lang.name == lang.english_name else f"{lang.name} ({lang.english_name})")
            for code, lang in langs.items()]
        if not self.language.data or self.language.data not in langs:
            self.language.data = ""  # field absent from the POST, no choice yet, or language removed
        if user is not None and not self.is_submitted():
            self.allowed_screens.data = [s.id for s in user.allowed_screens]
            self.home_screen_id.data = user.home_screen_id or 0
        if self.home_screen_id.data is None:  # field absent from the POST, or user without home screen
            self.home_screen_id.data = 0

    def validate_username(self, field) -> None:
        """Ensure the username is not used by another account.

        Args:
            field: The username field.

        Raises:
            ValidationError: If the name is taken.
        """
        other = User.query.filter_by(username=field.data).first()
        if other is not None and (self._user is None or other.id != self._user.id):
            raise ValidationError(gettext('Username already in use.'))

    def validate_email(self, field) -> None:
        """Ensure the e-mail is not used by another account.

        Args:
            field: The e-mail field.

        Raises:
            ValidationError: If the address is taken.
        """
        if not field.data:
            return
        other = User.query.filter_by(email=field.data).first()
        if other is not None and (self._user is None or other.id != self._user.id):
            raise ValidationError(gettext('E-mail already in use.'))

    def validate(self, extra_validators=None) -> bool:
        """Validate fields, then require a password when creating a user.

        (A field-level validator cannot do this because ``Optional()`` stops
        the validation chain for empty input.)

        Args:
            extra_validators: Passed to :meth:`FlaskForm.validate`.

        Returns:
            ``True`` if the form is valid.
        """
        ok = super().validate(extra_validators=extra_validators)
        if self._user is None and not self.password.data:
            self.password.errors = list(self.password.errors) + [gettext('A password is required for new users.')]
            ok = False
        if self.restrict_screens.data and not self.allowed_screens.data:
            self.allowed_screens.errors = list(self.allowed_screens.errors) + [gettext('Select at least one screen.')]
            ok = False
        home = self._screens.get(self.home_screen_id.data)
        role = db.session.get(Role, self.role_id.data) if self.role_id.data else None
        if home is not None and not screen_visible(role, self.restrict_screens.data, self.allowed_screens.data or [], home):
            self.home_screen_id.errors = list(self.home_screen_id.errors) + [
                gettext("This user cannot open '{0}' with the selected role / screen limits.", home.name)]
            ok = False
        return ok

    def apply_screen_access(self, user: User) -> None:
        """Copy the screen-access and home-screen fields into ``user``.

        Args:
            user: The account being created or edited.
        """
        user.restrict_screens = bool(self.restrict_screens.data)
        user.allowed_screens = [self._screens[i] for i in (self.allowed_screens.data or []) if i in self._screens]
        user.home_screen_id = self.home_screen_id.data or None


class RoleForm(FlaskForm):
    """Create a new (custom) role."""

    name = StringField("Role name", validators=[DataRequired(), Length(max=64)])
    description = StringField("Description", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Add role")

    def validate_name(self, field) -> None:
        """Ensure the role name is unique.

        Args:
            field: The name field.

        Raises:
            ValidationError: If a role with that name exists.
        """
        if Role.query.filter_by(name=field.data.strip()).first():
            raise ValidationError(gettext('A role with this name already exists.'))


class PLCForm(FlaskForm):
    """Create or edit a PLC / device connection (Siemens S7, Modbus TCP/RTU or BACnet/IP).

    Fields that do not apply to the selected protocol are hidden in the page
    and ignored by the validation.
    """

    name = StringField("Name", validators=[DataRequired(), Length(max=64)])
    protocol = SelectField("Protocol", choices=list(PROTOCOLS.items()), default="s7")
    ip_address = StringField("IP address / host", validators=[Optional(), Length(max=64)])
    tcp_port = IntegerField("Port", default=102, validators=[Optional(), NumberRange(min=1, max=65535)])
    # S7
    rack = IntegerField("Rack", default=0, validators=[Optional(), NumberRange(min=0, max=7)])
    slot = IntegerField("Slot", default=1, validators=[Optional(), NumberRange(min=0, max=31)])
    # Modbus
    modbus_library = SelectField("Client library (Modbus TCP)", choices=list(MODBUS_LIBRARIES.items()), default="pymodbus")
    unit_id = IntegerField("Unit / slave id", default=1, validators=[Optional(), NumberRange(min=0, max=255)])
    data_order = SelectField(
        "32/64-bit value order",
        choices=[("ABCD", "ABCD – big endian (Modbus standard)"), ("CDAB", "CDAB – word swapped"),
                 ("BADC", "BADC – byte swapped"), ("DCBA", "DCBA – little endian")],
        default="ABCD",
        description="How the device stores REAL/DINT/LREAL values across registers (see its manual).",
    )
    timeout = FloatField("Request timeout (s)", default=3.0, validators=[Optional(), NumberRange(min=0.1, max=60)],
                         description="How long to wait for one answer before the request counts as failed.")
    # Communication monitoring (all protocols)
    offline_delay = FloatField(
        "Communication timeout (s)", default=10.0, validators=[Optional(), NumberRange(min=0, max=3600)],
        description="Report the PLC as disconnected (red status, bad values, alarm) only when communication has "
                    "failed for this long. Shorter interruptions keep the last values on screen. 0 = at once.")
    comm_alarm = BooleanField("Raise an alarm when communication is lost", default=True)
    comm_severity = SelectField("Alarm severity", choices=[(k, v[0]) for k, v in SEVERITIES.items()], default="high")
    # BACnet
    bacnet_device_id = IntegerField("Device instance", validators=[Optional(), NumberRange(min=0, max=4194302)],
                                    description="The device object instance number (Who-Is shows it).")
    bacnet_priority = SelectField("Default write priority", coerce=int, default=8,
                                  choices=[(p, f"{p}" + {1: " – manual life safety", 5: " – critical equipment control",
                                                         8: " – manual operator (default)", 16: " – lowest"}.get(p, ""))
                                           for p in range(1, 17)])
    # Web devices (HTTP / WebSocket)
    url = StringField("Address (URL)", validators=[Optional(), Length(max=500)],
                      description="HTTP: http://192.168.1.50/status.json or https://… · "
                                  "WebSocket: ws://192.168.1.50:8080/live or wss://… · "
                                  "user:password@ in the URL = Basic authentication.")
    data_format = SelectField("Data format", choices=list(FORMATS.items()), default="json")
    poll_interval = FloatField("Request interval (s)", default=1.0,
                               validators=[Optional(), NumberRange(min=MIN_INTERVAL, max=MAX_INTERVAL)],
                               description="HTTP: time between two requests. WebSocket: time between two "
                                           "messages sent to the device (only with a message below).")
    http_mode = SelectField("Request mode", choices=list(HTTP_MODES.items()), default="auto")
    verify_tls = BooleanField("Check the certificate (HTTPS / WSS)", default=True,
                              description="Switch off for devices with a self-signed certificate.")
    ws_message = TextAreaField("Message sent after connecting (optional)",
                               validators=[Optional(), Length(max=MAX_MESSAGE)],
                               description='For devices that send data only on request, e.g. {"subscribe": "all"}.')
    http_method = SelectField("HTTP method", choices=[("GET", "GET"), ("POST", "POST – send parameters in the body")],
                              default="GET")
    http_content_type = StringField("Content type", default=DEFAULT_CONTENT_TYPE,
                                    validators=[Optional(), Length(max=100)],
                                    description="application/x-www-form-urlencoded (id=3&page=1), application/json, "
                                                "application/xml, text/plain …")
    http_body = TextAreaField("Request body (POST)", validators=[Optional(), Length(max=MAX_BODY)],
                              description='Sent with every request, e.g. id=3&page=1 or {"cmd": "read", "id": 3}.')
    http_headers = TextAreaField("Extra headers (optional)", validators=[Optional(), Length(max=MAX_HEADERS_TEXT)],
                                 description="One per line: Name: value – e.g. X-API-Key: 1234 or Authorization: "
                                             "Bearer … (HTTP requests and the WebSocket handshake).")
    serial_port = StringField("Serial port", validators=[Optional(), Length(max=64)],
                              description="Windows: COM3 · Linux: /dev/ttyUSB0")
    baudrate = SelectField("Baud rate", coerce=int, default=9600,
                           choices=[(b, str(b)) for b in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)])
    parity = SelectField("Parity", choices=[("N", "None"), ("E", "Even"), ("O", "Odd")], default="N")
    bytesize = SelectField("Data bits", coerce=int, choices=[(8, "8"), (7, "7")], default=8)
    stopbits = SelectField("Stop bits", coerce=int, choices=[(1, "1"), (2, "2")], default=1)
    description = StringField("Description", validators=[Optional(), Length(max=255)])
    enabled = BooleanField("Enabled (poll this PLC)", default=True)
    submit = SubmitField("Save")

    def __init__(self, *args, plc: PLC | None = None, **kwargs):
        """Build the form; pre-select the byte/word order preset when editing.

        Args:
            *args: Passed to :class:`FlaskForm`.
            plc: The PLC being edited, or ``None`` when creating.
            **kwargs: Passed to :class:`FlaskForm`.
        """
        super().__init__(*args, **kwargs)
        self._plc = plc
        if plc is not None and not self.is_submitted():
            current = (plc.byte_order or "big", plc.word_order or "big")
            self.data_order.data = next((k for k, v in ORDER_PRESETS.items() if v == current), "ABCD")
            alarm = plc.comm_alarm
            self.comm_alarm.data = bool(alarm is not None and alarm.enabled)
            self.comm_severity.data = alarm.severity if alarm is not None else "high"

    def validate_name(self, field) -> None:
        """Ensure the PLC name is unique.

        Args:
            field: The name field.

        Raises:
            ValidationError: If another PLC already uses that name.
        """
        other = PLC.query.filter_by(name=field.data.strip()).first()
        if other is not None and (self._plc is None or other.id != self._plc.id):
            raise ValidationError(gettext('A PLC with this name already exists.'))

    def validate(self, extra_validators=None) -> bool:
        """Validate the fields required by the selected protocol.

        * S7 / Modbus TCP: IP address or host name and TCP port.
        * S7: rack and slot.
        * Modbus: unit id.  Modbus RTU: serial port.
        * BACnet/IP: IP address, UDP port and device instance.
        * Dummy: nothing (no device).

        Args:
            extra_validators: Passed to :meth:`FlaskForm.validate`.

        Returns:
            ``True`` if valid.
        """
        ok = super().validate(extra_validators=extra_validators)
        proto = self.protocol.data

        def need(field, message):
            """Attach ``message`` to ``field`` when it is empty."""
            nonlocal ok
            if field.data in (None, ""):
                field.errors = list(field.errors) + [message]
                ok = False

        if proto in ("s7", "modbus_tcp", "bacnet_ip"):
            need(self.ip_address, "Enter the IP address or host name.")
            if self.ip_address.data and not _HOST_RE.match(self.ip_address.data.strip()):
                self.ip_address.errors = list(self.ip_address.errors) + [gettext('Enter a valid IPv4 address or host name.')]
                ok = False
            need(self.tcp_port, "Enter the TCP port.")
        if proto == "s7":
            need(self.rack, "Enter the rack.")
            need(self.slot, "Enter the slot.")
        if proto.startswith("modbus"):
            need(self.unit_id, "Enter the unit / slave id (usually 1).")
        if proto == "modbus_rtu":
            need(self.serial_port, "Enter the serial port, e.g. COM3.")
        if proto == "bacnet_ip":
            need(self.bacnet_device_id, "Enter the BACnet device instance (use Who-Is to find it).")
        if is_web(proto):
            _settings, errors = WebSettings.from_dict(self._web_data())
            for name, message in errors.items():
                field = getattr(self, name, None) or self.url
                field.errors = list(field.errors) + [message]
                ok = False
        return ok

    def _web_data(self) -> dict:
        """The web device fields as a dict for :meth:`WebSettings.from_dict`."""
        return {"protocol": self.protocol.data, "url": self.url.data, "data_format": self.data_format.data,
                "poll_interval": self.poll_interval.data if self.poll_interval.data is not None else 1.0,
                "timeout": self.timeout.data or 3.0, "http_mode": self.http_mode.data,
                "verify_tls": bool(self.verify_tls.data), "ws_message": self.ws_message.data or "",
                "http_method": self.http_method.data, "http_body": self.http_body.data or "",
                "http_content_type": self.http_content_type.data or "", "http_headers": self.http_headers.data or ""}

    def populate_plc(self, plc: PLC) -> None:
        """Copy validated data into a PLC model.

        Args:
            plc: Target model.
        """
        plc.name = self.name.data.strip()
        plc.protocol = self.protocol.data
        plc.ip_address = (self.ip_address.data or "").strip()
        plc.tcp_port = self.tcp_port.data or DEFAULT_PORTS.get(plc.protocol) or 502
        plc.rack = self.rack.data if self.rack.data is not None else 0
        plc.slot = self.slot.data if self.slot.data is not None else 1
        plc.modbus_library = self.modbus_library.data
        plc.unit_id = self.unit_id.data if self.unit_id.data is not None else 1
        plc.byte_order, plc.word_order = ORDER_PRESETS[self.data_order.data]
        plc.timeout = self.timeout.data or 3.0
        plc.offline_delay = self.offline_delay.data if self.offline_delay.data is not None else 10.0
        plc.serial_port = (self.serial_port.data or "").strip()
        plc.baudrate, plc.parity = self.baudrate.data, self.parity.data
        plc.bytesize, plc.stopbits = self.bytesize.data, self.stopbits.data
        plc.bacnet_device_id = self.bacnet_device_id.data or 0
        plc.bacnet_priority = self.bacnet_priority.data or 8
        plc.description = self.description.data or ""
        plc.enabled = self.enabled.data
        if is_web(plc.protocol):
            WebSettings.from_dict(self._web_data())[0].apply(plc)


class SettingsForm(FlaskForm):
    """System-wide settings (administrators only)."""

    public_home_screen_id = SelectField(
        "Public home screen",
        coerce=int,
        description="Shown to visitors who are not logged in and after logout. Only public screens can be chosen; "
                    "visitors can then move between public screens only. Choose “none” to require login for everything.",
    )
    history_retention_days = IntegerField(
        "Keep tag history (days)", default=90, validators=[Optional(), NumberRange(min=0, max=36500)],
        description="Samples older than this are deleted automatically (checked every hour). 0 = keep forever.",
    )
    submit = SubmitField("Save settings")

    def __init__(self, *args, **kwargs):
        """Build the form and list the public screens.

        Args:
            *args: Passed to :class:`FlaskForm`.
            **kwargs: Passed to :class:`FlaskForm`.
        """
        super().__init__(*args, **kwargs)
        public = Dashboard.query.filter_by(is_public=True).order_by(Dashboard.name).all()
        self.public_home_screen_id.choices = [(0, gettext("— none (login required) —"))] + [(s.id, s.name) for s in public]
