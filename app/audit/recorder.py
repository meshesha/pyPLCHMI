"""Record user actions in the audit log (:class:`~app.models.audit.AuditLog`).

Automatic part – :func:`install` registers an ``after_flush`` listener on
SQLAlchemy sessions.  For every flush that happens *during a web request* it
writes one row per created, changed or deleted object of an audited model
(:data:`AUDITED`), with the user, the time, the browser's IP address and the
values before and after (columns and many-to-many lists such as a role's
permissions or a PLC's files).  Password hashes are never written; long
values (screen layouts) are shortened.

Explicit part – :func:`record` for actions that are not database changes:
log in, failed log in, log out and writing a value to a PLC.

Background work (alarm scanner, history logger, CLI) runs outside a request
and is not recorded; code inside a request that changes data on behalf of
the system (an alarm scan started by a page request) runs inside
:func:`paused`.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

#: Longest value text stored per field.
MAX_VALUE = 500
#: Fields never shown with their value.
HIDDEN_FIELDS = {"password_hash"}
#: Fields not recorded (bookkeeping).
SKIPPED_FIELDS = {"last_login", "created_at", "updated_at", "address_text",
                  "plcs"}  # InfoFile.plcs: the same change is recorded on the PLC ("files")

_local = threading.local()


def _audited() -> dict:
    """Audited model classes and the object type names shown in the log.

    Returns:
        ``class -> name``.
    """
    from app.models import (  # noqa: PLC0415
        PLC, Alarm, AlarmEvent, Asset, AssetFolder, Dashboard, DataPoint, InfoFile, Role, ScreenTag, Setting,
        TrendChart, User,
    )

    return {User: "User", Role: "Role", PLC: "PLC", Dashboard: "Screen", DataPoint: "Tag", ScreenTag: "Screen tag",
            Alarm: "Alarm", AlarmEvent: "Alarm event", Asset: "Image", AssetFolder: "Image folder",
            Setting: "Setting", TrendChart: "Trend chart", InfoFile: "Library file"}


#: Object type names (for the filter of the audit page).
OBJECT_TYPES = ["User", "Role", "PLC", "Screen", "Tag", "Screen tag", "Alarm", "Alarm event", "Image",
                "Image folder", "Library file", "Setting", "Trend chart", "Value"]


@contextmanager
def paused():
    """Do not record database changes made inside this block (system work in a request)."""
    depth = getattr(_local, "paused", 0)
    _local.paused = depth + 1
    try:
        yield
    finally:
        _local.paused = depth


def is_paused() -> bool:
    """Whether recording is paused in this thread."""
    return getattr(_local, "paused", 0) > 0


def label_of(obj) -> str:
    """A readable name of an object.

    Args:
        obj: Model instance (or anything).

    Returns:
        Name, label, user name … or ``#id``.
    """
    if obj is None:
        return ""
    cls = type(obj).__name__
    if cls == "ScreenTag":
        screen = getattr(obj, "dashboard", None)
        tag = getattr(obj, "datapoint", None)
        return f"{label_of(screen)} → {label_of(tag)}"
    if cls == "Setting":
        return str(getattr(obj, "key", ""))
    for attr in ("username", "name", "label", "code", "title"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value:
            return value[:200]
    return f"#{getattr(obj, 'id', '?')}"


def _value(value):
    """JSON-friendly, shortened copy of a value.

    Args:
        value: Column value.

    Returns:
        ``None``, bool, number or text.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    text = value if isinstance(value, str) else label_of(value) if hasattr(value, "__table__") else str(value)
    if len(text) > MAX_VALUE:
        text = text[:MAX_VALUE] + f"… ({len(text)} characters)"
    return text


def _fk_label(session, column, value):
    """Show a foreign key as ``id (name)`` when the referenced row exists.

    Args:
        session: The session.
        column: The column.
        value: The id.

    Returns:
        Display value.
    """
    if value is None or not column.foreign_keys:
        return _value(value)
    table = next(iter(column.foreign_keys)).column.table
    try:
        from app.extensions import db  # noqa: PLC0415

        for mapper in db.Model.registry.mappers:
            if mapper.local_table is table:
                with session.no_autoflush:
                    target = session.get(mapper.class_, value)
                return f"{value} ({label_of(target)})" if target is not None else _value(value)
    except Exception:  # noqa: BLE001 - a label is optional
        pass
    return _value(value)


