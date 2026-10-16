"""Tests for Image (graphic) dashboards: views, designer, layout and tag APIs."""

import json

import pytest

from app.models import Dashboard, DataPoint


@pytest.fixture()
def image_dash(db, plc, users):
    """An Image dashboard with one writable BOOL tag and a small layout.

    Returns:
        The :class:`Dashboard`.
    """
    from tests.conftest import add_point

    dash = Dashboard(name="Plant", slug="plant", kind="image", is_public=False)
    db.session.add(dash)
    db.session.commit()
    run = add_point(dash, plc, "Fan run", "M5.0", writable=True)
    dash.set_layout({"version": 1, "canvas": {"width": 800, "height": 600, "background": "#1b2129", "grid": 10},
                     "elements": [{"id": "f1", "type": "symbol", "symbol": "fan", "x": 10, "y": 10, "w": 80, "h": 80, "point": run.id}]})
    db.session.commit()
    return dash


def test_create_image_dashboard_opens_designer(client, login_as):
    """Creating an Image dashboard stores an empty layout and opens the designer."""
    login_as("admin")
    resp = client.post("/screens/new", data={"name": "Site map", "kind": "image", "refresh_ms": 1000})
    dash = Dashboard.query.filter_by(slug="site-map").one()
    assert dash.is_image and dash.layout_data["elements"] == []
    assert resp.location.endswith(f"/screens/{dash.id}/designer")


def test_designer_access(client, login_as, image_dash, dashboards):
    """Only dashboard managers can open the designer; grid dashboards redirect."""
    login_as("admin")
    html = client.get(f"/screens/{image_dash.id}/designer").data.decode()
    assert 'id="designer-data"' in html and "hmi-designer.js" in html
    resp = client.get(f"/screens/{dashboards['private'].id}/designer")
    assert resp.status_code == 302
    client.post("/auth/logout")
    login_as("viewer")
    assert client.get(f"/screens/{image_dash.id}/designer").status_code == 403


def test_view_renders_mimic(client, login_as, image_dash):
    """Viewing an Image dashboard renders the SVG runtime with its layout."""
    login_as("viewer")
    html = client.get("/screens/plant").data.decode()
    assert 'id="mimic-svg"' in html and "hmi-mimic.js" in html
    start = html.index('id="mimic-data">') + len('id="mimic-data">')
    data = json.loads(html[start:html.index("</script>", start)])
    assert data["layout"]["elements"][0]["symbol"] == "fan"
    assert data["can_write"] is False  # viewer
    assert "Designer" not in html


def test_layout_save_and_load(client, login_as, image_dash):
    """The designer's layout is validated, stored and returned."""
    login_as("admin")
    pid = image_dash.datapoints[0].id
    layout = {"canvas": {"width": 1000, "height": 700},
              "elements": [{"type": "button", "text": "Start", "point": pid, "action": "toggle"},
                           {"type": "value", "point": 12345},
                           {"type": "bogus"}]}
    resp = client.put(f"/api/dashboards/{image_dash.id}/layout", json=layout)
    assert resp.status_code == 200
    saved = resp.get_json()["layout"]
    assert len(saved["elements"]) == 2 and saved["elements"][1]["point"] is None
    assert client.get(f"/api/dashboards/{image_dash.id}/layout").get_json() == saved


def test_layout_save_errors(client, login_as, image_dash, dashboards):
    """Invalid layouts, grid dashboards and non-managers are rejected."""
    login_as("admin")
    assert client.put(f"/api/dashboards/{image_dash.id}/layout", json=[1, 2]).status_code == 400
    assert client.put(f"/api/dashboards/{dashboards['private'].id}/layout", json={"elements": []}).status_code == 400
    client.post("/auth/logout")
    login_as("viewer")
    assert client.put(f"/api/dashboards/{image_dash.id}/layout", json={"elements": []}).status_code == 403


def test_layout_get_respects_visibility(client, login_as, image_dash):
    """Guests cannot read the layout of a private dashboard."""
    login_as("guest")
    assert client.get(f"/api/dashboards/{image_dash.id}/layout").status_code == 403


def test_create_tag_api(client, login_as, image_dash, plc):
    """The designer can create tags; validation errors are returned as JSON."""
    login_as("admin")
    url = f"/api/dashboards/{image_dash.id}/datapoints"
    ok = client.post(url, json={"label": "Level", "plc_id": plc.id, "address": "DB1.DBD0", "data_type": "REAL",
                                "unit": "%", "writable": False})
    assert ok.status_code == 201
    body = ok.get_json()
    assert body["address"] == "DB1.DBD0" and body["unit"] == "%" and body["writable"] is False
    w = client.post(url, json={"label": "Cmd", "plc_id": plc.id, "address": "M1.0", "data_type": "BOOL", "writable": True})
    assert w.get_json()["writable"] is True and w.get_json()["is_bool"] is True
    bad = client.post(url, json={"label": "Bad", "plc_id": plc.id, "address": "DB1.DBW0", "data_type": "REAL"})
    assert bad.status_code == 400 and "address" in bad.get_json()["errors"]
    assert len(Dashboard.query.get(image_dash.id).datapoints) == 3


def test_create_tag_forbidden_for_viewer(client, login_as, image_dash, plc):
    """Viewers cannot create tags."""
    login_as("viewer")
    resp = client.post(f"/api/dashboards/{image_dash.id}/datapoints", json={"label": "x", "plc_id": plc.id, "address": "M0.0"})
    assert resp.status_code == 403


def test_values_and_write_work_for_image_dashboards(client, login_as, image_dash):
    """Live values and writes use the same API as grid dashboards."""
    login_as("admin")
    pid = image_dash.datapoints[0].id
    assert client.post(f"/api/datapoints/{pid}/write", json={"value": True}).get_json()["value"] is True
    vals = client.get(f"/api/dashboards/{image_dash.id}/values").get_json()["values"]
    assert vals[str(pid)]["value"] is True


def test_go_route(client, login_as, image_dash):
    """/screens/go/<id> redirects to the slug URL."""
    login_as("viewer")
    resp = client.get(f"/screens/go/{image_dash.id}")
    assert resp.status_code == 302 and resp.location.endswith("/screens/plant")
    assert client.get("/screens/go/999").status_code == 404


def test_switch_kind_in_settings(client, login_as, image_dash):
    """The type can be changed in the dashboard settings; the layout is kept."""
    login_as("admin")
    client.post(f"/screens/{image_dash.id}/edit", data={"name": "Plant", "kind": "grid", "refresh_ms": 1000})
    dash = Dashboard.query.get(image_dash.id)
    assert dash.kind == "grid" and dash.layout_data["elements"]
    assert b"hmi-tile" in client.get("/screens/plant").data


def test_broken_layout_json_falls_back(db, image_dash):
    """Corrupt stored JSON yields an empty layout instead of an error."""
    image_dash.layout = "{not json"
    assert image_dash.layout_data["elements"] == []
