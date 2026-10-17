"""Dashboard (HMI screen), data point (tag) and screen–tag link models.

Tags (:class:`DataPoint`) live in the tag table independently of screens.
A screen shows a tag through a :class:`ScreenTag` link, so one tag can be
used on several screens and removing it from a screen keeps the tag.
"""

import json
import re

from sqlalchemy import false as sa_false

from app.extensions import db
from app.models.user import Perm, utcnow
from app.plc.datatypes import DATA_TYPES, is_numeric


def slugify(text: str) -> str:
    """Turn a display name into a URL-friendly slug.

    Args:
        text: Any string, e.g. ``"Line 1 – Overview"``.

    Returns:
        Lower-case ASCII words joined by ``-``, e.g. ``"line-1-overview"``.
        Returns ``"dashboard"`` if nothing usable remains.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or "dashboard"


class Dashboard(db.Model):
    """An HMI screen grouping data points from one or more PLCs.

    Two kinds exist (``kind`` column):

    * ``grid``  – automatic tile layout, one tile per data point.
    * ``image`` – free graphic (mimic) screen drawn in the SVG designer.  The
      drawing is stored as JSON in ``layout`` (see
      :mod:`app.dashboards.layout` for the schema); its elements reference
      this dashboard's data points by id.

    The tags of a screen are its :class:`ScreenTag` links (``tag_links``);
    ``datapoints`` is the read-only list of the linked tags in screen order.
    """

    __tablename__ = "dashboards"

    KINDS = {"grid": "Grid (automatic tiles)", "image": "Plane (graphic designer)"}  # shown as screen types

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    description = db.Column(db.String(500), nullable=False, default="")
    is_public = db.Column(db.Boolean, nullable=False, default=False)
    refresh_ms = db.Column(db.Integer, nullable=False, default=1000)
    kind = db.Column(db.String(10), nullable=False, default="grid", server_default="grid")
    layout = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    #: Links to the tags shown on this screen (deleting the screen deletes the links, not the tags).
    tag_links = db.relationship("ScreenTag", back_populates="dashboard", cascade="all, delete-orphan",
                                order_by="(ScreenTag.position, ScreenTag.id)")
    #: The linked tags in screen order (read-only; change links with :meth:`add_tag` / :meth:`remove_tag`).
    datapoints = db.relationship("DataPoint", secondary="screen_tags", viewonly=True,
                                 order_by="(ScreenTag.position, ScreenTag.id)")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<Dashboard line-1>``."""
        return f"<Dashboard {self.slug}>"

    @staticmethod
    def unique_slug(name: str, exclude_id: int | None = None) -> str:
        """Create a slug from ``name`` that no other dashboard uses.

        Args:
            name: Dashboard name.
            exclude_id: Id of the dashboard being edited (its own slug is OK).

        Returns:
            ``slugify(name)``, suffixed with ``-2``, ``-3``... when taken.
        """
        base = slugify(name)
        if base == "new":  # reserved by the /dashboards/new route
            base = "new-dashboard"
        candidate, n = base, 2
        while True:
            q = Dashboard.query.filter_by(slug=candidate)
            if exclude_id is not None:
                q = q.filter(Dashboard.id != exclude_id)
            if q.first() is None:
                return candidate
            candidate = f"{base}-{n}"
            n += 1

    @property
    def is_image(self) -> bool:
        """``True`` for graphic (``image``) dashboards."""
        return self.kind == "image"

    @property
    def layout_data(self) -> dict:
        """The parsed graphic layout, with defaults for a new/empty drawing.

        Returns:
            Dict ``{"version", "canvas": {...}, "elements": [...]}``.
        """
        from app.dashboards.layout import empty_layout  # noqa: PLC0415

        if not self.layout:
            return empty_layout()
        try:
            data = json.loads(self.layout)
        except ValueError:
            return empty_layout()
        return data if isinstance(data, dict) else empty_layout()

    def set_layout(self, data: dict) -> None:
        """Store an (already sanitised) layout as compact JSON.

        Args:
            data: Output of :func:`app.dashboards.layout.sanitize_layout`.
        """
        self.layout = json.dumps(data, separators=(",", ":"))

    # --- tag links ---------------------------------------------------------------
    def link_for(self, datapoint) -> "ScreenTag | None":
        """The link of a tag to this screen.

        Args:
            datapoint: A :class:`DataPoint` or its id.

        Returns:
            The :class:`ScreenTag`, or ``None`` if the tag is not on this screen.
        """
        dp_id = getattr(datapoint, "id", datapoint)
        return next((link for link in self.tag_links if link.datapoint_id == dp_id), None)

    def has_tag(self, datapoint) -> bool:
        """Tell whether a tag is shown on this screen.

        Args:
            datapoint: A :class:`DataPoint` or its id.

        Returns:
            ``True`` if linked.
        """
        return self.link_for(datapoint) is not None

    def add_tag(self, datapoint, position: int | None = None) -> "ScreenTag":
        """Show a tag on this screen (no-op if it is already there).

        Args:
            datapoint: The tag (may be new / unflushed).
            position: Order on the screen; default = after the last tag.

        Returns:
            The (new or existing) :class:`ScreenTag`.
        """
        with db.session.no_autoflush:
            link = self.link_for(datapoint) if datapoint.id is not None else None
            if link is None:
                if position is None:
                    position = max((lk.position for lk in self.tag_links), default=-1) + 1
                link = ScreenTag(datapoint=datapoint, position=position)
                self.tag_links.append(link)
            elif position is not None:
                link.position = position
        if self.id is not None and self in db.session:
            db.session.expire(self, ["datapoints"])
        return link

    def remove_tag(self, datapoint) -> bool:
        """Remove a tag from this screen; the tag itself is kept.

        Args:
            datapoint: A :class:`DataPoint` or its id.

        Returns:
            ``True`` if a link was removed.
        """
        link = self.link_for(datapoint)
        if link is None:
            return False
        self.tag_links.remove(link)
        if self in db.session:
            db.session.expire(self, ["datapoints"])
        return True

    @property
    def plcs(self) -> list:
        """Distinct PLCs referenced by this dashboard's data points."""
        seen = {}
        for dp in self.datapoints:
            seen.setdefault(dp.plc_id, dp.plc)
        return list(seen.values())


