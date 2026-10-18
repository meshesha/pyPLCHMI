"""Import of graphic screens from SVG files (Screens page → *Import SVG*).

Two kinds of files are accepted:

1. **Files exported by the designer** (*Export SVG*).  They carry the whole
   screen in ``<metadata id="pyplchmi-screen">`` (JSON: layout, descriptions
   of the tags used, embedded images, names of linked screens).  The screen
   is rebuilt fully editable:

   * tags are matched by id (when label and PLC also match), otherwise by
     label + PLC name; a missing tag is created when its PLC exists (same
     name), otherwise its bindings are removed (reported);
   * images are added to the image library (identical files are reused);
   * links to other screens are matched by screen name;
   * the layout is validated with :func:`~app.dashboards.layout.sanitize_layout`.

2. **Any other SVG drawing** (Inkscape, AutoCAD/Visio exports…).  It is checked
   like an uploaded image (no scripts, event handlers or entities), stored in
   the image library and used as the background of a new Image screen whose
   canvas has the drawing's size – HMI elements can then be placed on top.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

from app.assets.folders import FolderError, normalize_path, store_asset
from app.assets.storage import AssetError, delete_file
from app.dashboards.layout import CANVAS_SPEC, LayoutError, sanitize_layout
from app.extensions import db
from app.models import PLC, Asset, Dashboard, DataPoint
from app.plc.addressing import AddressError
from app.plc.protocols import parse_tag_address
from app.i18n.catalog import gettext

#: Largest SVG file accepted (exports embed their images).
MAX_SVG_BYTES = 25 * 1024 * 1024

_UNSAFE = re.compile(r"<!DOCTYPE|<!ENTITY", re.IGNORECASE)
_LENGTH = re.compile(r"^\s*([0-9.]+)\s*(px|pt|mm|cm|in)?\s*$", re.IGNORECASE)
_UNIT = {"px": 1.0, None: 1.0, "pt": 96 / 72, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0}


class SvgImportError(ValueError):
    """The file cannot be imported (not an SVG, unsafe, damaged…)."""


@dataclass
class ImportReport:
    """Outcome of importing one file.

    Attributes:
        screen: The new :class:`Dashboard`.
        kind: ``"screen"`` (restored export) or ``"background"`` (plain drawing).
        notes: Information and warnings for the user.
        saved_files: Asset files written (removed again if the import fails).
    """

    screen: Dashboard | None = None
    kind: str = ""
    notes: list = field(default_factory=list)
    saved_files: list = field(default_factory=list)


def _unique_name(name: str) -> str:
    """A screen name not used yet (adds `` (imported)``, `` (imported 2)``…).

    Args:
        name: Wanted name.

    Returns:
        Free name (max 100 characters).
    """
    base = (name or "Imported screen").strip()[:85] or "Imported screen"
    if Dashboard.query.filter_by(name=base).first() is None:
        return base
    candidate, n = f"{base} (imported)", 2
    while Dashboard.query.filter_by(name=candidate).first() is not None:
        candidate, n = f"{base} (imported {n})", n + 1
    return candidate


def _length(value: str | None) -> float | None:
    """Convert an SVG length (``297mm``, ``800``, ``11in``) to pixels.

    Args:
        value: Attribute value.

    Returns:
        Pixels, or ``None`` for missing/relative values (``100%``).
    """
    match = _LENGTH.match(value or "")
    if not match:
        return None
    return float(match.group(1)) * _UNIT[(match.group(2) or "").lower() or None]


def drawing_size(root) -> tuple[int, int]:
    """Canvas size for a drawing: width/height attributes, else the viewBox.

    The result is scaled to fit the allowed canvas range (200–10000).

    Args:
        root: Parsed ``<svg>`` element.

    Returns:
        ``(width, height)`` in canvas units.
    """
    w, h = _length(root.get("width")), _length(root.get("height"))
    if not (w and h):
        parts = re.split(r"[\s,]+", (root.get("viewBox") or "").strip())
        try:
            w, h = float(parts[2]), float(parts[3])
        except (IndexError, ValueError):
            w, h = 1600.0, 900.0
    if w <= 0 or h <= 0:
        w, h = 1600.0, 900.0
    factor = min(1.0, 10000 / max(w, h))
    if min(w, h) * factor < 200:  # enlarge small drawings (keeping the aspect ratio)
        factor = min(200 / min(w, h), 10000 / max(w, h))
    return int(round(max(200, min(10000, w * factor)))), int(round(max(200, min(10000, h * factor))))


def _parse(data: bytes):
    """Decode and parse an SVG file safely.

    Args:
        data: File content.

    Returns:
        ``(text, root element)``.

    Raises:
        SvgImportError: If the file is too large, not UTF-8, has a DOCTYPE or
            entities, is not well-formed XML or not an ``<svg>`` document.
    """
    if not data:
        raise SvgImportError(gettext('The file is empty.'))
    if len(data) > MAX_SVG_BYTES:
        raise SvgImportError(gettext('The file is larger than {0} MB.', MAX_SVG_BYTES // (1024 * 1024)))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SvgImportError(gettext('This is not an SVG file (it must be UTF-8 text).')) from exc
    if _UNSAFE.search(text):
        raise SvgImportError(gettext('SVG files with a DOCTYPE or entity declarations are not accepted.'))
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise SvgImportError(gettext('The SVG file is damaged ({0}).', exc)) from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise SvgImportError(gettext('This is not an SVG drawing.'))
    return text, root


def _metadata(root) -> dict | None:
    """The screen data embedded by *Export SVG*, if any.

    Args:
        root: Parsed ``<svg>``.

    Returns:
        The decoded metadata dict, or ``None``.
    """
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "metadata" and el.get("id") == "pyplchmi-screen" and el.text:
            try:
                meta = json.loads(el.text)
            except ValueError:
                return None
            return meta if isinstance(meta, dict) and meta.get("format") == "pyplchmi-screen" else None
    return None


def _store_asset(raw: bytes, name: str, category: str, user, report: ImportReport) -> Asset:
    """Add an image to the library, reusing an identical existing one.

    Args:
        raw: File content.
        name: Asset name.
        category: Library folder path (invalid paths fall back to ``Imported``).
        user: Importing user.
        report: Report (stored file names are recorded for clean-up).

    Returns:
        The (new or existing) :class:`Asset`.

    Raises:
        AssetError: If the content is not an accepted image.
    """
    try:
        folder = normalize_path(category, default="Imported")
    except FolderError:
        folder = "Imported"
    asset, created = store_asset(raw, name or "image", folder, user)
    if created:
        report.saved_files.append(asset.filename)
    return asset


def _resolve_tag(old_id: str, info: dict, screen: Dashboard, report: ImportReport) -> DataPoint | None:
    """Find (or create) the tag an exported element referred to.

    Args:
        old_id: Tag id in the exporting system.
        info: Exported tag description (label, plc, address, data type…).
        screen: The new screen (found tags are linked to it).
        report: Report for notes.

    Returns:
        The tag, or ``None`` if it cannot be found or created.
    """
    label, plc_name = str(info.get("label") or "")[:100], str(info.get("plc") or "")
    dp = db.session.get(DataPoint, int(old_id)) if str(old_id).isdigit() else None
    from app.tags.naming import normalize  # noqa: PLC0415

    names = [label] + ([normalize(label)] if label and normalize(label) != label else [])
    if dp is not None and (dp.label not in names or (dp.plc and dp.plc.name != plc_name)):
        dp = None
    if dp is None and label:   # the exact label, or the valid name it was created with
        dp = (DataPoint.query.join(PLC, DataPoint.plc_id == PLC.id)
              .filter(DataPoint.label.in_(names), PLC.name == plc_name).order_by(DataPoint.id).first())
    if dp is None:
        plc = PLC.query.filter_by(name=plc_name).first() if plc_name else None
        if plc is None:
            report.notes.append(("warning", gettext("Tag '{0}' ({1}) not found – its bindings were removed.", label, plc_name or 'no PLC')))
            return None
        try:
            address = parse_tag_address("" if plc.is_dummy else str(info.get("address") or ""),
                                        info.get("data_type"), info.get("string_length") or 20, plc.protocol)
        except AddressError as exc:
            report.notes.append(("warning", gettext("Tag '{0}' could not be created ({1}) – its bindings were removed.", label, exc)))
            return None
        from app.tags.naming import problems as name_problems, suggest  # noqa: PLC0415

        new_label = label if label and not name_problems(label) else suggest(label or "Imported_tag")
        if new_label != label:
            report.notes.append(("info", gettext("Tag '{0}' was created as '{1}' (tag names may not contain spaces "
                                                 "or symbols).", label, new_label)))
        dp = DataPoint(plc=plc, label=new_label, unit=str(info.get("unit") or "")[:20],
                       writable=bool(info.get("writable")), min_value=info.get("min"), max_value=info.get("max"),
                       true_text=str(info.get("true_text") or "ON")[:30], false_text=str(info.get("false_text") or "OFF")[:30])
        for attr in ("scale", "offset", "decimals"):
            if isinstance(info.get(attr), (int, float)) and not (attr == "scale" and info[attr] == 0):
                setattr(dp, attr, info[attr])
        if plc.is_dummy:
            from app.plc.dummy import next_slot  # noqa: PLC0415
            address.start = next_slot(plc.id)
            dp.dummy_scope = "local" if info.get("dummy_scope") == "local" else "general"
        dp.set_address(address)
        db.session.add(dp)
        db.session.flush()
        report.notes.append(("info", gettext("Tag '{0}' created on {1} ({2}).", dp.label, plc.name, dp.address)))
    screen.add_tag(dp)
    return dp


def _restore(meta: dict, filename: str, user, report: ImportReport) -> None:
    """Rebuild a screen from exported metadata.

    Args:
        meta: Decoded metadata.
        filename: Uploaded file name (fallback screen name).
        user: Importing user.
        report: Filled with the new screen and notes.
    """
    info = meta.get("screen") if isinstance(meta.get("screen"), dict) else {}
    layout = meta.get("layout") if isinstance(meta.get("layout"), dict) else {}
    name = _unique_name(str(info.get("name") or os.path.splitext(filename)[0]))
    try:
        refresh = min(max(int(info.get("refresh_ms") or 1000), 250), 60000)
    except (TypeError, ValueError):
        refresh = 1000
    screen = Dashboard(name=name, slug=Dashboard.unique_slug(name), kind="image", refresh_ms=refresh,
                       description=str(info.get("description") or "")[:500], created_by=user)
    db.session.add(screen)
    db.session.flush()

    asset_map = {}
    for old_id, a in (meta.get("assets") or {}).items():
        try:
            raw = base64.b64decode(str(a.get("data") or ""), validate=True)
            asset_map[str(old_id)] = _store_asset(raw, str(a.get("name") or "image"), str(a.get("category") or "Imported"),
                                                  user, report).id
        except (binascii.Error, ValueError, AssetError) as exc:
            report.notes.append(("warning", gettext("Image '{0}' skipped: {1}", a.get('name'), exc)))

    tag_map = {}
    for old_id, t in (meta.get("tags") or {}).items():
        if isinstance(t, dict):
            dp = _resolve_tag(str(old_id), t, screen, report)
            if dp is not None:
                tag_map[str(old_id)] = dp.id

    screen_map = {}
    for old_id, target_name in (meta.get("screens") or {}).items():
        target = Dashboard.query.filter(Dashboard.name == str(target_name), Dashboard.id != screen.id).first()
        if target is not None:
            screen_map[str(old_id)] = target.id
        else:
            report.notes.append(("warning", gettext("Linked screen '{0}' does not exist – the link was removed.", target_name)))

    def remap(value, mapping):
        """Translate an exported id (``None`` if unknown)."""
        return mapping.get(str(value)) if value is not None else None

    canvas = dict(layout.get("canvas") or {})
    canvas["backgroundImage"] = remap(canvas.get("backgroundImage"), asset_map)
    canvas["pageBackgroundImage"] = remap(canvas.get("pageBackgroundImage"), asset_map)
    elements = []
    for el in layout.get("elements") or []:
        if not isinstance(el, dict):
            continue
        el = dict(el)
        for prop in ("point", "visPoint"):
            el[prop] = remap(el.get(prop), tag_map)
        el["asset"] = remap(el.get("asset"), asset_map)
        el["link"] = remap(el.get("link"), screen_map)
        elements.append(el)
    try:
        clean = sanitize_layout({"canvas": canvas, "elements": elements}, point_ids=set(tag_map.values()),
                                asset_ids=set(asset_map.values()),
                                dashboard_ids={d.id for d in Dashboard.query.all()})
    except LayoutError as exc:
        raise SvgImportError(gettext('The screen data in the file is invalid: {0}', exc)) from exc
    screen.set_layout(clean)
    report.screen, report.kind = screen, "screen"
    report.notes.insert(0, ("success", f"Screen '{screen.name}' imported: {len(clean['elements'])} element(s), "
                                       f"{len(tag_map)} tag(s), {len(asset_map)} image(s)."))


def _as_background(data: bytes, root, filename: str, user, report: ImportReport) -> None:
    """Create a screen showing a plain SVG drawing as its background.

    Args:
        data: File content.
        root: Parsed ``<svg>``.
        filename: Uploaded file name.
        user: Importing user.
        report: Filled with the new screen and notes.

    Raises:
        SvgImportError: If the drawing is rejected as an image (scripts, event handlers…).
    """
    stem = os.path.splitext(os.path.basename(filename))[0] or "drawing"
    try:
        asset = _store_asset(data, stem, "Imported", user, report)
    except AssetError as exc:
        raise SvgImportError(str(exc)) from exc
    width, height = drawing_size(root)
    name = _unique_name(stem.replace("_", " ").replace("-", " ").strip().capitalize() or "Imported drawing")
    screen = Dashboard(name=name, slug=Dashboard.unique_slug(name), kind="image", created_by=user,
                       description=f"Imported from {os.path.basename(filename)}"[:500])
    db.session.add(screen)
    db.session.flush()
    canvas = {k: spec[1] for k, spec in CANVAS_SPEC.items()}
    canvas.update(width=width, height=height, backgroundImage=asset.id, backgroundFit="contain")
    screen.set_layout(sanitize_layout({"canvas": canvas, "elements": []}, point_ids=(), asset_ids={asset.id},
                                      dashboard_ids=()))
    report.screen, report.kind = screen, "background"
    report.notes.insert(0, ("success", f"Screen '{screen.name}' created with the drawing as background "
                                       f"({width} × {height}) – add tags, symbols and buttons on top in the designer."))


def import_svg(data: bytes, filename: str, user) -> ImportReport:
    """Import one SVG file as a new Image screen (not committed).

    Args:
        data: File content.
        filename: Original file name.
        user: The importing user.

    Returns:
        :class:`ImportReport`.

    Raises:
        SvgImportError: If the file cannot be imported (the caller rolls back
            and calls :func:`discard_files`).
    """
    report = ImportReport()
    try:
        _text, root = _parse(data)
        meta = _metadata(root)
        if meta is not None:
            _restore(meta, filename, user, report)
        else:
            _as_background(data, root, filename, user, report)
    except SvgImportError:
        discard_files(report)
        raise
    return report


def discard_files(report: ImportReport) -> None:
    """Delete asset files written by a failed import.

    Args:
        report: The import's report.
    """
    for filename in report.saved_files:
        delete_file(filename)
    report.saved_files.clear()
