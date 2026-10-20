"""How a faceplate instance finds its tags.

The template screen of a :class:`~app.models.faceplate.Faceplate` is drawn
with the tags of a reference device whose names start with the faceplate's
prefix::

    template prefix  Panel_01.MeterA.
    template tags    Panel_01.MeterA.voltage_l1   → parameter "voltage_l1"
                     Panel_01.MeterA.power        → parameter "power"
                     Site.frequency               → fixed tag (same in every window)

An instance with the prefix ``Panel_37.MeterB.`` shows
``Panel_37.MeterB.voltage_l1`` and ``Panel_37.MeterB.power`` instead (names are
compared without regard to upper/lower case).  A parameter can also be bound
to any other tag by hand (``overrides``).

Functions:

* :func:`parameters` / :func:`fixed_tags` – what the template uses;
* :func:`resolve` / :func:`point_map` – the tags of one instance;
* :func:`scan` – find devices among the existing tags (prefixes that have the
  parameter names);
* :func:`instances_of_tag` – which instances show a tag (for permissions);
* :func:`create_tags` – create a new device's tags by copying the reference tags;
* :func:`substitute_layout` – the template layout with the instance's tags.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from app.extensions import db
from app.i18n.catalog import gettext
from app.models import DataPoint, Faceplate, FaceplateInstance


def _naming():
    """The tag naming rules (imported late to avoid an import cycle through ``app.tags``)."""
    from app.tags import naming  # noqa: PLC0415

    return naming


@dataclass
class Parameter:
    """One parameter of a faceplate.

    Attributes:
        name: Name as written in the reference tag (``voltage_l1``).
        key: Lower-case name (used for matching).
        ref: The reference device's tag.
    """

    name: str
    key: str
    ref: DataPoint


def natural_key(text: str) -> list:
    """Sort key that puts ``Panel_2`` before ``Panel_10``."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text or "")]


def template_tags(fp: Faceplate) -> list[DataPoint]:
    """The tags of the template screen (empty when the screen was deleted)."""
    return list(fp.screen.datapoints) if fp.screen is not None else []


def parameters(fp: Faceplate) -> list[Parameter]:
    """The parameters: template tags whose name starts with the faceplate's prefix (sorted by name)."""
    prefix = (fp.prefix or "").lower()
    out = []
    if not prefix:
        return out
    for dp in template_tags(fp):
        label = dp.label or ""
        if label.lower().startswith(prefix) and len(label) > len(prefix):
            name = label[len(prefix):]
            out.append(Parameter(name=name, key=name.lower(), ref=dp))
    out.sort(key=lambda p: natural_key(p.name))
    return out


def fixed_tags(fp: Faceplate) -> list[DataPoint]:
    """Template tags that are not parameters (shown unchanged in every window)."""
    keys = {p.ref.id for p in parameters(fp)}
    return [dp for dp in template_tags(fp) if dp.id not in keys]


