"""Schema and server-side sanitising of graphic (Image) dashboard layouts.

A layout is the JSON document produced by the SVG designer
(``static/js/hmi-designer.js``)::

    {
      "version": 1,
      "canvas": {"width": 1600, "height": 900, "background": "#1b2129",
                 "backgroundImage": null, "backgroundFit": "contain", "grid": 10},
      "elements": [
        {"id": "e1", "type": "rect", "x": 40, "y": 40, "w": 400, "h": 300,
         "label": "Room 101", "fill": "#243040", ...},
        {"id": "e2", "type": "symbol", "symbol": "fan", "point": 12, ...},
        {"id": "e3", "type": "pipe", "points": [[100, 100], [300, 100]], ...}
      ]
    }

Every element may also carry a *visibility* rule (:data:`VIS_MODES`):
``vis`` = ``always`` (default), ``hidden`` (never shown at run time) or
``tag`` – shown while the tag ``visPoint`` satisfies ``visOp``/``visValue``
(e.g. ``on``, ``off``, ``eq 3``, ``gt 50``).  Several elements bound to the
same tag appear and disappear together.

Elements are drawn in array order (later = on top).  Every property is
validated against :data:`ELEMENT_SPECS`: unknown properties are dropped,
numbers are clamped, colours must be hex (or ``none``/``transparent``),
strings are length-limited, and references to data points, assets and other
dashboards must exist.  The browser renders text with ``textContent`` only, so
user strings can never become markup.
"""

import re
import uuid
from app.i18n.catalog import gettext

#: Built-in SVG symbols (keys must match ``static/js/hmi-symbols.js``).
SYMBOL_KEYS = (
    "fan", "pump", "motor", "generator", "valve", "tank", "boiler", "heater", "ac_unit", "light",
    "thermometer", "alarm", "breaker", "transformer", "solar_panel", "battery", "door", "window",
    "stairs", "arrow",
)

#: Button actions understood by the runtime (``static/js/hmi-mimic.js``).
BUTTON_ACTIONS = ("toggle", "set", "input", "momentary", "link", "faceplate")

#: Visibility modes of an element.
VIS_MODES = ("always", "hidden", "tag")
#: Visibility conditions on the ``visPoint`` tag (``on``/``off`` need no value).
VIS_OPS = ("on", "off", "eq", "ne", "gt", "ge", "lt", "le")

MAX_ELEMENTS = 2000
MAX_POINTS = 500
COORD_MIN, COORD_MAX = -20000.0, 20000.0

_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_ICON_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


class LayoutError(ValueError):
    """Raised when a submitted layout is structurally invalid."""


# ---------------------------------------------------------------------------
# Property spec helpers.  Each spec entry is ``(kind, default, *options)``.
# ---------------------------------------------------------------------------
def _num(lo, hi, default):
    """Spec for a number clamped to ``[lo, hi]``.

    Args:
        lo: Minimum.
        hi: Maximum.
        default: Value used when missing or invalid.

    Returns:
        A spec tuple.
    """
    return ("num", default, lo, hi)


def _opt_num(default=None):
    """Spec for an optional number (``None`` allowed).

    Args:
        default: Value used when missing.

    Returns:
        A spec tuple.
    """
    return ("optnum", default)


def _str(maxlen, default=""):
    """Spec for a string truncated to ``maxlen`` characters.

    Args:
        maxlen: Maximum length.
        default: Value used when missing.

    Returns:
        A spec tuple.
    """
    return ("str", default, maxlen)


def _color(default):
    """Spec for a colour.

    Args:
        default: Value used when missing or invalid.

    Returns:
        A spec tuple.
    """
    return ("color", default)


def _bool(default=False):
    """Spec for a boolean.

    Args:
        default: Value used when missing.

    Returns:
        A spec tuple.
    """
    return ("bool", default)


def _choice(choices, default):
    """Spec for one value out of a fixed set.

    Args:
        choices: Allowed values.
        default: Value used when missing or not allowed.

    Returns:
        A spec tuple.
    """
    return ("choice", default, tuple(choices))


def _ref(kind):
    """Spec for a reference to another object by id.

    Args:
        kind: ``"point"``, ``"asset"``, ``"dashboard"`` or ``"faceplate"`` (device id).

    Returns:
        A spec tuple.
    """
    return ("ref", None, kind)


