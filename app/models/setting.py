"""System-wide settings stored as key/value rows.

Currently used for the *public home screen*: the screen shown to visitors who
are not logged in (e.g. a wall display, or after logout).  When it is not set,
anonymous access is completely disabled and visitors see the login page.
"""

from flask import g, has_request_context

from app.extensions import db

PUBLIC_HOME_SCREEN = "public_home_screen_id"


class Setting(db.Model):
    """One configuration value (stored as text)."""

    __tablename__ = "settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(500), nullable=True)

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<Setting public_home_screen_id=3>``."""
        return f"<Setting {self.key}={self.value}>"


def get_setting(key: str, default=None):
    """Read a setting value.

    Args:
        key: Setting name.
        default: Returned when the setting does not exist or is empty.

    Returns:
        The stored string, or ``default``.
    """
    row = db.session.get(Setting, key)
    return row.value if row is not None and row.value not in (None, "") else default


def set_setting(key: str, value) -> None:
    """Create or update a setting (the caller commits).

    Args:
        key: Setting name.
        value: New value (converted to ``str``; ``None`` clears it).
    """
    row = db.session.get(Setting, key)
    if row is None:
        row = Setting(key=key)
        db.session.add(row)
    row.value = None if value is None else str(value)
    if has_request_context():
        g.pop("_public_home", None)


def public_home_screen():
    """Return the configured public home screen if it is still public.

    The result is cached for the duration of the request.

    Returns:
        The :class:`~app.models.dashboard.Dashboard`, or ``None`` when not
        configured, deleted, or no longer marked public.
    """
    if has_request_context() and "_public_home" in g:
        return g._public_home
    from app.models.dashboard import Dashboard  # noqa: PLC0415

    screen = None
    raw = get_setting(PUBLIC_HOME_SCREEN)
    if raw and raw.isdigit():
        candidate = db.session.get(Dashboard, int(raw))
        if candidate is not None and candidate.is_public:
            screen = candidate
    if has_request_context():
        g._public_home = screen
    return screen


def public_access_enabled() -> bool:
    """Tell whether anonymous visitors may open public screens.

    Returns:
        ``True`` when a valid public home screen is configured.
    """
    return public_home_screen() is not None
