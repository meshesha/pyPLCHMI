"""Tests for the top bar (icons, clock, logout confirmation) and the colour theme setting."""

from app.extensions import db
from app.models import User


def nav(html: str) -> str:
    """The top bar of a page."""
    return html.split("</nav>", 1)[0]


def test_icon_buttons_have_hover_labels(client, login_as):
    """Top bar buttons are icons; their names are hover labels (and aria-labels)."""
    login_as("admin")
    bar = nav(client.get("/screens/").get_data(as_text=True))
    for text in ("Screens", "Events", "Log out", "Settings", "Admin"):
        assert f'<span class="hmi-nav-label">{text}' in bar
    for text in ("Screens", "Log out", "Settings", "Admin", "Active alarm events"):
        assert f'aria-label="{text}"' in bar
    assert 'class="hmi-clock"' in bar


def test_language_menu_only_in_settings(client, login_as):
    """The language choice is on the Settings page, not in the top bar."""
    login_as("viewer")
    page = client.get("/screens/").get_data(as_text=True)
    assert "lang-menu" not in page and "i18n.set_language" not in page and 'action="/language"' not in nav(page)
    settings = client.get("/auth/profile").get_data(as_text=True)
    assert 'id="profile-language"' in settings and 'id="profile-theme"' in settings
    anon = client.get("/auth/login").get_data(as_text=True)
    assert 'action="/language"' not in anon


def test_logout_asks_for_confirmation(client, login_as):
    """The logout form carries a SweetAlert2 confirmation."""
    login_as("viewer")
    bar = nav(client.get("/screens/").get_data(as_text=True))
    form = bar.split('action="/auth/logout"', 1)[1].split(">", 1)[0]
    assert 'data-confirm="Log out of viewer?"' in form and 'data-confirm-button="Log out"' in form


def test_theme_setting(client, login_as, users):
    """Users choose dark, light or automatic; the page follows the choice."""
    login_as("viewer")
    assert 'data-bs-theme="dark" data-hmi-theme="dark"' in client.get("/screens/").get_data(as_text=True)
    resp = client.post("/auth/theme", data={"theme": "light"})
    assert resp.status_code == 302 and db.session.get(User, users["viewer"].id).theme == "light"
    assert 'data-bs-theme="light" data-hmi-theme="light"' in client.get("/screens/").get_data(as_text=True)
    client.post("/auth/theme", data={"theme": "auto"})
    assert 'data-hmi-theme="auto"' in client.get("/auth/profile").get_data(as_text=True)
    client.post("/auth/theme", data={"theme": "pink"})
    assert db.session.get(User, users["viewer"].id).theme == "auto"


def test_theme_requires_login(client):
    """Visitors cannot store a theme and get the dark theme."""
    assert client.post("/auth/theme", data={"theme": "light"}).status_code == 302
    assert 'data-bs-theme="dark"' in client.get("/auth/login").get_data(as_text=True)


def test_shell_script_served(client):
    """The clock / full-screen script is served."""
    assert client.get("/static/js/hmi-shell.js").status_code == 200


def test_fullscreen_frame_mode(client, login_as, dashboards):
    """?embed=1 shows the screen alone (frame of the full-screen mode); links between screens keep it."""
    login_as("admin")
    page = client.get("/screens/private").get_data(as_text=True)
    assert 'data-fullscreen-frame="1"' in page and '<nav class="navbar' in page
    embedded = client.get("/screens/private?embed=1").get_data(as_text=True)
    assert 'class="hmi-embed"' in embedded and '<nav class="navbar' not in embedded and 'id="hmi-grid"' in embedded
    target = dashboards["public"].id
    assert client.get(f"/screens/go/{target}?embed=1").location.endswith("/screens/public?embed=1")
    assert client.get(f"/screens/go/{target}").location.endswith("/screens/public")


def test_page_background_of_image_screens(client, login_as, db):
    """Image screens: no gap/border, and the page can have its own colour and image."""
    from app.dashboards.layout import page_style, referenced_assets, sanitize_layout  # noqa: PLC0415
    from app.models import Asset, Dashboard  # noqa: PLC0415

    asset = Asset(name="wall", filename="wall.png", mimetype="image/png", size=1)
    screen = Dashboard(name="Hall", slug="hall", kind="image")
    db.session.add_all([asset, screen])
    db.session.commit()
    layout = sanitize_layout({"canvas": {"width": 800, "height": 400, "pageBackground": "#203040",
                                         "pageBackgroundImage": asset.id, "pageBackgroundFit": "repeat"},
                              "elements": []}, set(), {asset.id}, set())
    assert layout["canvas"]["pageBackground"] == "#203040" and layout["canvas"]["pageBackgroundFit"] == "repeat"
    assert asset.id in referenced_assets(layout)
    css = page_style(layout, lambda i: f"/assets/{i}/file")
    assert "background-color: #203040;" in css and f"url('/assets/{asset.id}/file')" in css and "repeat" in css
    assert page_style({"canvas": {"pageBackground": "none"}}, str) == ""
    bad = sanitize_layout({"canvas": {"pageBackground": "red;}</style>", "pageBackgroundFit": "x"}, "elements": []},
                          set(), set(), set())
    assert bad["canvas"]["pageBackground"] == "none" and bad["canvas"]["pageBackgroundFit"] == "cover"
    screen.set_layout(layout)
    db.session.commit()
    login_as("admin")
    html = client.get("/screens/hall").get_data(as_text=True)
    main = html.split("<main", 1)[1].split(">", 1)[0]
    assert "p-0 hmi-page-mimic" in main and "background-color: #203040;" in main