_COMMON = {
    "x": _num(COORD_MIN, COORD_MAX, 0.0),
    "y": _num(COORD_MIN, COORD_MAX, 0.0),
    "w": _num(1, 20000, 100.0),
    "h": _num(1, 20000, 100.0),
    "rot": _num(-360, 360, 0.0),
    "opacity": _num(0, 1, 1.0),
    "locked": _bool(),
    "name": _str(100),
    "link": _ref("dashboard"),
    # A device (faceplate instance) whose pop-up window opens on click (see app/faceplates).
    "faceplate": _ref("faceplate"),
    "vis": _choice(VIS_MODES, "always"),
    "visPoint": _ref("point"),
    "visOp": _choice(VIS_OPS, "on"),
    "visValue": _str(100),
}

_LABEL = {
    "label": _str(200),
    "labelColor": _color("#e6edf3"),
    "labelSize": _num(4, 400, 16),
    "labelPos": _choice(("center", "top", "bottom", "none"), "center"),
}

_STATE = {
    "point": _ref("point"),
    "onColor": _color("#2ecc71"),
    "offColor": _color("#5b6b7d"),
}

#: Allowed properties (and their specs) for every element type.
ELEMENT_SPECS: dict[str, dict] = {
    "rect": {**_COMMON, **_LABEL, "fill": _color("#243040"), "stroke": _color("#8b98a8"),
             "strokeWidth": _num(0, 100, 2), "rx": _num(0, 1000, 0), "dash": _bool(),
             "point": _ref("point"), "onColor": _color("#c0392b"), "offColor": _color("#243040")},
    "ellipse": {**_COMMON, **_LABEL, "fill": _color("#243040"), "stroke": _color("#8b98a8"),
                "strokeWidth": _num(0, 100, 2), "dash": _bool(),
                "point": _ref("point"), "onColor": _color("#c0392b"), "offColor": _color("#243040")},
    "line": {**_COMMON, "points": ("points", None), "stroke": _color("#cfd8e3"),
             "strokeWidth": _num(0.5, 200, 6), "dash": _bool(), **_STATE},
    "pipe": {**_COMMON, "points": ("points", None), "stroke": _color("#7f8c8d"),
             "strokeWidth": _num(2, 200, 14), "flowColor": _color("#3498db"), "point": _ref("point")},
    "text": {**_COMMON, "text": _str(2000, "Text"), "color": _color("#e6edf3"), "size": _num(4, 400, 18),
             "bold": _bool(), "italic": _bool(), "align": _choice(("start", "middle", "end"), "start"),
             "font": _choice(("sans", "mono", "serif"), "sans"), "bg": _color("none")},
    "image": {**_COMMON, "asset": _ref("asset"), "fit": _choice(("contain", "stretch", "cover"), "contain")},
    "symbol": {**_COMMON, **_LABEL, "symbol": _choice(SYMBOL_KEYS, "fan"), "color": _color("#cfd8e3"),
               "min": _num(-1e12, 1e12, 0), "max": _num(-1e12, 1e12, 100), **_STATE},
    "value": {**_COMMON, "point": _ref("point"), "prefix": _str(100), "color": _color("#e6edf3"),
              "size": _num(4, 400, 22), "bold": _bool(True), "bg": _color("#0d1015"),
              "border": _color("#2b3440"), "align": _choice(("start", "middle", "end"), "middle"),
              "lo": _opt_num(), "hi": _opt_num(), "alarmColor": _color("#e74c3c")},
    "lamp": {**_STATE, **_COMMON, "blink": _bool(), "stroke": _color("#0d1015")},
    "button": {**_COMMON, "text": _str(100, "Button"), "point": _ref("point"),
               "action": _choice(BUTTON_ACTIONS, "toggle"), "value": _str(100, "1"), "confirm": _bool(True),
               "color": _color("#2fa4e7"), "textColor": _color("#ffffff"), "size": _num(4, 200, 16)},
    "bar": {**_COMMON, "point": _ref("point"), "min": _num(-1e12, 1e12, 0), "max": _num(-1e12, 1e12, 100),
            "color": _color("#2fa4e7"), "bg": _color("#0d1015"), "stroke": _color("#2b3440"),
            "orientation": _choice(("vertical", "horizontal"), "vertical")},
    # Tabler icon (see app/dashboards/icons.py); colour switches with the optional tag.
    "icon": {**_COMMON, **_LABEL, "labelPos": _choice(("center", "top", "bottom", "none"), "bottom"),
             "icon": ("icon", "help"), "iconStyle": _choice(("outline", "filled"), "outline"),
             "color": _color("#cfd8e3"), "strokeWidth": _num(0.25, 4, 2), "blink": _bool(),
             "point": _ref("point"), "onColor": _color("#2ecc71"), "offColor": _color("#cfd8e3")},
}

