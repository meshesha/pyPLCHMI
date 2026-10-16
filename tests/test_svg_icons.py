"""Tests for element visibility, Tabler icons, and SVG import/export of graphic screens."""

import base64
import io
import json

import pytest

from app.dashboards import icons
from app.dashboards.layout import referenced_points, sanitize_layout
from app.dashboards.svg_import import drawing_size, _parse, SvgImportError
from app.models import PLC, Asset, Dashboard, DataPoint
from tests.conftest import add_point

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
needs_icons = pytest.mark.skipif(not icons.available(), reason="pytablericons not installed")


# ---------------------------------------------------------------- visibility
def test_visibility_props_are_validated():
    """vis/visPoint/visOp/visValue are kept, cleaned or reset."""
    raw = {"elements": [
        {"id": "a", "type": "rect", "vis": "tag", "visPoint": 1, "visOp": "gt", "visValue": "50"},
        {"id": "b", "type": "text", "vis": "hidden"},
        {"id": "c", "type": "lamp", "vis": "tag", "visPoint": 99},          # unknown tag -> always
        {"id": "d", "type": "pipe", "points": [[0, 0], [10, 0]], "vis": "bogus", "visOp": "xx"},
    ]}
    out = sanitize_layout(raw, [1], [], [])
    a, b, c, d = out["elements"]
    assert (a["vis"], a["visPoint"], a["visOp"], a["visValue"]) == ("tag", 1, "gt", "50")
    assert b["vis"] == "hidden" and c["vis"] == "always" and c["visPoint"] is None
    assert d["vis"] == "always" and d["visOp"] == "on"
    assert referenced_points(out) == {1}


def test_runtime_view_includes_visibility_css(client, login_as, db, plc):
    """The graphic runtime hides vis-off elements; the designer only dims them."""
    css = client.get("/static/css/hmi.css").get_data(as_text=True)
    assert ".mimic-svg .hmi-vis-off { display: none; }" in css and ".dz-stage .hmi-vis-off" in css


# ---------------------------------------------------------------- icons
@needs_icons
def test_icon_index_search_and_shape():
    """The package's SVGs are indexed, searchable and turned into safe shapes."""
    assert len(icons.index()) > 4000
    found, total = icons.search("home", "outline", limit=5)
    assert total >= 1 and found[0]["name"] == "home"
    shape = icons.shape("home", "outline")
    assert shape["mode"] == "stroke" and shape["viewBox"] == "0 0 24 24"
    assert all(tag in {"path", "circle", "ellipse", "rect", "line", "polyline", "polygon"} for tag, _ in shape["nodes"])
    assert icons.shape("home", "filled")["mode"] == "fill"
    assert icons.shape("../etc/passwd", "outline") is None and icons.shape("home", "bogus") is None
    assert "Buildings" in icons.categories()


@needs_icons
def test_icon_element_validation():
    """Unknown icons fall back to 'help'; a wrong style is corrected when the other exists."""
    out = sanitize_layout({"elements": [
        {"id": "a", "type": "icon", "icon": "home", "iconStyle": "filled", "color": "#ff0000"},
        {"id": "b", "type": "icon", "icon": "no-such-icon-xyz"},
        {"id": "c", "type": "icon", "icon": "../../x"},
        {"id": "d", "type": "icon", "icon": "abacus", "iconStyle": "filled"},
    ]}, [], [], [])
    a, b, c, d = out["elements"]
    assert (a["icon"], a["iconStyle"], a["color"]) == ("home", "filled", "#ff0000")
    assert b["icon"] == "help" and c["icon"] == "help"
    assert d["iconStyle"] == "outline" if not icons.exists("abacus", "filled") else d["iconStyle"] == "filled"


@needs_icons
def test_icon_api(client, login_as, users):
    """The designer's icon search and shape endpoints (screen managers only)."""
    login_as("admin")
    data = client.get("/api/icons?q=fan&style=outline&limit=3").get_json()
    assert data["available"] and len(data["icons"]) == 3 and data["icons"][0]["shape"]["nodes"]
    assert data["total"] >= 3 and "Buildings" in data["categories"]
    assert client.get("/api/icons/outline/home").get_json()["mode"] == "stroke"
    assert client.get("/api/icons/outline/nope-nope").status_code == 404
    client.post("/auth/logout")
    login_as("viewer")
    assert client.get("/api/icons?q=fan").status_code == 403