def suggest_prefix(tags) -> str:
    """The reference prefix most template tags share, ending after a ``.`` or ``_``.

    ``Panel_01.MeterA.voltage``, ``Panel_01.MeterA.power``, ``Site.freq`` →
    ``Panel_01.MeterA.`` (the longest beginning shared by at least two tags
    and by at least half of them; with one tag: its beginning up to the last
    ``.`` or ``_``).
    """
    labels = [dp.label for dp in tags if dp.label]
    if not labels:
        return ""
    counts: dict[str, list] = {}
    for label in labels:
        for cut in (i + 1 for i, ch in enumerate(label) if ch in "._" and i + 1 < len(label)):
            entry = counts.setdefault(label[:cut].lower(), [label[:cut], 0])
            entry[1] += 1
    need = 1 if len(labels) == 1 else max(2, (len(labels) + 1) // 2)
    good = [text for text, count in counts.values() if count >= need]
    return max(good, key=len) if good else ""


def check_prefix(prefix: str) -> list[str]:
    """Problems of a tag-name prefix (it must make valid tag names when a parameter name follows)."""
    prefix = (prefix or "").strip()
    if not prefix:
        return [gettext("Enter the tag-name prefix of the device, e.g. {0}.", "Panel_01.MeterA.")]
    if len(prefix) > 90:
        return [gettext("The prefix is too long (at most {0} characters).", 90)]
    problems = _naming().problems(prefix + "x")
    return [gettext("The prefix must be the beginning of a valid tag name: {0}", " ".join(problems))] if problems else []


def tag_index() -> dict[str, DataPoint]:
    """Every tag by lower-case name (one query; used when many instances are resolved)."""
    return {dp.label.lower(): dp for dp in DataPoint.query.all() if dp.label}


def resolve(inst: FaceplateInstance, params: list[Parameter] | None = None,
            index: dict | None = None) -> dict[str, DataPoint | None]:
    """The tag of every parameter for one instance.

    Args:
        inst: The instance.
        params: :func:`parameters` of its faceplate (computed when ``None``).
        index: :func:`tag_index` (single lookups when ``None``).

    Returns:
        Parameter key → tag, or ``None`` when the instance has no such tag.
    """
    params = parameters(inst.faceplate) if params is None else params
    overrides = inst.overrides
    out: dict[str, DataPoint | None] = {}
    prefix = inst.prefix or ""
    for p in params:
        dp = None
        if p.key in overrides:
            dp = db.session.get(DataPoint, overrides[p.key])
        if dp is None:
            name = (prefix + p.name).lower()
            if index is not None:
                dp = index.get(name)
            else:
                dp = DataPoint.query.filter(db.func.lower(DataPoint.label) == name).first()
        out[p.key] = dp
    return out


def point_map(inst: FaceplateInstance) -> tuple[dict[int, int | None], list[str]]:
    """Template tag id → the instance's tag id.

    Returns:
        ``(mapping, missing)`` – fixed tags map to themselves; parameters
        without a tag map to ``None`` and their names are listed in ``missing``.
    """
    fp = inst.faceplate
    params = parameters(fp)
    found = resolve(inst, params)
    mapping: dict[int, int | None] = {dp.id: dp.id for dp in fixed_tags(fp)}
    missing = []
    for p in params:
        dp = found[p.key]
        mapping[p.ref.id] = dp.id if dp is not None else None
        if dp is None:
            missing.append(p.name)
    return mapping, missing


def substitute_layout(layout: dict, mapping: dict[int, int | None]) -> dict:
    """A copy of the template layout with every tag replaced (``point``, ``visPoint``).

    Elements of a parameter without a tag keep their shape but show no value;
    a visibility rule on such a tag is dropped (the element stays visible).
    """
    out = copy.deepcopy(layout)
    for el in out.get("elements", []):
        if el.get("point"):
            el["point"] = mapping.get(el["point"], el["point"])
        if el.get("visPoint"):
            new = mapping.get(el["visPoint"], el["visPoint"])
            el["visPoint"] = new
            if new is None and el.get("vis") == "tag":
                el["vis"] = "always"
    return out


def instances_of_tag(dp: DataPoint) -> list[FaceplateInstance]:
    """Instances whose window shows this tag (by prefix + parameter name, or bound by hand)."""
    label = (dp.label or "").lower()
    out = []
    cache: dict[int, set] = {}
    for inst in FaceplateInstance.query.all():
        if dp.id in inst.overrides.values():
            out.append(inst)
            continue
        prefix = (inst.prefix or "").lower()
        if not prefix or not label.startswith(prefix) or len(label) == len(prefix):
            continue
        keys = cache.get(inst.faceplate_id)
        if keys is None:
            keys = cache[inst.faceplate_id] = {p.key for p in parameters(inst.faceplate)}
        if label[len(prefix):] in keys and inst.overrides.get(label[len(prefix):]) in (None, dp.id):
            out.append(inst)
    return out


def scan(fp: Faceplate) -> list[dict]:
    """Find devices among the existing tags: prefixes followed by the parameter names.

    ``Panel_02.MeterA.voltage_l1`` and ``Panel_02.MeterA.power`` → candidate
    prefix ``Panel_02.MeterA.`` with 2 of the parameters.  The reference prefix
    and the prefixes of existing instances are left out.

    Returns:
        ``[{"prefix", "name", "found", "total", "missing": [names]}]`` sorted by prefix.
    """
    params = parameters(fp)
    if not params:
        return []
    taken = {(fp.prefix or "").lower()} | {(i.prefix or "").lower() for i in fp.instances}
    found: dict[str, dict] = {}
    for dp in DataPoint.query.all():
        label = dp.label or ""
        low = label.lower()
        for p in params:
            if low.endswith(p.key) and len(low) > len(p.key):
                prefix = label[: len(label) - len(p.name)]
                if prefix.lower() in taken:
                    continue
                entry = found.setdefault(prefix.lower(), {"prefix": prefix, "keys": set()})
                entry["keys"].add(p.key)
    out = []
    for entry in found.values():
        keys = entry["keys"]
        out.append({"prefix": entry["prefix"], "name": instance_name(entry["prefix"]), "found": len(keys),
                    "total": len(params), "missing": [p.name for p in params if p.key not in keys]})
    # Prefixes that only match by accident (one short common ending) are rare; keep the best first.
    out.sort(key=lambda c: natural_key(c["prefix"]))
    return out


def shifted_address(ref: DataPoint, protocol: str, db_shift: int, start_shift: int):
    """The reference tag's address moved by the shifts, checked with the protocol's rules.

    Returns:
        ``(address or None for dummy tags, error message or None)``.
    """
    from app.plc.addressing import AddressError  # noqa: PLC0415
    from app.plc.protocols import make_address, parse_tag_address  # noqa: PLC0415

    if protocol == "dummy":
        return None, None
    db_number = (ref.db_number or 0) + (db_shift if ref.area == "DB" else 0)
    start = (ref.start or 0) + int(start_shift or 0)
    if db_shift and ref.area != "DB" and protocol == "s7":
        return None, gettext("{0} is not in a DB – the DB shift cannot be used.", ref.address)
    if start < 0 or db_number < 0:
        return None, gettext("the shifted address would be negative.")
    address = make_address(protocol, ref.area, db_number, start, ref.bit or 0, ref.data_type, ref.string_length or 0,
                           ref.property_name or "", ref.priority or 0, ref.path or "")
    try:
        parse_tag_address(address.format(True), ref.data_type, ref.string_length or None, protocol)
    except (AddressError, ValueError) as exc:
        return None, gettext("invalid shifted address {0}: {1}", address.format(), exc)
    return address, None


def instance_name(prefix: str) -> str:
    """A readable name from a prefix: ``Panel_37.MeterB.`` → ``Panel 37 · MeterB``."""
    text = (prefix or "").strip("._-")
    return " · ".join(part.replace("_", " ").strip() for part in text.split(".") if part.strip("_ ")) or prefix


def create_tags(fp: Faceplate, prefix: str, plc=None, db_shift: int = 0,
                start_shift: int = 0) -> tuple[list[DataPoint], list[str]]:
    """Create the missing tags of a device by copying the reference device's tags.

    Each parameter without a tag gets a copy of the reference tag named
    ``prefix + parameter`` – same type, scaling, limits, write settings and
    history settings, and the reference address **shifted**:

    * ``db_shift`` – added to the DB number of S7 ``DB`` addresses (each
      device has its own DB: DB50 → DB51 …);
    * ``start_shift`` – added to the start address: the byte offset (S7), the
      register / coil number (Modbus) or the object instance (BACnet), e.g. a
      DB with an array of meters, 20 bytes each.

    With ``plc`` the copies go to that PLC (typical: one Modbus device per
    meter with the same register map – then no shift is needed); it must use
    the same protocol as the reference tag's PLC.  Copies on the reference
    PLC need a shift (otherwise they would read the reference device).

    Nothing is saved when any copy is not possible.

    Args:
        fp: The faceplate.
        prefix: The new device's prefix.
        plc: Target :class:`~app.models.plc.PLC`, or ``None`` = the reference tag's PLC.
        db_shift: DB number shift (S7 DB addresses).
        start_shift: Start address shift.

    Returns:
        ``(created tags (added to the session, not committed), problems)``.
    """
    from app.plc.dummy import next_slot  # noqa: PLC0415

    problems = check_prefix(prefix)
    if problems:
        return [], problems
    params = parameters(fp)
    if not params:
        return [], [gettext("The faceplate has no parameters – check its template screen and prefix.")]
    index = tag_index()
    plan: list[tuple[Parameter, str, object]] = []
    taken = set()
    for p in params:
        name = prefix + p.name
        if name.lower() in index or name.lower() in taken:
            continue                                   # the device already has this tag
        ref = p.ref
        target = plc or ref.plc
        if target is None or ref.plc is None:
            problems.append(gettext("{0}: the reference tag has no PLC.", ref.label))
            continue
        if (target.protocol or "s7") != (ref.plc.protocol or "s7"):
            problems.append(gettext("{0}: {1} uses another protocol than {2} – choose a PLC of the same kind.",
                                    p.name, target.name, ref.plc.name))
            continue
        issues = _naming().problems(name)
        if issues:
            problems.append(f"{name}: " + " ".join(issues))
            continue
        protocol = target.protocol or "s7"
        if protocol != "dummy" and target.id == ref.plc_id and not db_shift and not start_shift:
            problems.append(gettext("The copies would read the same addresses as the reference device on {0} – "
                                    "choose another PLC or an address shift.", target.name))
            break
        address, error = shifted_address(ref, protocol, db_shift, start_shift)
        if error:
            problems.append(f"{p.name}: {error}")
            continue
        taken.add(name.lower())
        plan.append((p, name, target, address))
    if problems:
        return [], problems
    # Everything checked: create the copies (only now, so a refused request leaves nothing behind).
    created: list[DataPoint] = []
    slots: dict[int, int] = {}
    with db.session.no_autoflush:
        for p, name, target, address in plan:
            dp = p.ref.clone(label=name)
            dp.plc_id = target.id
            if address is not None:
                dp.db_number, dp.start = address.db_number, address.start
                dp.address_text = address.format()[:80]
            if target.protocol == "dummy":
                slots[target.id] = slots.get(target.id) or next_slot(target.id) - 1
                slots[target.id] += 1
                dp.start = slots[target.id]
                dp.address_text = f"D{dp.start}"
            db.session.add(dp)
            dp.write_roles = list(p.ref.write_roles)
            created.append(dp)
    return created, []


def window_screens(dp: DataPoint, user) -> list:
    """Template screens through which ``user`` sees ``dp`` in a device window.

    Used by the write and *Details* APIs: a tag shown in a faceplate window
    may be written / inspected like a tag on the template screen.
    """
    screens, seen = [], set()
    for inst in instances_of_tag(dp):
        screen = inst.faceplate.screen if inst.faceplate else None
        if screen is not None and screen.id not in seen and user.can_view_dashboard(screen):
            seen.add(screen.id)
            screens.append(screen)
    return screens