CANVAS_SPEC = {
    "width": _num(200, 10000, 1600),
    "height": _num(200, 10000, 900),
    "background": _color("#1b2129"),
    "backgroundImage": _ref("asset"),
    "backgroundFit": _choice(("contain", "stretch", "cover"), "contain"),
    "grid": _num(1, 200, 10),
    # The page around the drawing (the whole window of the screen), so the drawing can blend in.
    "pageBackground": _color("none"),
    "pageBackgroundImage": _ref("asset"),
    "pageBackgroundFit": _choice(("cover", "contain", "stretch", "repeat", "center"), "cover"),
}

#: CSS for the page background fit options.
PAGE_FIT_CSS = {
    "cover": "background-size: cover; background-position: center; background-repeat: no-repeat;",
    "contain": "background-size: contain; background-position: center; background-repeat: no-repeat;",
    "stretch": "background-size: 100% 100%; background-repeat: no-repeat;",
    "repeat": "background-repeat: repeat;",
    "center": "background-position: center; background-repeat: no-repeat;",
}


def page_style(layout: dict, asset_url) -> str:
    """Inline CSS for the page around an Image screen (colour and/or image).

    Args:
        layout: The screen layout.
        asset_url: ``function(asset_id) -> URL`` of a library image.

    Returns:
        CSS declarations (empty when the page keeps the theme's background).
    """
    canvas = layout.get("canvas") or {}
    parts = []
    color = canvas.get("pageBackground")
    if color and color not in ("none", "transparent") and _COLOR_RE.match(str(color)):
        parts.append(f"background-color: {color};")
    image = canvas.get("pageBackgroundImage")
    if image:
        parts.append(f"background-image: url('{asset_url(image)}');")
        parts.append(PAGE_FIT_CSS.get(canvas.get("pageBackgroundFit"), PAGE_FIT_CSS["cover"]))
    return " ".join(parts)


def empty_layout() -> dict:
    """Return the layout used for a brand-new Image dashboard.

    Returns:
        A dict with default canvas settings and no elements.
    """
    return {"version": 1, "canvas": {k: spec[1] for k, spec in CANVAS_SPEC.items()}, "elements": []}


def _clean_value(spec: tuple, value, refs: dict):
    """Validate one property value against its spec.

    Args:
        spec: Spec tuple ``(kind, default, *options)``.
        value: Raw value from the client.
        refs: Dict of valid id sets: ``{"point": set, "asset": set, "dashboard": set}``.

    Returns:
        The cleaned value (or the spec default if ``value`` is unusable).
    """
    kind, default = spec[0], spec[1]
    if value is None:
        return default
    if kind == "num":
        lo, hi = spec[2], spec[3]
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
            return default
        number = min(max(number, lo), hi)
        return round(number, 3)
    if kind == "optnum":
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return None if number != number else round(number, 6)
    if kind == "str":
        return str(value)[: spec[2]]
    if kind == "color":
        text = str(value).strip()
        if text in ("none", "transparent") or _COLOR_RE.match(text):
            return text
        return default
    if kind == "bool":
        return bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "yes", "on")
    if kind == "choice":
        return value if value in spec[2] else default
    if kind == "ref":
        try:
            ident = int(value)
        except (TypeError, ValueError):
            return None
        return ident if ident in refs.get(spec[2], set()) else None
    if kind == "points":
        return _clean_points(value)
    if kind == "icon":
        name = str(value).strip().lower()[:80]
        return name if _ICON_RE.match(name) else default
    raise LayoutError(gettext('Unknown spec kind {0}', kind))  # pragma: no cover


def _clean_points(value) -> list | None:
    """Validate a polyline point list ``[[x, y], ...]``.

    Args:
        value: Raw list from the client.

    Returns:
        A list of 2..MAX_POINTS ``[x, y]`` pairs, or ``None`` if invalid.
    """
    if not isinstance(value, list) or not 2 <= len(value) <= MAX_POINTS:
        return None
    cleaned = []
    for pt in value:
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            return None
        try:
            x, y = float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            return None
        if x != x or y != y:
            return None
        cleaned.append([round(min(max(x, COORD_MIN), COORD_MAX), 2), round(min(max(y, COORD_MIN), COORD_MAX), 2)])
    return cleaned