@needs_icons
def test_runtime_and_designer_carry_icon_data(client, login_as, db, users):
    """Screens send the shapes of the icons they use; the designer gets the icon URLs."""
    screen = Dashboard(name="Icons", slug="icons", kind="image", is_public=True)
    screen.set_layout(sanitize_layout({"elements": [{"id": "i1", "type": "icon", "icon": "home", "x": 10, "y": 10}]}, [], [], []))
    db.session.add(screen)
    db.session.commit()
    login_as("admin")
    html = client.get("/screens/icons").get_data(as_text=True)
    assert '"outline/home"' in html
    html = client.get(f"/screens/{screen.id}/designer").get_data(as_text=True)
    assert '"icons_available": true' in html and "/api/icons/__style__/__name__" in html
    assert 'id="dz-export"' in html


# ---------------------------------------------------------------- SVG import helpers
def test_drawing_size_units_and_limits():
    """Sizes come from width/height (with units) or the viewBox, clamped to the canvas range."""
    _, root = _parse(b'<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm"/>')
    assert drawing_size(root) == (1123, 794)
    _, root = _parse(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40000 20000"/>')
    assert drawing_size(root) == (10000, 5000)
    _, root = _parse(b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="25"/>')
    assert drawing_size(root) == (400, 200)
    _, root = _parse(b'<svg xmlns="http://www.w3.org/2000/svg" width="100%"/>')
    assert drawing_size(root) == (1600, 900)


@pytest.mark.parametrize("data,message", [
    (b"", "empty"),
    (b"\xff\xfe\x00", "UTF-8"),
    (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "y">]><svg/>', "DOCTYPE"),
    (b"<svg><rect></svg>", "damaged"),
    (b"<html></html>", "not an SVG"),
])
def test_parse_rejects_bad_files(data, message):
    """Unsafe or broken files are refused with a clear message."""
    with pytest.raises(SvgImportError, match=message):
        _parse(data)


# ---------------------------------------------------------------- SVG import route
def _upload(client, files):
    """POST files to the import route.

    Args:
        client: Test client.
        files: ``[(bytes, filename)]``.

    Returns:
        Response (redirects followed).
    """
    return client.post("/screens/import", data={"file": [(io.BytesIO(d), n) for d, n in files]},
                       content_type="multipart/form-data", follow_redirects=True)


def _export_svg(meta: dict) -> bytes:
    """A minimal file as produced by the designer's Export SVG.

    Args:
        meta: Metadata dict.

    Returns:
        SVG bytes.
    """
    text = json.dumps(meta).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return ('<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">'
            f'<metadata id="pyplchmi-screen">{text}</metadata><rect width="10" height="10"/></svg>').encode()


@pytest.fixture()
def exported(db, dashboards, plc):
    """Metadata of an exported screen using two tags, an image and a link.

    Returns:
        Dict with ``meta`` and the referenced objects.
    """
    temp = dashboards["points"]["temp"]
    meta = {
        "format": "pyplchmi-screen", "version": 1,
        "screen": {"name": "Boiler room", "description": "Exported", "refresh_ms": 2000},
        "layout": {"canvas": {"width": 800, "height": 600, "background": "#112233", "backgroundImage": 77},
                   "elements": [
                       {"id": "v1", "type": "value", "x": 10, "y": 10, "w": 100, "h": 40, "point": 9001},
                       {"id": "r1", "type": "rect", "x": 0, "y": 0, "w": 50, "h": 50, "vis": "tag", "visPoint": 9002,
                        "visOp": "on", "link": 555},
                       {"id": "l1", "type": "lamp", "x": 0, "y": 0, "w": 20, "h": 20, "point": 9003},
                       {"id": "im", "type": "image", "x": 0, "y": 0, "w": 20, "h": 20, "asset": 77},
                   ]},
        "tags": {
            "9001": {"label": "Temp", "plc": "PLC1", "address": "DB1.DBD0", "data_type": "REAL"},        # existing
            "9002": {"label": "Door open", "plc": "PLC1", "address": "M5.0", "data_type": "BOOL", "writable": True},  # created
            "9003": {"label": "Ghost", "plc": "Nowhere", "address": "M1.0", "data_type": "BOOL"},       # unknown PLC
        },
        "assets": {"77": {"name": "floor plan", "category": "Plans", "mime": "image/png",
                          "data": base64.b64encode(PNG).decode()}},
        "screens": {"555": "Public"},
    }
    return {"meta": meta, "temp": temp, "public": dashboards["public"]}


def test_import_restores_exported_screen(client, login_as, db, exported):
    """Exported files become complete screens with remapped tags, images and links."""
    login_as("admin")
    resp = _upload(client, [(_export_svg(exported["meta"]), "boiler.svg")])
    html = resp.get_data(as_text=True)
    assert "Screen &#39;Boiler room&#39; imported: 4 element(s), 2 tag(s), 1 image(s)" in html
    assert "Tag &#39;Ghost&#39; (Nowhere) not found" in html and "Tag &#39;Door open&#39; created on PLC1" in html
    screen = Dashboard.query.filter_by(name="Boiler room").one()
    assert resp.request.path == f"/screens/{screen.id}/designer"
    assert screen.kind == "image" and screen.refresh_ms == 2000 and not screen.is_public
    layout = screen.layout_data
    els = {e["id"]: e for e in layout["elements"]}
    door = DataPoint.query.filter_by(label="Door open").one()
    assert els["v1"]["point"] == exported["temp"].id
    assert els["r1"]["visPoint"] == door.id and els["r1"]["vis"] == "tag" and els["r1"]["link"] == exported["public"].id
    assert els["l1"]["point"] is None
    asset = Asset.query.filter_by(name="floor plan").one()
    assert els["im"]["asset"] == asset.id and layout["canvas"]["backgroundImage"] == asset.id
    assert {dp.label for dp in screen.datapoints} == {"Temp", "Door open"}
    assert door.writable and door.address == "M5.0"

    # Importing the same file again: new screen name, the identical image is reused.
    html = _upload(client, [(_export_svg(exported["meta"]), "boiler.svg")]).get_data(as_text=True)
    assert "Boiler room (imported)" in html
    assert Asset.query.filter_by(name="floor plan").count() == 1
    assert DataPoint.query.filter_by(label="Door open").count() == 1


def test_import_plain_drawing_as_background(client, login_as, db):
    """Other SVG drawings become the background of a new screen."""
    login_as("admin")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="500"><rect width="10" height="10"/></svg>'
    html = _upload(client, [(svg, "floor_1.svg")]).get_data(as_text=True)
    assert "created with the drawing as background (1000 × 500)" in html
    screen = Dashboard.query.filter_by(name="Floor 1").one()
    canvas = screen.layout_data["canvas"]
    assert (canvas["width"], canvas["height"]) == (1000, 500)
    assert db.session.get(Asset, canvas["backgroundImage"]).mimetype == "image/svg+xml"


def test_import_errors_and_several_files(client, login_as, db):
    """Bad files are reported, good files in the same upload are still imported."""
    login_as("admin")
    good = b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"/>'
    evil = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    html = _upload(client, [(good, "a.svg"), (evil, "evil.svg"), (b"hello", "b.svg")]).get_data(as_text=True)
    assert "evil.svg: SVG files containing scripts" in html
    assert "b.svg: The SVG file is damaged" in html or "b.svg: This is not an SVG" in html
    assert Dashboard.query.filter_by(name="A").count() == 1
    assert Dashboard.query.filter(Dashboard.name.in_(["Evil", "B"])).count() == 0
    resp = client.post("/screens/import", data={}, follow_redirects=True)
    assert "Choose one or more SVG files" in resp.get_data(as_text=True)


def test_import_requires_screen_manager(client, login_as, users):
    """Viewers cannot import screens; the button is shown to managers only."""
    login_as("viewer")
    assert client.post("/screens/import").status_code == 403
    assert 'id="svg-import-btn"' not in client.get("/screens/").get_data(as_text=True)
    client.post("/auth/logout")
    login_as("admin")
    assert 'id="svg-import-btn"' in client.get("/screens/").get_data(as_text=True)


def test_import_creates_dummy_tags(client, login_as, db, users):
    """Tags of dummy PLCs are recreated without an address."""
    dummy = PLC(name="Internal", ip_address="", protocol="dummy")
    db.session.add(dummy)
    db.session.commit()
    login_as("admin")
    meta = {"format": "pyplchmi-screen", "screen": {"name": "D"},
            "layout": {"elements": [{"id": "x", "type": "lamp", "point": 1}]},
            "tags": {"1": {"label": "Mode", "plc": "Internal", "address": "D4", "data_type": "BOOL"}}}
    _upload(client, [(_export_svg(meta), "d.svg")])
    tag = DataPoint.query.filter_by(label="Mode").one()
    assert tag.plc_id == dummy.id and tag.address == "D1"
