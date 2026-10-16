"""Tabler icons for graphic screens, read from the ``pytablericons`` package.

``pytablericons`` ships the Tabler icon set as SVG files (``icons/outline``
and ``icons/filled``, about 5,200 icons).  The package's own Python API
renders PNG images through pygame; the HMI only needs the vector data, so
this module reads the SVG files directly (without importing the package) and
turns each icon into a small, safe list of shapes:

    {"viewBox": "0 0 24 24", "mode": "stroke" | "fill",
     "nodes": [["path", {"d": "M5 12l-2 0l9 -9l9 9l-2 0"}], ...]}

Only basic shape elements and geometry attributes are kept, and the browser
builds the nodes with ``setAttribute`` – no markup from files ever reaches the
page.  The index (name, style, category, search tags) is built once and cached.

If ``pytablericons`` is not installed, :func:`available` is ``False`` and the
designer shows a hint instead of the icon palette.
"""

from __future__ import annotations

import functools
import importlib.util
import os
import re
from xml.etree import ElementTree

#: Icon styles (sub-folders of the package's ``icons`` folder).
STYLES = ("outline", "filled")

#: Valid icon file names.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")

_SHAPES = {"path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}
_ATTRS = {"d", "cx", "cy", "r", "rx", "ry", "x", "y", "width", "height", "x1", "y1", "x2", "y2", "points",
          "fill", "stroke", "stroke-width", "transform"}
_HEADER_RE = re.compile(r"<!--(.*?)-->", re.S)


@functools.lru_cache(maxsize=1)
def icon_dir() -> str | None:
    """Folder holding the icon SVG files.

    Returns:
        ``.../pytablericons/icons`` or ``None`` if the package is missing.
    """
    try:
        spec = importlib.util.find_spec("pytablericons")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    path = os.path.join(list(spec.submodule_search_locations)[0], "icons")
    return path if os.path.isdir(path) else None


def available() -> bool:
    """Tell whether the icon set is installed.

    Returns:
        ``True`` if ``pytablericons`` and its SVG files are present.
    """
    return icon_dir() is not None


def _header(text: str) -> tuple[str, str]:
    """Category and search tags from the comment at the top of a Tabler SVG.

    Args:
        text: File content.

    Returns:
        ``(category, tags)`` (empty strings when missing).
    """
    match = _HEADER_RE.search(text[:600])
    if not match:
        return "", ""
    category, tags = "", ""
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "category":
            category = value.strip()
        elif key.strip() == "tags":
            tags = value.strip().strip("[]").replace(",", " ").replace('"', "")
    return category, tags


@functools.lru_cache(maxsize=1)
def index() -> tuple:
    """All icons with their search data.

    Returns:
        Tuple of dicts ``{"name", "style", "category", "search"}`` sorted by name
        (outline first); empty if the package is missing.
    """
    base = icon_dir()
    if base is None:
        return ()
    items = []
    for style in STYLES:
        folder = os.path.join(base, style)
        if not os.path.isdir(folder):
            continue
        for filename in sorted(os.listdir(folder)):
            if not filename.endswith(".svg"):
                continue
            name = filename[:-4]
            if not NAME_RE.match(name):
                continue
            with open(os.path.join(folder, filename), encoding="utf-8") as fh:
                category, tags = _header(fh.read(600))
            items.append({"name": name, "style": style, "category": category,
                          "search": f"{name.replace('-', ' ')} {category} {tags}".lower()})
    return tuple(items)


@functools.lru_cache(maxsize=1)
def _names() -> dict:
    """Set of icon names per style (for validation).

    Returns:
        ``{"outline": set, "filled": set}``.
    """
    names = {style: set() for style in STYLES}
    for item in index():
        names[item["style"]].add(item["name"])
    return names


def exists(name: str, style: str) -> bool:
    """Tell whether an icon exists.

    Args:
        name: Icon name, e.g. ``home``.
        style: ``outline`` or ``filled``.

    Returns:
        ``True`` if the icon file exists.
    """
    return style in STYLES and bool(NAME_RE.match(name or "")) and name in _names().get(style, set())


def _rank(name: str, words: list[str]) -> int:
    """Relevance of an icon name for search words (lower is better).

    Args:
        name: Icon name.
        words: Lower-case search words.

    Returns:
        0 exact name, 1 a name part equals a word, 2 name starts with the
        first word, 3 a name part starts with a word, 4 the name contains a
        word, 5 only category/tags match.
    """
    parts = name.split("-")
    joined = " ".join(words)
    if name == "-".join(words) or name == joined:
        return 0
    if any(w in parts for w in words):
        return 1
    if name.startswith(words[0]):
        return 2
    if any(p.startswith(w) for p in parts for w in words):
        return 3
    if any(w in name for w in words):
        return 4
    return 5


def search(query: str = "", style: str | None = None, category: str | None = None, limit: int = 60) -> tuple[list, int]:
    """Find icons by name, category or Tabler search tags.

    Every word of ``query`` must match; names that start with the query come first.

    Args:
        query: Search words.
        style: Restrict to ``outline`` or ``filled``.
        category: Restrict to a category.
        limit: Maximum number of results.

    Returns:
        ``(results, total)`` where results are ``{"name", "style", "category"}``.
    """
    words = [w for w in (query or "").lower().split() if w]
    hits = []
    for item in index():
        if style and item["style"] != style:
            continue
        if category and item["category"] != category:
            continue
        if all(w in item["search"] for w in words):
            hits.append(item)
    if words:
        hits.sort(key=lambda i: (_rank(i["name"], words), i["name"], i["style"]))
    return [{"name": i["name"], "style": i["style"], "category": i["category"]} for i in hits[:limit]], len(hits)


def categories() -> list[str]:
    """Sorted icon categories.

    Returns:
        Category names.
    """
    return sorted({i["category"] for i in index() if i["category"]})


@functools.lru_cache(maxsize=2048)
def shape(name: str, style: str) -> dict | None:
    """The drawable shapes of one icon.

    Args:
        name: Icon name.
        style: ``outline`` or ``filled``.

    Returns:
        ``{"viewBox", "mode", "nodes"}`` or ``None`` if unknown.
    """
    if not exists(name, style):
        return None
    path = os.path.join(icon_dir(), style, name + ".svg")
    try:
        with open(path, encoding="utf-8") as fh:
            root = ElementTree.fromstring(fh.read())
    except (OSError, ElementTree.ParseError):
        return None
    nodes = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in _SHAPES:
            continue
        attrs = {k: v for k, v in el.attrib.items() if k in _ATTRS and len(v) < 4000}
        # Tabler draws an invisible bounding box (stroke="none") in some icons – skip it.
        if attrs.get("stroke") == "none" and attrs.get("fill") in (None, "none"):
            continue
        nodes.append([tag, attrs])
    mode = "fill" if style == "filled" or root.get("fill") not in (None, "none") else "stroke"
    return {"viewBox": root.get("viewBox", "0 0 24 24"), "mode": mode, "nodes": nodes}


def key(name: str, style: str) -> str:
    """Cache key of an icon used by the browser (``outline/home``).

    Args:
        name: Icon name.
        style: Icon style.

    Returns:
        ``"<style>/<name>"``.
    """
    return f"{style}/{name}"


def shapes_for_layout(layout: dict) -> dict:
    """Shapes of every icon used in a layout (sent with the screen).

    Args:
        layout: Layout dict.

    Returns:
        ``{"outline/home": shape, ...}``.
    """
    result = {}
    for el in layout.get("elements", []):
        if el.get("type") == "icon" and el.get("icon"):
            data = shape(el["icon"], el.get("iconStyle", "outline"))
            if data:
                result[key(el["icon"], el.get("iconStyle", "outline"))] = data
    return result