class ScreenTag(db.Model):
    """Link between a screen and a tag shown on it, with the tag's order on that screen."""

    __tablename__ = "screen_tags"
    __table_args__ = (db.UniqueConstraint("dashboard_id", "datapoint_id", name="uq_screen_tags_dashboard_datapoint"),)

    id = db.Column(db.Integer, primary_key=True)
    dashboard_id = db.Column(db.Integer, db.ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True)
    datapoint_id = db.Column(db.Integer, db.ForeignKey("datapoints.id", ondelete="CASCADE"), nullable=False, index=True)
    #: Order of the tile on a grid screen.
    position = db.Column(db.Integer, nullable=False, default=0)

    dashboard = db.relationship("Dashboard", back_populates="tag_links")
    datapoint = db.relationship("DataPoint", back_populates="screen_links")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<ScreenTag 3:12>``."""
        return f"<ScreenTag {self.dashboard_id}:{self.datapoint_id}>"


#: Roles allowed to write a tag whose ``write_access`` is ``"roles"``.
datapoint_write_roles = db.Table(
    "datapoint_write_roles",
    db.Column("datapoint_id", db.Integer, db.ForeignKey("datapoints.id", ondelete="CASCADE"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

#: Who may write a writable tag: everybody with the ``write_values`` permission,
#: or only users whose role is selected in :attr:`DataPoint.write_roles`.
WRITE_ACCESS = {"all": "Everyone", "roles": "Based on user roles"}


class DataPoint(db.Model):
    """A PLC tag: one PLC variable, shown (and written) on any number of screens.

    Every tag is part of the tag table; screens show it through
    :class:`ScreenTag` links (``screens``).  A tag on no screen can still be
    used by alarms.

    The location is stored in decomposed form (``area``, ``db_number``,
    ``start``, ``bit``) together with the S7 ``data_type``; the human readable
    address (``DB1.DBX0.0``, ``MW20``...) is derived via :attr:`address`.
    """

    __tablename__ = "datapoints"

    WIDGETS = {
        "value": "Numeric / text value",
        "gauge": "Bar gauge (needs min/max)",
        "indicator": "Indicator lamp (BOOL)",
        "switch": "On/Off switch (BOOL, writable)",
    }

    #: Kinds of dummy tags (see :attr:`dummy_scope`).
    DUMMY_SCOPES = {
        "general": "General – same value everywhere (stored in the database)",
        "local": "Local – own value in each browser window",
    }

    id = db.Column(db.Integer, primary_key=True)
    plc_id = db.Column(db.Integer, db.ForeignKey("plcs.id", ondelete="CASCADE"), nullable=False)
    label = db.Column(db.String(100), nullable=False)

    # S7 location
    area = db.Column(db.String(2), nullable=False)  # DB, M, I, Q, T, C
    db_number = db.Column(db.Integer, nullable=False, default=0)
    start = db.Column(db.Integer, nullable=False, default=0)
    bit = db.Column(db.Integer, nullable=False, default=0)
    data_type = db.Column(db.String(10), nullable=False, default="INT")
    string_length = db.Column(db.Integer, nullable=False, default=0)
    #: Formatted address (``DB1.DBD4``, ``HR100``, ``AV3``…) kept for searching the tag table.
    address_text = db.Column(db.String(80), nullable=False, default="", server_default="", index=True)
    #: BACnet property (empty = presentValue); unused for S7/Modbus.
    property_name = db.Column(db.String(64), nullable=False, default="", server_default="")
    #: BACnet write priority 1..16 (0 = device default); unused for S7/Modbus.
    priority = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    # presentation
    widget = db.Column(db.String(20), nullable=False, default="value")
    unit = db.Column(db.String(20), nullable=False, default="")
    scale = db.Column(db.Float, nullable=False, default=1.0)
    offset = db.Column(db.Float, nullable=False, default=0.0)
    decimals = db.Column(db.Integer, nullable=False, default=2)
    min_value = db.Column(db.Float, nullable=True)
    max_value = db.Column(db.Float, nullable=True)
    true_text = db.Column(db.String(30), nullable=False, default="ON")
    false_text = db.Column(db.String(30), nullable=False, default="OFF")
    writable = db.Column(db.Boolean, nullable=False, default=False)
    #: ``all`` (every user allowed to write values) or ``roles`` (only :attr:`write_roles`).
    write_access = db.Column(db.String(10), nullable=False, default="all", server_default="all")
    width = db.Column(db.Integer, nullable=False, default=1)  # grid columns 1..4
    #: Dummy PLC tags only: ``general`` (one value for everybody, stored in the
    #: database) or ``local`` (an own value in every browser window).
    dummy_scope = db.Column(db.String(10), nullable=False, default="general", server_default="general")
    #: Save samples of the value in the tag history (see :mod:`app.history`).
    log_enabled = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())
    #: Seconds between two history samples (when :attr:`log_enabled`).
    log_interval = db.Column(db.Integer, nullable=False, default=60, server_default="60")

    plc = db.relationship("PLC", back_populates="datapoints", lazy="joined")
    #: Roles allowed to write this tag when :attr:`write_access` is ``roles``.
    write_roles = db.relationship("Role", secondary=datapoint_write_roles, order_by="Role.name",
                                  backref=db.backref("write_tags", lazy="select"))
    #: Links to the screens showing this tag (deleted with the tag).
    screen_links = db.relationship("ScreenTag", back_populates="datapoint", cascade="all, delete-orphan")
    #: Screens showing this tag, by name (read-only).
    screens = db.relationship("Dashboard", secondary="screen_tags", viewonly=True, order_by="Dashboard.name")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<DataPoint Temp DB1.DBD4>``."""
        return f"<DataPoint {self.label} {self.address}>"

    def clone(self, label: str | None = None) -> "DataPoint":
        """A new, unsaved tag with the same settings (not linked to any screen).

        Args:
            label: Label of the copy (default: ``"<label> (copy)"``).

        Returns:
            The new :class:`DataPoint`.
        """
        columns = [c.name for c in DataPoint.__table__.columns if c.name != "id"]
        copy = DataPoint(**{name: getattr(self, name) for name in columns})
        copy.label = label or f"{self.label} (copy)"[:100]
        return copy

    @property
    def screen_names(self) -> str:
        """Comma-separated names of the screens showing this tag (``""`` if none)."""
        return ", ".join(s.name for s in self.screens)

    @property
    def is_local(self) -> bool:
        """``True`` for a *local* dummy tag (value kept by each browser window, not by the server)."""
        return self.protocol == "dummy" and self.dummy_scope == "local"

    @property
    def is_logged(self) -> bool:
        """``True`` when samples of this tag are saved in the history (never for local dummy tags)."""
        return bool(self.log_enabled) and not self.is_local

    @property
    def is_chartable(self) -> bool:
        """``True`` when the history of this tag can be drawn as a trend (numbers and BOOL, not STRING)."""
        return self.data_type != "STRING"

    @property
    def scope_label(self) -> str:
        """Short kind of a dummy tag for lists: ``general`` / ``local`` (``""`` for real PLC tags)."""
        if self.protocol != "dummy":
            return ""
        return "local" if self.is_local else "general"

    def local_spec(self) -> dict:
        """Everything the browser needs to keep, check and display a local tag's value.

        Returns:
            Dict with ``data_type``, ``integer``, ``range`` (``[min, max]`` of
            an integer type or ``None``), ``string_length``, ``decimals``,
            ``unit``, ``true_text``, ``false_text`` and ``initial`` (the start
            value: 0, ``False`` or ``""``).
        """
        from app.plc.dummy import default_value  # noqa: PLC0415

        info = DATA_TYPES[self.data_type]
        rng = [info.min, info.max] if info.integer and info.min is not None else None
        return {"data_type": self.data_type, "integer": bool(info.integer), "range": rng,
                "numeric": self.is_numeric, "bool": self.is_bool,
                "string_length": (self.string_length or 254) if self.data_type == "STRING" else
                (1 if self.data_type == "CHAR" else 0),
                "decimals": self.decimals if self.decimals is not None else 2, "unit": self.unit or "",
                "true_text": self.true_text or "ON", "false_text": self.false_text or "OFF",
                "initial": default_value(self.data_type)}

    # --- address helpers ---------------------------------------------------
    @property
    def protocol(self) -> str:
        """Protocol of this data point's PLC (``s7``, ``modbus_tcp``, ``modbus_rtu``)."""
        return (self.plc.protocol if self.plc is not None else None) or "s7"

    @property
    def tag_address(self):
        """The location as a protocol-specific address object.

        Returns:
            :class:`~app.plc.addressing.S7Address` for S7 PLCs,
            :class:`~app.plc.modbus.ModbusAddress` for Modbus devices or
            :class:`~app.plc.bacnet.BACnetAddress` for BACnet devices.
        """
        from app.plc.protocols import make_address  # noqa: PLC0415

        return make_address(self.protocol, self.area, self.db_number or 0, self.start or 0, self.bit or 0,
                            self.data_type, self.string_length or 0, self.property_name or "", self.priority or 0)

    @property
    def s7_address(self):
        """Backward-compatible alias of :attr:`tag_address`."""
        return self.tag_address

    def set_address(self, address) -> None:
        """Copy all location fields from a parsed address.

        Args:
            address: :class:`~app.plc.addressing.S7Address` or
                :class:`~app.plc.modbus.ModbusAddress`.
        """
        self.area = address.area
        self.db_number = address.db_number
        self.start = address.start
        self.bit = address.bit
        self.data_type = address.data_type
        self.string_length = address.string_length
        prop = getattr(address, "property_name", "")
        self.property_name = "" if prop in ("", "presentValue") else prop
        self.priority = getattr(address, "priority", 0) or 0
        self.address_text = address.format()[:80]

    @property
    def address(self) -> str:
        """Address string, e.g. ``DB1.DBD4`` (S7), ``HR100`` (Modbus) or ``AV3`` (BACnet)."""
        return self.tag_address.format()

    @property
    def is_numeric(self) -> bool:
        """``True`` when the data type is a number (scaling applies)."""
        return is_numeric(self.data_type)

    @property
    def is_bool(self) -> bool:
        """``True`` for ``BOOL`` data points."""
        return self.data_type == "BOOL"

    # --- engineering value conversion --------------------------------------
    def to_engineering(self, raw):
        """Convert a raw PLC value to the displayed engineering value.

        Numeric values are transformed with ``raw * scale + offset`` and
        rounded to ``decimals``; other types are returned unchanged.

        Args:
            raw: The decoded value read from the PLC.

        Returns:
            The value to show on the dashboard.
        """
        if raw is None or not self.is_numeric:
            return raw
        value = raw * (self.scale if self.scale is not None else 1.0) + (self.offset or 0.0)
        if self.scale == 1.0 and not self.offset and DATA_TYPES[self.data_type].integer:
            return int(value)
        return round(value, self.decimals if self.decimals is not None else 2)

    def to_raw(self, value):
        """Convert an engineering value typed by the operator back to raw.

        Inverse of :meth:`to_engineering`: ``(value - offset) / scale``.
        Integer data types are rounded to the nearest whole number.

        Args:
            value: Engineering value (number, bool or string).

        Returns:
            The raw value to encode and write to the PLC.

        Raises:
            ValueError: If the value cannot be converted for this data type.
        """
        info = DATA_TYPES[self.data_type]
        if self.is_bool:
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "on", "yes"}
            return bool(value)
        if not self.is_numeric:
            return str(value)
        number = float(value)
        scale = self.scale or 1.0
        raw = (number - (self.offset or 0.0)) / scale
        if info.integer:
            raw = int(round(raw))
            if info.min is not None and not (info.min <= raw <= info.max):
                raise ValueError(f"{raw} out of range for {self.data_type} ({info.min}..{info.max})")
        return raw

    def format_value(self, value) -> str:
        """Format an engineering value for display, including the unit.

        Args:
            value: Result of :meth:`to_engineering`.

        Returns:
            Text such as ``"23.50 °C"``, ``"ON"`` or ``"—"`` for no value.
        """
        if value is None:
            return "—"
        if self.is_bool:
            return self.true_text if value else self.false_text
        if isinstance(value, float):
            text = f"{value:.{self.decimals}f}"
        else:
            text = str(value)
        return f"{text} {self.unit}".strip()

    @property
    def limited_write(self) -> bool:
        """``True`` when only the selected roles may write this tag."""
        return self.write_access == "roles"

    def may_write(self, user) -> bool:
        """Decide whether a user may write this tag.

        Rules:

        1. The tag must be writable.
        2. Server tags need the ``write_values`` permission.  Local dummy
           tags are only changed in the user's own browser window, so they
           need no permission (as before).
        3. With *Based on user roles* the user's role must be one of
           :attr:`write_roles` (this applies to every role, Admin included).

        Args:
            user: The current user (``AnonymousUser`` for visitors).

        Returns:
            ``True`` if allowed.
        """
        if not self.writable:
            return False
        if not self.is_local and not user.can(Perm.WRITE_VALUES):
            return False
        if self.limited_write:
            role_id = getattr(user, "role_id", None)
            return role_id is not None and role_id in {role.id for role in self.write_roles}
        return True

    def write_access_text(self) -> str:
        """Short description of who may write, e.g. ``Everyone`` or ``Operator, Admin``.

        Returns:
            Text in English (translate where it is shown).
        """
        if not self.limited_write:
            return WRITE_ACCESS["all"]
        return ", ".join(role.name for role in self.write_roles) or "—"

    def to_dict(self) -> dict:
        """Serialise the configuration of this data point for JSON.

        Returns:
            A dictionary with location, presentation and PLC fields.
        """
        return {
            "id": self.id,
            "label": self.label,
            "plc": self.plc.name if self.plc else None,
            "plc_id": self.plc_id,
            "address": self.address,
            "data_type": self.data_type,
            "widget": self.widget,
            "unit": self.unit,
            "min": self.min_value,
            "max": self.max_value,
            "writable": self.writable,
            "write_access": self.write_access,
            "write_roles": [role.name for role in self.write_roles] if self.limited_write else [],
            "is_bool": self.is_bool,
            "is_numeric": self.is_numeric,
            "true_text": self.true_text,
            "false_text": self.false_text,
            "plc_protocol": self.protocol,
            "string_length": self.string_length,
            "scale": self.scale,
            "offset": self.offset,
            "decimals": self.decimals,
            "dummy_scope": self.scope_label,
            "local": self.local_spec() if self.is_local else None,
            "log_enabled": self.log_enabled,
            "log_interval": self.log_interval,
        }
