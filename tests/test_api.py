"""Tests for the JSON API (live values, writes, test reads)."""

from app.models import Perm, Role


def test_values_requires_login(client, dashboards):
    """Anonymous calls get 401 JSON."""
    resp = client.get(f"/api/dashboards/{dashboards['private'].id}/values")
    assert resp.status_code == 401 and resp.is_json


def test_values(client, login_as, dashboards):
    """All points of a dashboard are returned with good quality."""
    login_as("viewer")
    data = client.get(f"/api/dashboards/{dashboards['private'].id}/values").get_json()
    assert len(data["values"]) == len(dashboards["private"].datapoints)
    assert all(v["quality"] == "good" for v in data["values"].values())
    plc_status = next(iter(data["plcs"].values()))
    assert plc_status["connected"] is True


def test_guest_cannot_read_private(client, login_as, dashboards):
    """Guests get 403 for private dashboards."""
    login_as("guest")
    assert client.get(f"/api/dashboards/{dashboards['private'].id}/values").status_code == 403
    assert client.get(f"/api/dashboards/{dashboards['public'].id}/values").status_code == 200


def test_unknown_dashboard_404_json(client, login_as, users):
    """Missing resources return JSON 404."""
    login_as("admin")
    resp = client.get("/api/dashboards/999/values")
    assert resp.status_code == 404 and resp.get_json()["error"] == "not found"


def test_write_and_read_back(client, login_as, dashboards):
    """Admins write values; the response confirms the new value."""
    login_as("admin")
    pts = dashboards["points"]
    r = client.post(f"/api/datapoints/{pts['setpoint'].id}/write", json={"value": 55.5}).get_json()
    assert r["ok"] and r["value"] == 55.5
    r = client.post(f"/api/datapoints/{pts['speed'].id}/write", json={"value": 12.3}).get_json()
    assert r["raw"] == 123 and r["value"] == 12.3  # scale 0.1
    r = client.post(f"/api/datapoints/{pts['run'].id}/write", json={"value": False}).get_json()
    assert r["value"] is False
    r = client.post(f"/api/datapoints/{pts['name'].id}/write", json={"value": "Batch 7"}).get_json()
    assert r["value"] == "Batch 7"
    values = client.get(f"/api/dashboards/{dashboards['private'].id}/values").get_json()["values"]
    assert values[str(pts["setpoint"].id)]["value"] == 55.5


def test_write_forbidden_for_viewer(client, login_as, dashboards):
    """Viewers have no write_values permission by default."""
    login_as("viewer")
    resp = client.post(f"/api/datapoints/{dashboards['points']['setpoint'].id}/write", json={"value": 1})
    assert resp.status_code == 403


def test_write_allowed_after_grant(client, login_as, db, dashboards):
    """Granting write_values to Viewer enables writes."""
    viewer = Role.query.filter_by(name="Viewer").one()
    viewer.set_permissions([Perm.VIEW_SCREENS, Perm.WRITE_VALUES])
    db.session.commit()
    login_as("viewer")
    resp = client.post(f"/api/datapoints/{dashboards['points']['setpoint'].id}/write", json={"value": 3})
    assert resp.status_code == 200


def test_write_read_only_point(client, login_as, dashboards):
    """Read-only points refuse writes."""
    login_as("admin")
    resp = client.post(f"/api/datapoints/{dashboards['points']['temp'].id}/write", json={"value": 1})
    assert resp.status_code == 400


def test_write_invalid_values(client, login_as, dashboards):
    """Bad payloads and out-of-range values give 400."""
    login_as("admin")
    pts = dashboards["points"]
    assert client.post(f"/api/datapoints/{pts['setpoint'].id}/write", json={}).status_code == 400
    assert client.post(f"/api/datapoints/{pts['setpoint'].id}/write", json={"value": "abc"}).status_code == 400
    assert client.post(f"/api/datapoints/{pts['speed'].id}/write", json={"value": 99999}).status_code == 400
    assert client.post(f"/api/datapoints/{pts['name'].id}/write", json={"value": "x" * 50}).status_code == 400


def test_offline_plc_quality_bad(client, login_as, db, dashboards, plc):
    """Values of an unreachable PLC are reported with bad quality."""
    plc.ip_address = "10.0.0.254"  # simulated offline
    db.session.commit()
    login_as("viewer")
    data = client.get(f"/api/dashboards/{dashboards['private'].id}/values").get_json()
    assert all(v["quality"] == "bad" and v["error"] for v in data["values"].values())


def test_test_read(client, login_as, plc):
    """The *Test read* endpoint parses and reads an address."""
    login_as("admin")
    ok = client.post(f"/api/plcs/{plc.id}/read", json={"address": "DB1.DBD0", "data_type": "REAL"})
    assert ok.status_code == 200 and isinstance(ok.get_json()["value"], float)
    bad = client.post(f"/api/plcs/{plc.id}/read", json={"address": "nonsense"})
    assert bad.status_code == 400


def test_plc_list_and_status(client, login_as, plc):
    """PLC list/status endpoints return JSON for admins only."""
    login_as("admin")
    assert client.get("/api/plcs").get_json()[0]["name"] == "PLC1"
    assert "connected" in client.get(f"/api/plcs/{plc.id}/status").get_json()
    client.post("/auth/logout")
    login_as("viewer")
    assert client.get("/api/plcs").status_code == 403
