"""Tests for per-user screen access, home screens, public (anonymous) access and the UI changes."""

import io

import pytest

from app.models import Dashboard, Role, User
from app.models.setting import PUBLIC_HOME_SCREEN, get_setting, public_access_enabled, set_setting
from app.models.user import screen_visible
from tests.conftest import PASSWORD, add_point, login


@pytest.fixture()
def screens(db, plc, users):
    """Three screens: ``lobby`` (public, image), ``plant`` (public), ``office`` (private).

    Returns:
        Dict name -> Dashboard.
    """
    lobby = Dashboard(name="Lobby", slug="lobby", kind="image", is_public=True)
    plant = Dashboard(name="Plant", slug="plant", is_public=True)
    office = Dashboard(name="Office", slug="office", is_public=False)
    db.session.add_all([lobby, plant, office])
    db.session.commit()
    add_point(plant, plc, "Temp", "DB1.DBD0:REAL")
    add_point(office, plc, "Secret", "DB1.DBD4:REAL", writable=True)
    lobby.set_layout({"version": 1, "canvas": {"width": 800, "height": 600},
                      "elements": [{"id": "l1", "type": "rect", "x": 0, "y": 0, "w": 50, "h": 50, "link": office.id}]})
    db.session.commit()
    return {"lobby": lobby, "plant": plant, "office": office}


@pytest.fixture()
def public_on(db, screens):
    """Configure ``lobby`` as the public home screen."""
    set_setting(PUBLIC_HOME_SCREEN, screens["lobby"].id)
    db.session.commit()


# ---------------------------------------------------------------------------
# Access rule
# ---------------------------------------------------------------------------
def test_screen_visible_rules(app, screens):
    """Role rules, per-user restriction and the manager override."""
    admin, viewer, guest = (Role.query.filter_by(name=n).one() for n in ("Admin", "Viewer", "Guest"))
    office, plant = screens["office"], screens["plant"]
    assert screen_visible(viewer, False, [], office)
    assert not screen_visible(guest, False, [], office)
    assert screen_visible(guest, False, [], plant)
    # restriction overrides the role in both directions
    assert not screen_visible(viewer, True, [plant.id], office)
    assert screen_visible(guest, True, [office.id], office)
    assert not screen_visible(guest, True, [office.id], plant)
    # managers always see everything
    assert screen_visible(admin, True, [], office)
    assert not screen_visible(None, False, [], plant)


# ---------------------------------------------------------------------------
# Admin user form
# ---------------------------------------------------------------------------
def user_data(role, **extra):
    """POST data for the user form.

    Args:
        role: Role name.
        **extra: Additional fields.

    Returns:
        Form data dict.
    """
    data = {"username": "operator1", "role_id": Role.query.filter_by(name=role).one().id, "active": "y",
            "password": "operator-pass", "confirm": "operator-pass", "home_screen_id": 0}
    data.update(extra)
    return data


def test_admin_sets_screen_access_and_home(client, login_as, screens):
    """Admins limit a user to screens and set the home screen."""
    login_as("admin")
    resp = client.post("/admin/users/new", data=user_data(
        "Viewer", restrict_screens="y", allowed_screens=[screens["plant"].id, screens["office"].id],
        home_screen_id=screens["office"].id))
    assert resp.status_code == 302
    user = User.query.filter_by(username="operator1").one()
    assert user.restrict_screens and {s.name for s in user.allowed_screens} == {"Plant", "Office"}
    assert user.home_screen.name == "Office"
    page = client.get("/admin/users").data.decode()
    assert "2 selected" in page and "Office" in page


def test_home_screen_must_be_visible(client, login_as, screens):
    """A home screen the user cannot open is rejected."""
    login_as("admin")
    resp = client.post("/admin/users/new", data=user_data("Guest", home_screen_id=screens["office"].id))
    assert b"cannot open" in resp.data
    resp = client.post("/admin/users/new", data=user_data(
        "Viewer", restrict_screens="y", allowed_screens=[screens["plant"].id], home_screen_id=screens["office"].id))
    assert b"cannot open" in resp.data
    assert User.query.filter_by(username="operator1").first() is None


def test_restrict_needs_selection(client, login_as, screens):
    """Limiting a user to zero screens is refused."""
    login_as("admin")
    resp = client.post("/admin/users/new", data=user_data("Viewer", restrict_screens="y"))
    assert b"Select at least one screen" in resp.data


def test_edit_keeps_and_clears_access(client, login_as, db, screens, users):
    """Editing updates the selection; unticking the limit restores role rules."""
    login_as("admin")
    uid = users["viewer"].id
    client.post(f"/admin/users/{uid}/edit", data={"username": "viewer", "role_id": users["viewer"].role_id, "active": "y",
                                                  "restrict_screens": "y", "allowed_screens": [screens["plant"].id]})
    assert db.session.get(User, uid).restrict_screens
    client.post(f"/admin/users/{uid}/edit", data={"username": "viewer", "role_id": users["viewer"].role_id, "active": "y"})
    assert not db.session.get(User, uid).restrict_screens


