"""Tests for the server health endpoint used by the browser's connection monitor."""

from unittest import mock

from app.api.routes import BOOT_ID


def test_health_is_public_and_not_cached(client):
    """/api/health answers without login, is never cached and sets no session cookie."""
    resp = client.get("/api/health")
    assert resp.status_code == 200 and resp.get_json() == {"status": "ok", "boot": BOOT_ID}
    assert resp.headers["Cache-Control"] == "no-store" and "Set-Cookie" not in resp.headers


def test_health_reports_database_failure(client, db):
    """A database that cannot be reached gives 503 (the monitor shows "database not available")."""
    with mock.patch.object(db.session, "execute", side_effect=RuntimeError("db down")):
        resp = client.get("/api/health")
    assert resp.status_code == 503 and resp.get_json()["status"] == "db_error"
    assert client.get("/api/health").status_code == 200


def test_monitor_script_on_every_page(client, login_as):
    """Every page loads the monitor with the health URL, the worker and the interval."""
    page = client.get("/auth/login").get_data(as_text=True)
    assert 'id="hmi-connection-js"' in page and 'data-health-url="/api/health"' in page
    assert 'data-worker-url="/static/js/hmi-heartbeat.js"' in page and 'data-interval="' in page
    assert client.get("/static/js/hmi-heartbeat.js").status_code == 200