def _column_changes(session, obj, mode: str) -> list[dict]:
    """Changed columns and many-to-many lists of an object.

    Args:
        session: The session.
        obj: The object.
        mode: ``create``, ``update`` or ``delete``.

    Returns:
        ``[{"field", "before", "after"}]``.
    """
    state = inspect(obj)
    mapper = state.mapper
    out = []
    for prop in mapper.column_attrs:
        key = prop.key
        if key == "id" or key in SKIPPED_FIELDS:
            continue
        column = prop.columns[0]
        hist = state.attrs[key].history
        if mode == "update":
            if not hist.has_changes():
                continue
            before = hist.deleted[0] if hist.deleted else None
            after = hist.added[0] if hist.added else None
            if before == after:
                continue
        elif mode == "create":
            before, after = None, getattr(obj, key)
            if after is None or after == "":
                continue
        else:  # delete
            before, after = (hist.unchanged or hist.deleted or [None])[0], None
            if before is None or before == "":
                continue
        if key in HIDDEN_FIELDS:
            out.append({"field": key, "before": "•••" if before else None, "after": "•••" if after else None})
            continue
        out.append({"field": key, "before": _fk_label(session, column, before),
                    "after": _fk_label(session, column, after)})
    for rel in mapper.relationships:
        if rel.secondary is None or rel.viewonly or not rel.uselist or rel.key in SKIPPED_FIELDS:
            continue
        hist = state.attrs[rel.key].history
        if mode == "update" and not hist.has_changes():
            continue
        if mode != "update" and not (hist.added or hist.unchanged or hist.deleted):
            continue
        unchanged = list(hist.unchanged or [])
        before = sorted(label_of(o) for o in unchanged + list(hist.deleted or []))
        after = sorted(label_of(o) for o in unchanged + list(hist.added or []))
        if mode == "create":
            before = []
        if mode == "delete":
            after = []
        if before != after:
            out.append({"field": rel.key, "before": ", ".join(before), "after": ", ".join(after)})
    return out


def _who() -> dict:
    """User, name and IP of the current request.

    Returns:
        Dict with ``user_id``, ``username`` and ``ip``.
    """
    from flask import request  # noqa: PLC0415
    from flask_login import current_user  # noqa: PLC0415

    user_id, username = None, ""
    try:
        if current_user and current_user.is_authenticated:
            user_id, username = current_user.id, current_user.username
    except Exception:  # noqa: BLE001 - no login manager in some contexts
        pass
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or "")[:45]
    return {"user_id": user_id, "username": username, "ip": ip}


def _after_flush(session, flush_context) -> None:
    """Write audit rows for the objects of this flush (``after_flush`` listener).

    Args:
        session: The flushed session.
        flush_context: Unused.
    """
    from flask import has_request_context  # noqa: PLC0415

    if not has_request_context() or is_paused():
        return
    from app.models.audit import AuditLog  # noqa: PLC0415

    audited = _audited()
    rows = []
    now = datetime.now(timezone.utc)
    who = None
    for mode, objects in (("create", session.new), ("update", session.dirty), ("delete", session.deleted)):
        for obj in list(objects):
            kind = audited.get(type(obj))
            if kind is None:
                continue
            if mode == "update" and not session.is_modified(obj, include_collections=True):
                continue
            changes = _column_changes(session, obj, mode)
            if mode == "update" and not changes:
                continue
            who = who or _who()
            rows.append(dict(who, ts=now, action=mode, object_type=kind, object_id=getattr(obj, "id", None),
                             object_label=label_of(obj)[:200], changes_json=json.dumps(changes, ensure_ascii=False)))
    if rows:
        session.connection().execute(AuditLog.__table__.insert(), rows)


def install() -> None:
    """Register the listener once (called by the application factory)."""
    if not event.contains(Session, "after_flush", _after_flush):
        event.listen(Session, "after_flush", _after_flush)


def record(action: str, object_type: str = "", object_id: int | None = None, label: str = "",
           changes: list[dict] | None = None, username: str | None = None, commit: bool = True) -> None:
    """Write an explicit audit row (log in, log out, value written…).

    Args:
        action: An :data:`~app.models.audit.ACTIONS` code.
        object_type: Kind of object (``User``, ``Value`` …).
        object_id: Its id.
        label: Its name.
        changes: ``[{"field", "before", "after"}]``.
        username: User name to store when nobody is logged in (failed log in).
        commit: Commit the session afterwards.
    """
    from flask import has_request_context  # noqa: PLC0415

    from app.extensions import db  # noqa: PLC0415
    from app.models.audit import AuditLog  # noqa: PLC0415

    who = _who() if has_request_context() else {"user_id": None, "username": "", "ip": ""}
    if username is not None and not who["username"]:
        who["username"] = username[:64]
    entry = AuditLog(user_id=who["user_id"], username=who["username"], ip=who["ip"], action=action,
                     object_type=object_type, object_id=object_id, object_label=(label or "")[:200],
                     changes_json=json.dumps([{"field": c["field"], "before": _value(c.get("before")),
                                               "after": _value(c.get("after"))} for c in (changes or [])],
                                             ensure_ascii=False))
    db.session.add(entry)
    if commit:
        db.session.commit()