# ---------------------------------------------------------------------------
# Login / home screen / restricted user
# ---------------------------------------------------------------------------
def test_login_opens_home_screen(client, db, screens, users):
    """After login the user lands on their home screen; ?next= wins."""
    users["viewer"].home_screen_id = screens["plant"].id
    db.session.commit()
    assert login(client, "viewer").location.endswith("/screens/plant")
    client.post("/auth/logout")
    resp = client.post("/auth/login?next=/screens/office", data={"username": "viewer", "password": PASSWORD})
    assert resp.location.endswith("/screens/office")
    assert client.get("/").location.endswith("/screens/plant")


def test_inaccessible_home_falls_back_to_list(client, db, screens, users):
    """A home screen the user can no longer open is ignored."""
    users["guest"].home_screen_id = screens["office"].id  # guest cannot open private screens
    db.session.commit()
    assert login(client, "guest").location.endswith("/screens/")


def test_restricted_user_sees_only_allowed(client, db, screens, users):
    """A limited user sees only the selected screens; others show Restricted Area."""
    u = users["viewer"]
    u.restrict_screens = True
    u.allowed_screens = [screens["plant"]]
    db.session.commit()
    login(client, "viewer")
    listing = client.get("/screens/").data.decode()
    assert "Plant" in listing and "Office" not in listing and "Lobby" not in listing
    resp = client.get("/screens/office")
    assert resp.status_code == 403 and b"Restricted Area" in resp.data
    assert client.get(f"/api/dashboards/{screens['office'].id}/values").status_code == 403


# ---------------------------------------------------------------------------
# Public (anonymous) access
# ---------------------------------------------------------------------------
def test_public_access_off_by_default(client, screens):
    """Without a public home screen, visitors must log in."""
    assert not public_access_enabled()
    assert client.get("/").location.endswith("/auth/login")
    resp = client.get("/screens/plant")
    assert resp.status_code == 403 and b"Restricted Area" in resp.data and b"Log in" in resp.data
    assert client.get(f"/api/dashboards/{screens['plant'].id}/values").status_code == 401


def test_settings_page(client, login_as, db, screens):
    """Admins choose the public home screen; only public screens are allowed."""
    login_as("admin")
    page = client.get("/admin/settings").data.decode()
    assert "Lobby" in page and "Office" not in page
    client.post("/admin/settings", data={"public_home_screen_id": screens["lobby"].id})
    assert get_setting(PUBLIC_HOME_SCREEN) == str(screens["lobby"].id)
    resp = client.post("/admin/settings", data={"public_home_screen_id": screens["office"].id})
    assert b"Not a valid choice" in resp.data
    client.post("/admin/settings", data={"public_home_screen_id": 0})
    assert get_setting(PUBLIC_HOME_SCREEN) is None


@pytest.mark.parametrize("role", ["viewer", "guest"])
def test_settings_admin_only(client, login_as, screens, role):
    """Only administrators can open the settings."""
    login_as(role)
    assert client.get("/admin/settings").status_code == 403


def test_anonymous_public_navigation(client, public_on, screens):
    """Visitors land on the public home and can open public screens only."""
    assert client.get("/").location.endswith("/screens/lobby")
    lobby = client.get("/screens/lobby").data.decode()
    assert 'id="mimic-svg"' in lobby and 'id="login-btn"' in lobby and "Plant" in lobby  # public menu
    assert "Office" not in lobby.split('id="mimic-data"')[0]  # not in the menu
    assert client.get("/screens/plant").status_code == 200
    resp = client.get("/screens/office")
    assert resp.status_code == 403 and b"Restricted Area" in resp.data and b"Back to public screen" in resp.data
    # a link on the public screen to the private one ends in the restricted page
    go = client.get(f"/screens/go/{screens['office'].id}", follow_redirects=True)
    assert go.status_code == 403 and b"Restricted Area" in go.data
    listing = client.get("/screens/").data.decode()
    assert "Plant" in listing and "Office" not in listing


def test_anonymous_api(client, public_on, screens):
    """Visitors read values of public screens but never write."""
    plant, office = screens["plant"], screens["office"]
    assert client.get(f"/api/dashboards/{plant.id}/values").status_code == 200
    assert client.get(f"/api/dashboards/{office.id}/values").status_code == 401
    assert client.get(f"/api/dashboards/{screens['lobby'].id}/layout").status_code == 200
    assert client.post(f"/api/datapoints/{office.datapoints[0].id}/write", json={"value": 1}).status_code == 401


def test_logout_shows_public_home(client, login_as, public_on, screens):
    """After logout the public home screen is displayed."""
    login_as("viewer")
    resp = client.post("/auth/logout")
    assert resp.location.endswith("/screens/lobby")
    page = client.get(resp.location).data.decode()
    assert "logged out" in page and 'id="login-btn"' in page


