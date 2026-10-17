"""User, role and permission models.

Authorisation is permission based: every :class:`User` has exactly one
:class:`Role`, and every role owns a set of :class:`Permission` rows.  Views
check *permissions* (``user.can(Perm.MANAGE_PLCS)``) rather than role names,
so an administrator can change what a role is allowed to do from the web UI
without touching code.

Four built-in roles are created by default (see :data:`DEFAULT_ROLES`):

* **Admin**    – everything: users, roles/permissions, PLCs, dashboards, writes.
* **Operator** – can open every screen and the trends, and write values to
  writable tags (each tag can further limit writing to selected roles).
* **Viewer**   – can open every dashboard (read only) and the tag history / trends.
* **Guest**    – can open dashboards that are flagged *public* only.
"""

from datetime import datetime, timezone

from flask_login import AnonymousUserMixin, UserMixin

from app.extensions import bcrypt, db


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC ``datetime``.

    Returns:
        ``datetime.now(timezone.utc)``; used as a column default.
    """
    return datetime.now(timezone.utc)


class Perm:
    """String constants naming every permission known to the application."""

    MANAGE_USERS = "manage_users"
    MANAGE_ROLES = "manage_roles"
    MANAGE_PLCS = "manage_plcs"
    MANAGE_SCREENS = "manage_screens"
    VIEW_SCREENS = "view_screens"
    VIEW_PUBLIC_SCREENS = "view_public_screens"
    WRITE_VALUES = "write_values"
    VIEW_HISTORY = "view_history"
    VIEW_AUDIT = "view_audit"
    MANAGE_AUTOMATIONS = "manage_automations"

    #: Human-readable description for each permission (shown in the admin UI).
    DESCRIPTIONS = {
        MANAGE_USERS: "Create, edit and delete user accounts",
        MANAGE_ROLES: "Edit roles and their permissions",
        MANAGE_PLCS: "Add, edit and delete PLC connections",
        MANAGE_SCREENS: "Create and edit screens and their tags",
        VIEW_SCREENS: "Open every screen",
        VIEW_PUBLIC_SCREENS: "Open screens marked as public",
        WRITE_VALUES: "Write values to writable tags",
        VIEW_HISTORY: "View tag history and trend charts, export history to Excel",
        VIEW_AUDIT: "View the audit log (who changed what and when)",
        MANAGE_AUTOMATIONS: "Create, edit, run and delete automations",
    }

    @classmethod
    def all(cls) -> list[str]:
        """List every permission code.

        Returns:
            Permission codes in a stable display order.
        """
        return list(cls.DESCRIPTIONS.keys())


#: Roles created by ``flask seed`` and their default permissions.
DEFAULT_ROLES: dict[str, dict] = {
    "Admin": {"description": "Full control of the system", "permissions": Perm.all()},
    "Operator": {
        "description": "Operates the plant: all screens and trends, writes values to writable tags",
        "permissions": [Perm.VIEW_SCREENS, Perm.VIEW_PUBLIC_SCREENS, Perm.VIEW_HISTORY, Perm.WRITE_VALUES],
    },
    "Viewer": {
        "description": "Read-only access to all screens",
        "permissions": [Perm.VIEW_SCREENS, Perm.VIEW_PUBLIC_SCREENS, Perm.VIEW_HISTORY],
    },
    "Guest": {
        "description": "Access to public screens only",
        "permissions": [Perm.VIEW_PUBLIC_SCREENS],
    },
}

#: Colour themes a user can choose (Settings page): code -> label.
THEMES = {"dark": "Dark mode", "light": "Light mode", "auto": "Automatic (follow the operating system)"}

#: Role assigned to new users when none is selected.
DEFAULT_USER_ROLE = "Viewer"

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


#: Screens a user is limited to when ``User.restrict_screens`` is set.
user_screens = db.Table(
    "user_screens",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("dashboard_id", db.Integer, db.ForeignKey("dashboards.id", ondelete="CASCADE"), primary_key=True),
)


def screen_visible(role, restrict: bool, allowed_ids, screen) -> bool:
    """Central screen-access rule for logged-in users.

    1. Users who can manage screens (``manage_screens``) see everything.
    2. If the account is limited to selected screens (``restrict``), exactly
       those screens are visible – an explicit per-user grant, which may
       include non-public screens even for a Guest.
    3. Otherwise the role decides: ``view_screens`` → all screens,
       ``view_public_screens`` → public screens only.

    Args:
        role: The user's :class:`Role` (may be ``None``).
        restrict: Whether the user is limited to ``allowed_ids``.
        allowed_ids: Ids of the screens selected for the user.
        screen: The :class:`~app.models.dashboard.Dashboard` (screen) to check.

    Returns:
        ``True`` if the screen may be opened.
    """
    if role is None:
        return False
    if role.has_permission(Perm.MANAGE_SCREENS):
        return True
    if restrict:
        return screen.id in set(allowed_ids)
    if role.has_permission(Perm.VIEW_SCREENS):
        return True
    return bool(screen.is_public) and role.has_permission(Perm.VIEW_PUBLIC_SCREENS)


class Permission(db.Model):
    """A single named capability (e.g. ``manage_plcs``)."""

    __tablename__ = "permissions"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=False, default="")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<Permission manage_plcs>``."""
        return f"<Permission {self.code}>"

    @staticmethod
    def sync() -> None:
        """Make the ``permissions`` table match :class:`Perm`.

        Missing permission rows are inserted and descriptions are refreshed.
        Rows for permissions that no longer exist in code are left untouched
        so that no role assignment is silently lost.  The caller commits.
        """
        existing = {p.code: p for p in Permission.query.all()}
        for code, desc in Perm.DESCRIPTIONS.items():
            perm = existing.get(code)
            if perm is None:
                db.session.add(Permission(code=code, description=desc))
            else:
                perm.description = desc
        db.session.flush()


