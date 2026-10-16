"""Time zone of the user's browser for dates shown and entered in the UI.

Dates are stored in UTC.  Every page sets the cookie ``hmi_tz`` to the
browser's IANA zone name (``Intl.DateTimeFormat().resolvedOptions().timeZone``,
e.g. ``Asia/Jerusalem``); server-rendered dates, date filters and Excel files
use that zone.  Without the cookie (first request, very old browser) the
``DISPLAY_TIMEZONE`` setting or the server's own zone is used.

On Windows the zone names need the ``tzdata`` package (see requirements.txt).
"""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from urllib.parse import unquote

#: Cookie written by base.html.
COOKIE = "hmi_tz"


def zone_by_name(name: str | None) -> tzinfo | None:
    """Look up an IANA time zone.

    Args:
        name: Zone name such as ``Europe/Berlin``.

    Returns:
        The zone, or ``None`` if the name is empty or unknown.
    """
    if not name or len(name) > 64:
        return None
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown name, missing tzdata, bad characters
        return None


def server_zone() -> tzinfo:
    """The zone of the web server's clock.

    Returns:
        A fixed-offset zone (current offset of the server).
    """
    return datetime.now().astimezone().tzinfo or timezone.utc


def user_zone() -> tzinfo:
    """Time zone for the current request.

    Order: ``hmi_tz`` cookie, ``DISPLAY_TIMEZONE`` config, server zone.

    Returns:
        A ``tzinfo``.
    """
    from flask import current_app, has_request_context, request  # noqa: PLC0415

    if has_request_context():
        zone = zone_by_name(unquote(request.cookies.get(COOKIE) or ""))
        if zone is not None:
            return zone
    try:
        zone = zone_by_name(current_app.config.get("DISPLAY_TIMEZONE"))
    except RuntimeError:  # outside an application context
        zone = None
    return zone or server_zone()


def zone_label(zone: tzinfo) -> str:
    """Name of a zone for the UI (IANA name or UTC offset).

    Args:
        zone: The zone.

    Returns:
        ``Asia/Jerusalem`` or ``UTC+03:00``.
    """
    key = getattr(zone, "key", None)
    if key:
        return key
    offset = datetime.now(zone).utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset is not None else 0
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def to_local(value: datetime | None, zone: tzinfo | None = None) -> datetime | None:
    """Convert a UTC datetime (naive values count as UTC) to the user's zone.

    Args:
        value: The datetime.
        zone: Target zone (default :func:`user_zone`).

    Returns:
        An aware datetime in the zone, or ``None``.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone or user_zone())


def parse_local(text: str | None, zone: tzinfo | None = None) -> datetime | None:
    """Parse a date entered in the browser's zone into UTC.

    Accepts ``YYYY-MM-DDTHH:MM[:SS]`` (``datetime-local`` inputs),
    ``YYYY-MM-DD HH:MM[:SS]`` and ``YYYY-MM-DD`` (midnight).

    Args:
        text: The entered text.
        zone: Zone of the text (default :func:`user_zone`).

    Returns:
        An aware UTC datetime, or ``None`` if empty or invalid.
    """
    if not text:
        return None
    text = text.strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=zone or user_zone()).astimezone(timezone.utc)
    return None


def format_local(value: datetime | None, zone: tzinfo | None = None, seconds: bool = True) -> str:
    """Format a UTC datetime as full local date and time.

    Args:
        value: The datetime.
        zone: Target zone (default :func:`user_zone`).
        seconds: Include the seconds.

    Returns:
        ``YYYY-MM-DD HH:MM:SS`` (or ``""`` for ``None``).
    """
    local = to_local(value, zone)
    if local is None:
        return ""
    return local.strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")