def test_logout_without_public_home_goes_to_login(client, login_as, screens):
    """Without public access, logout returns to the login page."""
    login_as("viewer")
    assert client.post("/auth/logout").location.endswith("/auth/login")


def test_unpublishing_home_disables_public_access(client, login_as, db, public_on, screens):
    """If the public home screen stops being public, visitors see the login page again."""
    login_as("admin")
    resp = client.post(f"/screens/{screens['lobby'].id}/edit",
                       data={"name": "Lobby", "kind": "image", "refresh_ms": 1000}, follow_redirects=True)
    assert b"no longer public" in resp.data
    client.post("/auth/logout")
    assert not public_access_enabled()
    assert client.get("/").location.endswith("/auth/login")


def test_delete_screen_clears_references(client, login_as, db, public_on, screens, users):
    """Deleting a screen clears home screens and the public home setting."""
    users["viewer"].home_screen_id = screens["lobby"].id
    db.session.commit()
    login_as("admin")
    client.post(f"/screens/{screens['lobby'].id}/delete")
    assert db.session.get(User, users["viewer"].id).home_screen_id is None
    assert get_setting(PUBLIC_HOME_SCREEN) is None


def test_anonymous_assets(client, login_as, db, screens):
    """Visitors may load images used by public screens only."""
    from tests.test_assets import png_bytes

    login_as("admin")
    upl = lambda: client.post("/assets/upload", data={"file": (io.BytesIO(png_bytes()), "a.png")},  # noqa: E731
                              content_type="multipart/form-data", headers={"Accept": "application/json"})
    used, unused = upl().get_json()["assets"][0]["id"], upl().get_json()["assets"][0]["id"]
    lobby = screens["lobby"]
    lobby.set_layout({**lobby.layout_data, "canvas": {**lobby.layout_data["canvas"], "backgroundImage": used}})
    set_setting(PUBLIC_HOME_SCREEN, lobby.id)
    db.session.commit()
    client.post("/auth/logout")
    assert client.get(f"/assets/{used}/file").status_code == 200
    assert client.get(f"/assets/{unused}/file").status_code == 302


# ---------------------------------------------------------------------------
# UI: naming, login button, hamburger header, legacy URLs
# ---------------------------------------------------------------------------
def test_ui_uses_screen_wording(client, login_as, screens):
    """The UI says 'Screen', not 'Dashboard'."""
    login_as("admin")
    for url in ("/screens/", "/screens/new", f"/screens/{screens['plant'].id}/edit", "/screens/plant", "/admin/roles"):
        html = client.get(url).data.decode()
        visible = html.split("<script", 1)[0]
        assert "Dashboard" not in visible and " dashboard" not in visible, url


def test_login_and_logout_buttons_share_position(client, login_as, screens):
    """Both account buttons are the last item of the icon group of the top bar."""
    anon = client.get("/auth/login").data.decode()
    assert 'id="login-btn"' in anon and 'id="logout-btn"' not in anon
    login_as("viewer")
    page = client.get("/screens/").data.decode()
    assert 'id="logout-btn"' in page and 'id="login-btn"' not in page
    for html, btn in ((anon, "login-btn"), (page, "logout-btn")):
        right = html.split('hmi-icon-nav">', 1)[1].split("</nav>", 1)[0]
        assert btn in right.rsplit('<li class="nav-item">', 1)[1]


def test_screen_name_and_edit_in_top_bar(client, login_as, screens):
    """Screens have no sub-header: name, full screen and Edit are in the top bar; no pause, no PLC list."""
    login_as("admin")
    html = client.get("/screens/lobby").data.decode()
    nav = html.split("</nav>", 1)[0]
    assert 'id="screen-head"' in nav and "Lobby" in nav and 'id="screen-edit"' in nav and 'id="fullscreen-btn"' in nav
    for gone in ("mimic-menu-btn", "mimic-header", "pause-btn", "plc-status", "last-update"):
        assert gone not in html
    assert 'id="hmi-clock"' in nav and "hmi-shell.js" in html


def test_legacy_dashboard_urls_redirect(client):
    """Old /dashboards/... bookmarks redirect to /screens/...."""
    resp = client.get("/dashboards/plant")
    assert resp.status_code == 301 and resp.location.endswith("/screens/plant")


def test_navbar_login_goes_to_home_screen(client, db, public_on, screens, users):
    """Logging in from the navbar lands on the user's home screen, not the page being viewed."""
    users["viewer"].home_screen_id = screens["plant"].id
    db.session.commit()
    html = client.get("/screens/lobby").data.decode()
    href = html.split('id="login-btn"', 1)[1].split('href="', 1)[1].split('"', 1)[0]
    assert href == "/auth/login"
    resp = client.post(href, data={"username": "viewer", "password": PASSWORD})
    assert resp.location.endswith("/screens/plant")


def test_restricted_page_login_returns_to_screen(client, public_on, screens):
    """The Log in button on the Restricted Area page returns to the requested screen."""
    html = client.get("/screens/office").data.decode()
    assert "/auth/login?next=/screens/office" in html