class Role(db.Model):
    """A named group of permissions that users are assigned to."""

    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=False, default="")
    permissions = db.relationship(
        "Permission", secondary=role_permissions, lazy="selectin", order_by="Permission.id"
    )
    users = db.relationship("User", back_populates="role", lazy="dynamic")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<Role Admin>``."""
        return f"<Role {self.name}>"

    @property
    def permission_codes(self) -> set[str]:
        """Set of permission codes granted to this role."""
        return {p.code for p in self.permissions}

    def has_permission(self, code: str) -> bool:
        """Check whether the role grants a permission.

        Args:
            code: Permission code, one of the :class:`Perm` constants.

        Returns:
            ``True`` if the role owns that permission.
        """
        return code in self.permission_codes

    def set_permissions(self, codes) -> None:
        """Replace the role's permissions with the given codes.

        Args:
            codes: Iterable of permission codes. Unknown codes are ignored.
        """
        wanted = set(codes)
        self.permissions = Permission.query.filter(Permission.code.in_(wanted)).all() if wanted else []

    @staticmethod
    def insert_defaults(reset_permissions: bool = False) -> None:
        """Create the default roles (Admin, Operator, Viewer, Guest).

        Safe to run repeatedly.  Existing roles keep their customised
        permissions unless ``reset_permissions`` is true.  The *Admin* role
        is always granted every permission so the system cannot be locked.

        Args:
            reset_permissions: Restore factory permissions on existing roles.
        """
        Permission.sync()
        for name, spec in DEFAULT_ROLES.items():
            role = Role.query.filter_by(name=name).first()
            created = role is None
            if created:
                role = Role(name=name, description=spec["description"])
                db.session.add(role)
            if created or reset_permissions or name == "Admin":
                role.set_permissions(spec["permissions"])
        db.session.commit()


class User(UserMixin, db.Model):
    """An application account."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(128), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)
    #: Screen opened right after login (``None`` = the screen list).
    home_screen_id = db.Column(
        db.Integer,
        # use_alter breaks the users <-> dashboards FK cycle (dashboards.created_by_id -> users).
        db.ForeignKey("dashboards.id", ondelete="SET NULL", use_alter=True, name="fk_users_home_screen_id"),
        nullable=True,
    )
    #: When true the user may only open the screens in :attr:`allowed_screens`.
    restrict_screens = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    #: Preferred user interface language (code of a language file, e.g. ``he``); ``None`` = default language.
    language = db.Column(db.String(16), nullable=True)
    #: Colour theme: ``dark``, ``light`` or ``auto`` (follow the operating system); ``None`` = dark.
    theme = db.Column(db.String(10), nullable=True)

    role = db.relationship("Role", back_populates="users", lazy="joined")
    home_screen = db.relationship("Dashboard", foreign_keys=[home_screen_id])
    allowed_screens = db.relationship("Dashboard", secondary=user_screens, lazy="selectin",
                                      backref=db.backref("allowed_users", lazy="selectin"))

    def __init__(self, **kwargs):
        """Create a user, defaulting the role to :data:`DEFAULT_USER_ROLE`.

        Args:
            **kwargs: Column values. ``password`` may be passed and is hashed.
        """
        password = kwargs.pop("password", None)
        super().__init__(**kwargs)
        if self.role is None and self.role_id is None:
            with db.session.no_autoflush:
                self.role = Role.query.filter_by(name=DEFAULT_USER_ROLE).first()
        if password is not None:
            self.set_password(password)

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<User admin>``."""
        return f"<User {self.username}>"

    # --- password handling -------------------------------------------------
    @property
    def password(self):
        """Write-only attribute; reading it is an error.

        Raises:
            AttributeError: Always – the plain password is never stored.
        """
        raise AttributeError("password is write-only")

    @password.setter
    def password(self, value: str) -> None:
        """Hash and store a new password (``user.password = "..."``).

        Args:
            value: The plain-text password.
        """
        self.set_password(value)

    def set_password(self, value: str) -> None:
        """Hash a plain-text password with bcrypt and store the hash.

        Args:
            value: The plain-text password.
        """
        self.password_hash = bcrypt.generate_password_hash(value).decode("utf-8")

    def check_password(self, value: str) -> bool:
        """Verify a plain-text password against the stored bcrypt hash.

        Args:
            value: Password typed by the user.

        Returns:
            ``True`` when the password matches.
        """
        if not self.password_hash:
            return False
        return bcrypt.check_password_hash(self.password_hash, value)

    # --- Flask-Login -------------------------------------------------------
    @property
    def is_active(self) -> bool:
        """Flask-Login hook: disabled accounts cannot log in."""
        return bool(self.active)

    # --- authorisation -----------------------------------------------------
    def can(self, code: str) -> bool:
        """Check whether the user's role grants a permission.

        Args:
            code: Permission code (see :class:`Perm`).

        Returns:
            ``True`` if allowed.
        """
        return self.role is not None and self.role.has_permission(code)

    @property
    def is_admin(self) -> bool:
        """``True`` when the user can manage roles (i.e. is an administrator)."""
        return self.can(Perm.MANAGE_ROLES)

    def can_view_dashboard(self, dashboard) -> bool:
        """Decide whether the user may open a screen (see :func:`screen_visible`).

        Args:
            dashboard: A :class:`~app.models.dashboard.Dashboard` (screen).

        Returns:
            ``True`` if allowed.
        """
        return screen_visible(self.role, self.restrict_screens, [s.id for s in self.allowed_screens], dashboard)

    @property
    def landing_screen(self):
        """The home screen to open after login, if set and still accessible.

        Returns:
            The :class:`Dashboard` or ``None``.
        """
        screen = self.home_screen
        return screen if screen is not None and self.can_view_dashboard(screen) else None

    def touch_login(self) -> None:
        """Record the current time as the last successful login."""
        self.last_login = utcnow()


class AnonymousUser(AnonymousUserMixin):
    """User object for visitors who are not logged in; has no permissions."""

    def can(self, code: str) -> bool:
        """Anonymous visitors have no permissions.

        Args:
            code: Permission code (ignored).

        Returns:
            Always ``False``.
        """
        return False

    @property
    def is_admin(self) -> bool:
        """Anonymous visitors are never administrators."""
        return False

    def can_view_dashboard(self, dashboard) -> bool:
        """Visitors who are not logged in may open *public* screens only.

        This is only possible when an administrator has configured a public
        home screen (System settings); otherwise anonymous access is off.

        Args:
            dashboard: A :class:`~app.models.dashboard.Dashboard` (screen).

        Returns:
            ``True`` for public screens while public access is enabled.
        """
        from app.models.setting import public_access_enabled  # noqa: PLC0415

        return bool(dashboard.is_public) and public_access_enabled()

    @property
    def landing_screen(self):
        """Anonymous visitors land on the public home screen (if configured).

        Returns:
            The public home :class:`Dashboard` or ``None``.
        """
        from app.models.setting import public_home_screen  # noqa: PLC0415

        return public_home_screen()