def _bbox(points: list) -> tuple:
    """Bounding box of a point list.

    Args:
        points: ``[[x, y], ...]``.

    Returns:
        ``(x, y, w, h)`` with ``w``/``h`` at least 1.
    """
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(max(xs) - min(xs), 1), max(max(ys) - min(ys), 1)


def sanitize_element(raw: dict, refs: dict) -> dict | None:
    """Validate one element.

    Args:
        raw: Element dict from the client.
        refs: Valid reference ids (see :func:`_clean_value`).

    Returns:
        The cleaned element, or ``None`` if it must be dropped (unknown type,
        polyline without valid points).
    """
    if not isinstance(raw, dict):
        return None
    etype = raw.get("type")
    spec = ELEMENT_SPECS.get(etype)
    if spec is None:
        return None
    el = {"id": raw.get("id") if isinstance(raw.get("id"), str) and _ID_RE.match(raw.get("id")) else None,
          "type": etype}
    for prop, prop_spec in spec.items():
        el[prop] = _clean_value(prop_spec, raw.get(prop), refs)
    if etype in ("line", "pipe"):
        if el["points"] is None:
            return None
        el["x"], el["y"], el["w"], el["h"] = _bbox(el["points"])
    if etype == "icon":
        from app.dashboards import icons  # noqa: PLC0415

        if icons.available() and not icons.exists(el["icon"], el["iconStyle"]):
            other = "filled" if el["iconStyle"] == "outline" else "outline"
            if icons.exists(el["icon"], other):
                el["iconStyle"] = other
            else:
                el["icon"] = "help"
    if el["vis"] == "tag" and el["visPoint"] is None:
        el["vis"] = "always"  # the tag was removed: show the element instead of hiding it for ever
    return el


def sanitize_layout(data, point_ids, asset_ids, dashboard_ids, faceplate_ids=()) -> dict:
    """Validate a complete layout document from the designer.

    Args:
        data: Parsed JSON from the request.
        point_ids: Ids of the dashboard's own data points.
        asset_ids: Ids of existing image assets.
        dashboard_ids: Ids of dashboards that may be linked to.
        faceplate_ids: Ids of devices (faceplate instances) that may be opened.

    Returns:
        A cleaned layout safe to store and render.

    Raises:
        LayoutError: If the document is not an object, has no element list
            or has too many elements.
    """
    if not isinstance(data, dict):
        raise LayoutError(gettext('Layout must be a JSON object'))
    elements = data.get("elements", [])
    if not isinstance(elements, list):
        raise LayoutError(gettext("'elements' must be a list"))
    if len(elements) > MAX_ELEMENTS:
        raise LayoutError(gettext('Too many elements (max {0})', MAX_ELEMENTS))
    refs = {"point": set(point_ids), "asset": set(asset_ids), "dashboard": set(dashboard_ids),
            "faceplate": set(faceplate_ids)}

    raw_canvas = data.get("canvas") if isinstance(data.get("canvas"), dict) else {}
    canvas = {k: _clean_value(spec, raw_canvas.get(k), refs) for k, spec in CANVAS_SPEC.items()}

    cleaned, seen = [], set()
    for raw in elements:
        el = sanitize_element(raw, refs)
        if el is None:
            continue
        if el["id"] is None or el["id"] in seen:
            el["id"] = "e" + uuid.uuid4().hex[:10]
        seen.add(el["id"])
        cleaned.append(el)
    return {"version": 1, "canvas": canvas, "elements": cleaned}


def referenced_assets(layout: dict) -> set:
    """Collect every asset id used by a layout (images and background).

    Args:
        layout: A layout dict.

    Returns:
        Set of asset ids.
    """
    ids = set()
    for key in ("backgroundImage", "pageBackgroundImage"):
        bg = (layout.get("canvas") or {}).get(key)
        if bg:
            ids.add(bg)
    for el in layout.get("elements", []):
        if el.get("type") == "image" and el.get("asset"):
            ids.add(el["asset"])
    return ids


def referenced_points(layout: dict) -> set:
    """Collect every data point id used by a layout (values and visibility tags).

    Args:
        layout: A layout dict.

    Returns:
        Set of data point ids.
    """
    ids = set()
    for el in layout.get("elements", []):
        for prop in ("point", "visPoint"):
            if el.get(prop):
                ids.add(el[prop])
    return ids
