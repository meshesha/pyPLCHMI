"""Tests for login, logout, profile and CSRF protection."""

from app import create_app
from app.extensions import db
from app.models import Role, User
from tests.conftest import PASSWORD, login, make_user


def test_login_page(client):
    """The login page renders."""
    assert b"login" in client.get("/auth/login").data.lower()


def test_requires_login(client):
    """Protected pages redirect anonymous visitors to the login page."""
    resp = client.get("/")
    assert resp.status_code == 302 and "/auth/login" in resp.location


def test_login_success_sets_last_login(client, users):
    """Valid credentials log the user in."""
    resp = login(client, "viewer")
    assert resp.status_code == 302
    assert users["viewer"].last_login is not None
    assert client.get("/screens/").status_code == 200


def test_login_wrong_password(client, users):
    """Wrong passwords are rejected with a generic message."""
    resp = login(client, "viewer", "nope")
    assert b"Invalid username or password" in resp.data


def test_login_unknown_user(client, users):
    """Unknown users get the same generic message."""
    assert b"Invalid username or password" in login(client, "ghost").data


def test_disabled_user_cannot_login(client, app):
    """Disabled accounts are refused."""
    make_user("off", "Viewer", active=False)
    resp = login(client, "off")
    assert resp.status_code == 403


def test_safe_next_redirect(client, users):
    """``next`` is honoured for local URLs only."""
    resp = client.post("/auth/login?next=/admin/", data={"username": "admin", "password": PASSWORD})
    assert resp.location.endswith("/admin/")
    client.post("/auth/logout")
    resp = client.post("/auth/login?next=http://evil.example/", data={"username": "admin", "password": PASSWORD})
    assert "evil" not in resp.location


def test_logout(client, login_as):
    """Logout ends the session."""
    login_as("viewer")
    client.post("/auth/logout")
    assert client.get("/screens/").status_code == 302


def test_change_password(client, login_as, users):
    """Users can change their own password."""
    login_as("viewer")
    resp = client.post("/auth/profile", data={
        "current_password": PASSWORD, "new_password": "brand-new-pass", "confirm": "brand-new-pass"})
    assert resp.status_code == 302
    assert users["viewer"].check_password("brand-new-pass")


def test_change_password_wrong_current(client, login_as, users):
    """A wrong current password is rejected."""
    login_as("viewer")
    resp = client.post("/auth/profile", data={
        "current_password": "bad", "new_password": "brand-new-pass", "confirm": "brand-new-pass"})
    assert b"incorrect" in resp.data
    assert users["viewer"].check_password(PASSWORD)


def test_csrf_enforced_when_enabled():
    """With CSRF enabled, a POST without token is rejected."""
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    with app.app_context():
        db.create_all()
        Role.insert_defaults()
        db.session.add(User(username="u", password=PASSWORD))
        db.session.commit()
        resp = app.test_client().post("/auth/login", data={"username": "u", "password": PASSWORD})
        assert resp.status_code == 400  # CSRFProtect rejects the request
        api = app.test_client().post("/api/datapoints/1/write", json={"value": 1})
        assert api.status_code == 400  # CSRF error
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
