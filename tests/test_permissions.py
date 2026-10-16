"""Role-based access matrix: which role can open which page."""

import pytest

ADMIN_PAGES = ["/admin/", "/admin/users", "/admin/roles", "/admin/plcs", "/admin/plcs/new", "/screens/new"]


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_admin_pages_allowed_for_admin(client, login_as, url):
    """Admins can open every management page."""
    login_as("admin")
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("role", ["viewer", "guest"])
@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_admin_pages_forbidden(client, login_as, role, url):
    """Viewers and guests get 403 on management pages."""
    login_as(role)
    assert client.get(url).status_code == 403


def test_dashboard_visibility(client, login_as, dashboards):
    """Viewer: all dashboards. Guest: only public ones."""
    login_as("viewer")
    assert client.get("/screens/private").status_code == 200
    assert client.get("/screens/public").status_code == 200
    client.post("/auth/logout")
    login_as("guest")
    assert client.get("/screens/private").status_code == 403
    assert client.get("/screens/public").status_code == 200
    home = client.get("/screens/").data
    assert b"Public" in home and b"Private" not in home


def test_permission_change_takes_effect(client, login_as, db, dashboards):
    """Granting a permission to a role immediately unlocks the feature."""
    from app.models import Perm, Role

    login_as("guest")
    assert client.get("/screens/private").status_code == 403
    guest = Role.query.filter_by(name="Guest").one()
    guest.set_permissions([Perm.VIEW_DASHBOARDS])
    db.session.commit()
    assert client.get("/screens/private").status_code == 200


def test_edit_button_only_for_managers(client, login_as, dashboards):
    """The *Edit* button is shown only with manage_dashboards."""
    login_as("viewer")
    assert b'id="screen-edit"' not in client.get("/screens/private").data
    client.post("/auth/logout")
    login_as("admin")
    assert b'id="screen-edit"' in client.get("/screens/private").data
