"""Addresses of published tags: automatic assignment and overlap checks.

Registers and bits are two separate address spaces (0 … 65535).  A tag of
type FLOAT32 at register 10 uses registers 10 and 11, so the next free
register is 12.  Reserved addresses – the heartbeat register and the quality
bits – are never given to tags.
"""

from __future__ import annotations

from app.i18n.catalog import gettext
from app.modbus_server.config import ServerConfig
from app.modbus_server.encoding import is_bit, register_count

MAX_ADDRESS = 65535


def area_of(mb_type: str) -> str:
    """``bit`` for ``BIT``, otherwise ``reg``."""
    return "bit" if is_bit(mb_type) else "reg"


def span(area: str, address: int, mb_type: str, string_length: int = 0) -> range:
    """Addresses used by one published tag."""
    count = 1 if area == "bit" else register_count(mb_type, string_length)
    return range(address, address + count)


def reserved(cfg: ServerConfig, tag_count: int) -> dict[str, set[int]]:
    """Addresses used by the heartbeat register and the quality bits.

    Args:
        cfg: Server settings.
        tag_count: Number of published tags (one quality bit each).
    """
    out = {"reg": set(), "bit": set()}
    if cfg.heartbeat is not None:
        out["reg"].add(cfg.heartbeat)
    if cfg.quality_base is not None:
        out["bit"].update(range(cfg.quality_base, min(MAX_ADDRESS + 1, cfg.quality_base + tag_count)))
    return out


def used(exports, exclude_id=None) -> dict[str, set[int]]:
    """Addresses used by published tags (``exclude_id``: the row being edited)."""
    out = {"reg": set(), "bit": set()}
    for exp in exports:
        if exp.id is not None and exp.id == exclude_id:
            continue
        out[exp.area].update(span(exp.area, exp.address, exp.mb_type, exp.string_length))
    return out


def next_free(area: str, count: int, taken: set[int]) -> int | None:
    """Lowest address with ``count`` free addresses in a row (``None`` when full)."""
    address = 0
    while address + count - 1 <= MAX_ADDRESS:
        clash = [a for a in range(address, address + count) if a in taken]
        if not clash:
            return address
        address = max(clash) + 1
    return None


def check(exp, exports, cfg: ServerConfig) -> list[str]:
    """Problems of one published tag's address (range, overlaps, reserved addresses).

    Args:
        exp: The row (new or edited; may be unsaved).
        exports: All published tags (the row itself is skipped by id).
        cfg: Server settings.

    Returns:
        Messages (empty = valid).
    """
    addresses = span(exp.area, exp.address, exp.mb_type, exp.string_length)
    if exp.address < 0 or addresses[-1] > MAX_ADDRESS:
        return [gettext("The address must be from 0 to {0} (last used address: {1}).", MAX_ADDRESS, addresses[-1])]
    problems = []
    for other in exports:
        if other.id is not None and other.id == exp.id or other.area != exp.area:
            continue
        theirs = span(other.area, other.address, other.mb_type, other.string_length)
        if set(addresses) & set(theirs):
            problems.append(gettext("Overlaps {0} ({1} {2}–{3}).", other.datapoint.label,
                                    gettext("bits") if other.area == "bit" else gettext("registers"),
                                    theirs[0], theirs[-1]))
    count = len(list(exports)) + (0 if exp.id else 1)
    res = reserved(cfg, count)[exp.area] & set(addresses)
    if res:
        what = gettext("the heartbeat register") if exp.area == "reg" else gettext("the quality bits")
        problems.append(gettext("Uses {0} (address {1}).", what, min(res)))
    return problems


def check_config(cfg: ServerConfig, exports) -> dict[str, str]:
    """Problems of the heartbeat / quality bit addresses with the published tags."""
    taken = used(exports)
    errors = {}
    if cfg.heartbeat is not None and cfg.heartbeat in taken["reg"]:
        errors["heartbeat"] = gettext("Register {0} is used by a published tag.", cfg.heartbeat)
    if cfg.quality_base is not None:
        bits = range(cfg.quality_base, cfg.quality_base + max(1, len(list(exports))))
        clash = sorted(set(bits) & taken["bit"])
        if clash:
            errors["quality_base"] = gettext("Bit {0} is used by a published tag (the quality bits need {1} bits "
                                             "from {2}).", clash[0], len(bits), cfg.quality_base)
        elif bits[-1] > MAX_ADDRESS:
            errors["quality_base"] = gettext("The quality bits would go past address {0}.", MAX_ADDRESS)
    return errors
