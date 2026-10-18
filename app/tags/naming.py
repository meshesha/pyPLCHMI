"""Rules for tag names (the *Label* of a tag).

A tag name works like an identifier – it is used in automations
(``tag_1 = tag_1 + 1``), in Excel imports and in the YAML of automations –
so it must be:

* **unique** – no other tag may have the same name (upper/lower case is
  ignored: ``Pump_1`` and ``pump_1`` are the same name);
* made of **letters, digits, ``_`` and ``.``** only – no spaces and no
  mathematical or other symbols (``+ - * / % = ( ) [ ] ,`` …); letters of
  any language are allowed;
* **not starting with a digit** (or a dot); a dot must stand between two
  parts, like in an e-mail address (``Tank_1.level``).

Names saved before these rules keep working; they must follow the rules as
soon as they are changed (:meth:`problems` reports what is wrong, and the
tag table marks such names).
"""

from __future__ import annotations

import re

from app.i18n.catalog import gettext

#: A valid tag name: a letter or ``_``, then letters, digits and ``_``; dots between parts.
NAME_RE = re.compile(r"^[^\W\d]\w*(?:\.\w+)*$", re.UNICODE)
#: Longest name (the database column holds 100 characters).
MAX_LENGTH = 100
#: Example shown in the messages.
EXAMPLE = "Tank_1.level"


def problems(name: str) -> list[str]:
    """What is wrong with the *form* of a name (uniqueness is checked by :func:`duplicate`).

    Args:
        name: The proposed name.

    Returns:
        Messages with instructions (empty = the name is valid).
    """
    name = name or ""
    if not name.strip():
        return [gettext("Enter a tag name, e.g. {0}.", EXAMPLE)]
    if NAME_RE.match(name) and len(name) <= MAX_LENGTH:
        return []
    out = []
    if len(name) > MAX_LENGTH:
        out.append(gettext("The tag name is too long (at most {0} characters).", MAX_LENGTH))
    if any(ch.isspace() for ch in name):
        out.append(gettext("The tag name cannot contain spaces – use _ instead (e.g. Tank_1_level)."))
    if name[0].isdigit():
        out.append(gettext("The tag name cannot start with a digit – start with a letter or _ (e.g. T1_level)."))
    bad = []
    for ch in name:
        if not (ch.isspace() or ch == "_" or ch == "." or re.match(r"\w", ch)) and ch not in bad:
            bad.append(ch)
    if bad:
        out.append(gettext("The tag name cannot contain {0} – use only letters, digits, _ and . (e.g. {1}).",
                           " ".join(bad), EXAMPLE))
    if name.startswith(".") or name.endswith(".") or ".." in name:
        out.append(gettext("A dot must stand between two parts of the name (not at the start or end, and not "
                           "two dots in a row), e.g. {0}.", EXAMPLE))
    if not out:   # e.g. "._x" or other rare forms
        out.append(gettext("Use only letters, digits, _ and . and start with a letter (e.g. {0}).", EXAMPLE))
    return out


def duplicate(name: str, exclude_id: int | None = None):
    """The tag that already uses this name (case-insensitive), or ``None``.

    Args:
        name: Proposed name.
        exclude_id: Id of the tag being edited.
    """
    from app.extensions import db  # noqa: PLC0415
    from app.models import DataPoint  # noqa: PLC0415

    q = DataPoint.query.filter(db.func.lower(DataPoint.label) == (name or "").strip().lower())
    if exclude_id is not None:
        q = q.filter(DataPoint.id != exclude_id)
    with db.session.no_autoflush:
        return q.order_by(DataPoint.id).first()


def duplicate_message(name: str, other) -> str:
    """Message for a name that is already used by ``other``."""
    where = other.plc.name if other.plc is not None else "?"
    return gettext("The name '{0}' is already used by another tag ({1} on {2}). Tag names must be unique "
                   "(upper/lower case is ignored).", name, other.label, where)


def check(name: str, current=None) -> list[str]:
    """All problems of a name for saving a tag.

    Names saved before the rules (``current`` unchanged) stay allowed; a new
    or changed name must follow the rules and be unique.

    Args:
        name: Proposed name.
        current: The tag being edited (``None`` for a new tag).

    Returns:
        Messages (empty = may be saved).
    """
    name = (name or "").strip()
    original = current.label if current is not None else None
    if original is not None and name == original:
        return []
    out = problems(name)
    if not out:
        other = duplicate(name, current.id if current is not None and current.id else None)
        if other is not None:
            out.append(duplicate_message(name, other))
    return out


def normalize(name: str) -> str:
    """Turn any text into a valid name, without checking uniqueness (``Tank temp (°C)`` → ``Tank_temp_C``)."""
    base = re.sub(r"\s+", "_", (name or "").strip())
    base = re.sub(r"[^\w.]", "_", base)
    base = re.sub(r"_+", "_", base)
    base = re.sub(r"\.{2,}", ".", base).strip("._") or "tag"
    base = re.sub(r"_?\._?", ".", base)
    if base[0].isdigit():
        base = "T" + base
    return base[:MAX_LENGTH - 4]


def suggest(name: str, exclude_id: int | None = None, taken: set | None = None) -> str:
    """A valid name that no other tag uses (:func:`normalize`, then ``_2``, ``_3`` … if taken).

    Args:
        name: Original text.
        exclude_id: Tag whose own name does not count as taken.
        taken: Extra names to avoid (lower case), e.g. names created in the same import.
    """
    base = normalize(name)
    candidate, n = base, 1
    taken = taken or set()
    while candidate.lower() in taken or duplicate(candidate, exclude_id) is not None:
        n += 1
        candidate = f"{base}_{n}"
    return candidate
