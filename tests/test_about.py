"""The About page: project, author (GitHub), credits; system information for administrators only."""

from tests.conftest import login


def test_about_page_for_visitors(client, users):
    page = client.get("/about").get_data(as_text=True)
    assert "https://github.com/meshesha/pyPLCHMI" in page and "@meshesha" in page and "https://meshesha.js.org" in page
    assert "Bootstrap" in page and "python-snap7" in page
    assert 'id="about-system-table"' not in page                      # no server details for visitors
    assert "mailto:" not in page and "@gmail" not in page               # no e-mail address
    assert 'id="login-about"' in client.get("/auth/login").get_data(as_text=True)


def test_about_page_system_info_for_admins(client, users):
    login(client, "viewer")
    page = client.get("/about").get_data(as_text=True)
    assert 'id="about-system-table"' not in page and 'id="nav-about"' in page
    client.post("/auth/logout")
    login(client, "admin")
    page = client.get("/about").get_data(as_text=True)
    assert 'id="about-system-table"' in page and "Database schema revision" in page and "sqlite" in page
