"""Tests for the graphic layout schema/sanitiser (app/dashboards/layout.py)."""

import os
import re

import pytest

from app.dashboards.layout import (
    MAX_ELEMENTS, SYMBOL_KEYS, LayoutError, empty_layout, referenced_assets, referenced_points, sanitize_layout,
)

REFS = {"point_ids": [1, 2], "asset_ids": [7], "dashboard_ids": [3]}


def clean(elements, canvas=None):
    """Sanitise a layout built from ``elements`` with the default references.

    Args:
        elements: Element list.
        canvas: Optional canvas dict.

    Returns:
        The sanitised layout.
    """
    return sanitize_layout({"canvas": canvas or {}, "elements": elements}, **REFS)


def test_empty_layout_defaults():
    """New layouts have a default canvas and no elements."""
    lay = empty_layout()
    assert lay["canvas"]["width"] == 1600 and lay["elements"] == []


def test_valid_rect_kept():
    """Known properties survive, unknown ones are dropped."""
    out = clean([{"id": "r1", "type": "rect", "x": 10, "y": 20, "w": 100, "h": 50, "label": "Room 1",
                  "fill": "#112233", "evil": "<script>", "point": 1}])
    el = out["elements"][0]
    assert el["id"] == "r1" and el["label"] == "Room 1" and el["fill"] == "#112233" and el["point"] == 1
    assert "evil" not in el


def test_unknown_type_dropped():
    """Elements with unknown types are removed."""
    assert clean([{"type": "iframe"}, {"type": "text"}])["elements"][0]["type"] == "text"


@pytest.mark.parametrize("color,expected", [("#abc", "#abc"), ("#AABBCC", "#AABBCC"), ("none", "none"),
                                            ("red;background:url(x)", "#243040"), ("javascript:1", "#243040")])
def test_colors(color, expected):
    """Only hex colours and none/transparent are accepted."""
    assert clean([{"type": "rect", "fill": color}])["elements"][0]["fill"] == expected


def test_numbers_clamped_and_nan():
    """Numbers are clamped; NaN/garbage fall back to defaults."""
    el = clean([{"type": "rect", "x": 1e9, "w": -5, "opacity": "abc", "rot": float("nan")}])["elements"][0]
    assert el["x"] == 20000 and el["w"] == 1 and el["opacity"] == 1.0 and el["rot"] == 0.0


def test_references_validated():
    """Unknown point/asset/dashboard ids become None."""
    out = clean([
        {"type": "value", "point": 99},
        {"type": "image", "asset": 7},
        {"type": "image", "asset": 8},
        {"type": "rect", "link": 3},
        {"type": "rect", "link": 4},
    ])
    els = out["elements"]
    assert els[0]["point"] is None and els[1]["asset"] == 7 and els[2]["asset"] is None
    assert els[3]["link"] == 3 and els[4]["link"] is None


def test_polyline_points_and_bbox():
    """Pipes need ≥2 valid points; the bounding box is computed."""
    out = clean([
        {"type": "pipe", "points": [[10, 10], [110, 10], [110, 60]]},
        {"type": "pipe", "points": [[1, 1]]},
        {"type": "line", "points": "nope"},
        {"type": "line", "points": [[0, 0], ["a", 1]]},
    ])
    assert len(out["elements"]) == 1
    pipe = out["elements"][0]
    assert (pipe["x"], pipe["y"], pipe["w"], pipe["h"]) == (10, 10, 100, 50)


def test_ids_generated_and_unique():
    """Missing, invalid and duplicate ids are replaced."""
    out = clean([{"type": "text", "id": "a"}, {"type": "text", "id": "a"}, {"type": "text", "id": "bad id!"}, {"type": "text"}])
    ids = [e["id"] for e in out["elements"]]
    assert ids[0] == "a" and len(set(ids)) == 4


def test_choices_and_strings():
    """Choice fields fall back to defaults; strings are truncated."""
    el = clean([{"type": "button", "action": "format_disk", "text": "x" * 500}])["elements"][0]
    assert el["action"] == "toggle" and len(el["text"]) == 100
    sym = clean([{"type": "symbol", "symbol": "unicorn"}])["elements"][0]
    assert sym["symbol"] == "fan"


def test_optional_numbers():
    """Alarm limits accept numbers or null."""
    el = clean([{"type": "value", "lo": "5", "hi": None}])["elements"][0]
    assert el["lo"] == 5.0 and el["hi"] is None


def test_canvas_cleaned():
    """Canvas settings are validated."""
    out = clean([], canvas={"width": 50, "height": 99999, "background": "bad", "backgroundImage": 7, "grid": 0})
    c = out["canvas"]
    assert (c["width"], c["height"], c["background"], c["backgroundImage"], c["grid"]) == (200, 10000, "#1b2129", 7, 1)


@pytest.mark.parametrize("bad", [None, [], "x", {"elements": "no"}])
def test_structure_errors(bad):
    """Non-object layouts and non-list elements raise LayoutError."""
    with pytest.raises(LayoutError):
        sanitize_layout(bad, **REFS)


def test_too_many_elements():
    """The element count is limited."""
    with pytest.raises(LayoutError):
        clean([{"type": "text"}] * (MAX_ELEMENTS + 1))


def test_referenced_helpers():
    """Asset and point references are collected."""
    lay = clean([{"type": "image", "asset": 7}, {"type": "value", "point": 2}], canvas={"backgroundImage": 7})
    assert referenced_assets(lay) == {7}
    assert referenced_points(lay) == {2}


def test_symbol_keys_match_javascript():
    """Python SYMBOL_KEYS and the JS symbol library define the same symbols."""
    path = os.path.join(os.path.dirname(__file__), "..", "app", "static", "js", "hmi-symbols.js")
    with open(path, encoding="utf-8") as fh:
        js = fh.read()
    js_keys = set(re.findall(r"^\s{4}(\w+): \{", js, re.MULTILINE))
    assert js_keys == set(SYMBOL_KEYS)
