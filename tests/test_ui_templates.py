"""Tests for the UI templates: built-in templates, activation, preview, upload and the template features."""

import io
import json
import zipfile

import pytest

from app.models import AlarmEvent, AuditLog, Dashboard, DataPoint
from app.models.alarm import Alarm
from app.models.setting import get_setting
from app.ui_templates.registry import (
    FEATURES,
    TemplateError,
    all_templates,
    install_zip,
    parse_manifest,
)

BUILTIN = ["classic", "high-performance", "alarm-focus", "workspace", "engineering"]


def manifest(**kw) -> dict:
    """A valid manifest with overrides."""
    data = {"pyplchmi_template": 1, "id": "my-plant", "name": "My plant", "version": "1.0.0",
            "features": {"alarm_ui": "banner"}, "css": ["theme.css"]}
    data.update(kw)
    return data


def package(files: dict, top: str | None = "my-plant") -> io.BytesIO:
    """Build a template ZIP in memory.

    Args:
        files: ``name -> text or bytes``.
        top: Folder inside the ZIP (``None`` = files at the top).

    Returns:
        Readable bytes stream.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(f"{top}/{name}" if top else name, data)
    buf.seek(0)
    return buf


def good_files(**kw) -> dict:
    """Files of a valid package."""
    return {"template.json": json.dumps(manifest(**kw)), "theme.css": ":root { --hmi-accent: #ff00aa; }"}


def upload(client, stream, replace=False, name="pkg.zip"):
    """Post a package to the upload form."""
    data = {"file": (stream, name)}
    if replace:
        data["replace"] = "1"
    return client.post("/admin/ui-templates/upload", data=data, content_type="multipart/form-data",
                       follow_redirects=True)


def activate(client, tid):
    """Activate a template through the admin page."""
    return client.post(f"/admin/ui-templates/{tid}/activate", follow_redirects=True)


# ---------------------------------------------------------------------------- registry
def test_builtin_templates_load(app):
    """The five built-in templates load; classic is first and is the default."""
    templates = all_templates()
    assert list(templates)[:1] == ["classic"]
    assert set(BUILTIN) <= set(templates)
    assert all(templates[t].builtin for t in BUILTIN)
    assert templates["high-performance"].feature("tiles") == "hp"
    assert templates["workspace"].feature("nav") == "sidebar"
    assert templates["classic"].changed_features() == {}
    assert templates["workspace"].title("he") == "סביבות עבודה"


@pytest.mark.parametrize("change, message", [
    ({"pyplchmi_template": 2}, "pyplchmi_template"),
    ({"id": "Bad Id"}, "Invalid template id"),
    ({"name": ""}, "needs a name"),
    ({"color_mode": "pink"}, "color_mode"),
    ({"features": {"nav": "floating"}}, "cannot be"),
    ({"features": {"wobble": True}}, "Unknown feature"),
    ({"css": ["../x.css"]}, "Invalid file path"),
    ({"css": ["missing.css"]}, "missing"),
    ({"js": ["theme.css"]}, "wrong file type"),
])
def test_manifest_validation(app, tmp_path, change, message):
    """Invalid manifests are rejected with a clear message."""
    (tmp_path / "theme.css").write_text("")
    with pytest.raises(TemplateError, match=message):
        parse_manifest(manifest(**change), str(tmp_path))


# ---------------------------------------------------------------------------- admin page
def test_admin_only(client, login_as):
    """Only administrators may open or change the templates."""
    login_as("viewer")
    assert client.get("/admin/ui-templates").status_code == 403
    assert client.post("/admin/ui-templates/workspace/activate").status_code == 403
    assert client.post("/admin/ui-templates/upload").status_code == 403


def test_activate_for_everyone(app, client, login_as):
    """Activating stores the setting, changes every page and is audited."""
    login_as("admin")
    page = client.get("/admin/ui-templates").get_data(as_text=True)
    for tid in BUILTIN:
        assert f'data-template="{tid}"' in page
    activate(client, "alarm-focus")
    assert get_setting("ui_template") == "alarm-focus"
    html = client.get("/screens/").get_data(as_text=True)
    assert 'data-ui-template="alarm-focus"' in html
    assert "/ui-templates/alarm-focus/theme.css?v=" in html
    assert 'id="alarm-strip"' in html
    assert AuditLog.query.filter_by(object_type="Setting", object_label="ui_template").count() == 1
    assert client.post("/admin/ui-templates/nope/activate").status_code == 404


def test_preview_is_per_session(app, client, login_as, users):
    """A preview changes only the administrator's session, until it ends."""
    login_as("admin")
    client.post("/admin/ui-templates/workspace/preview")
    html = client.get("/screens/").get_data(as_text=True)
    assert 'data-ui-template="workspace"' in html and 'id="ui-preview-bar"' in html
    assert get_setting("ui_template", "classic") == "classic"
    other = app.test_client()
    from tests.conftest import login  # noqa: PLC0415

    login(other, "viewer")
    assert 'data-ui-template="classic"' in other.get("/screens/").get_data(as_text=True)
    client.post("/admin/ui-templates/preview/stop")
    assert 'data-ui-template="classic"' in client.get("/screens/").get_data(as_text=True)


def test_color_mode_overrides_user_choice(client, login_as, users, db):
    """A template with a fixed colour mode wins over the user's theme."""
    users["admin"].theme = "dark"
    db.session.commit()
    login_as("admin")
    activate(client, "high-performance")
    html = client.get("/auth/profile").get_data(as_text=True)
    assert 'data-bs-theme="light"' in html and 'id="theme-forced"' in html
    activate(client, "classic")
    assert 'data-bs-theme="dark"' in client.get("/auth/profile").get_data(as_text=True)


# ---------------------------------------------------------------------------- static files
def test_asset_serving(client):
    """Template files are served with nosniff; manifests, docs and paths outside the folder are not."""
    resp = client.get("/ui-templates/high-performance/theme.css")
    assert resp.status_code == 200 and resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "text/css" in resp.content_type
    svg = client.get("/ui-templates/workspace/preview.svg")
    assert svg.status_code == 200 and "default-src 'none'" in svg.headers["Content-Security-Policy"]
    assert client.get("/ui-templates/workspace/template.json").status_code == 404
    assert client.get("/ui-templates/workspace/../classic/template.json").status_code == 404
    assert client.get("/ui-templates/nope/theme.css").status_code == 404


# ---------------------------------------------------------------------------- upload
def test_upload_preview_activate_delete(app, client, login_as):
    """Full life cycle of an uploaded template."""
    login_as("admin")
    files = good_files(js=["extra.js"])
    files["extra.js"] = "console.log('hi');"
    files["img/logo.png"] = b"\x89PNG\r\n\x1a\n"
    page = upload(client, package(files)).get_data(as_text=True)
    assert "installed" in page and "contains JavaScript" in page
    assert "my-plant" in all_templates()
    assert client.get("/ui-templates/my-plant/img/logo.png").status_code == 200
    assert AuditLog.query.filter_by(object_type="UI template", action="create").count() == 1

    activate(client, "my-plant")
    html = client.get("/screens/").get_data(as_text=True)
    assert "/ui-templates/my-plant/theme.css" in html and "/ui-templates/my-plant/extra.js" in html

    # download -> same files inside a <id>/ folder
    data = client.get("/admin/ui-templates/my-plant/download").data
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "my-plant/template.json" in names and "my-plant/img/logo.png" in names

    # same id again needs "replace"
    assert "already exists" in upload(client, package(good_files(version="1.1"))).get_data(as_text=True)
    upload(client, package(good_files(version="1.1")), replace=True)
    assert all_templates()["my-plant"].version == "1.1"
    assert AuditLog.query.filter_by(object_type="UI template", action="update").count() == 1

    # deleting the active template falls back to classic
    client.post("/admin/ui-templates/my-plant/delete")
    assert "my-plant" not in all_templates()
    assert get_setting("ui_template") == "classic"
    assert client.post("/admin/ui-templates/classic/delete", follow_redirects=True).status_code == 200
    assert "classic" in all_templates()  # built-in templates cannot be deleted


def test_upload_files_at_top_level(app, client, login_as):
    """A ZIP without a top folder works too."""
    login_as("admin")
    upload(client, package(good_files(), top=None))
    assert "my-plant" in all_templates()


@pytest.mark.parametrize("files, message", [
    ({"template.json": json.dumps(manifest(id="classic")), "theme.css": ""}, "built-in template"),
    ({"template.json": json.dumps(manifest()), "theme.css": "", "evil.py": "import os"}, "File type not allowed"),
    ({"template.json": json.dumps(manifest()), "theme.css": "", "base.html": "{{ config }}"}, "File type not allowed"),
    ({"theme.css": ""}, "template.json was not found"),
    ({"template.json": "{not json", "theme.css": ""}, "not valid JSON"),
    ({"template.json": json.dumps(manifest(features={"tiles": "3d"})), "theme.css": ""}, "cannot be"),
])
def test_upload_rejects_bad_packages(app, client, login_as, files, message):
    """Bad packages are refused and nothing is installed."""
    login_as("admin")
    page = upload(client, package(files)).get_data(as_text=True)
    assert message in page
    assert "my-plant" not in all_templates()


def test_upload_rejects_path_escape_and_non_zip(app, tmp_path):
    """ZIP slip paths and non-ZIP data are refused; nothing is written outside the folder."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("template.json", json.dumps(manifest()))
        zf.writestr("theme.css", "")
        zf.writestr("../../escape.css", "x")
    buf.seek(0)
    with app.test_request_context():
        with pytest.raises(TemplateError, match="Invalid file path"):
            install_zip(buf)
        with pytest.raises(TemplateError, match="not a ZIP"):
            install_zip(io.BytesIO(b"hello"))
    assert not (tmp_path / "escape.css").exists()


# ---------------------------------------------------------------------------- features
def test_sidebar_overview_and_palette(app, client, login_as, dashboards):
    """workspace: sidebar with both workspaces, overview home page, quick search."""
    login_as("admin")
    activate(client, "workspace")
    resp = client.get("/")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/overview")
    html = client.get("/overview").get_data(as_text=True)
    assert 'id="hmi-sidebar"' in html and 'id="ws-engineer"' in html and 'id="side-tags"' in html
    assert 'id="hmi-palette"' in html and 'id="hmi-overview"' in html
    assert 'data-screen-path="/screens/private"' in html


def test_sidebar_for_operator_has_no_engineer_workspace(app, client, dashboards):
    """Users without engineering pages get no workspace switch."""
    from tests.conftest import login, make_user  # noqa: PLC0415

    make_user("op", "Operator")
    login(client, "admin")
    activate(client, "workspace")
    client.post("/auth/logout")
    login(client, "op")
    html = client.get("/screens/").get_data(as_text=True)
    assert 'id="hmi-sidebar"' in html and 'id="ws-engineer"' not in html and 'id="side-tags"' not in html


def test_overview_api(app, client, login_as, dashboards, db):
    """/api/overview: one entry per visible screen with alarms and values."""
    temp = dashboards["points"]["temp"]
    alarm = Alarm(name="Too hot", datapoint=temp, operator="gt", setpoint="50", severity="critical",
                  screen=dashboards["private"])
    event = AlarmEvent(alarm=alarm, name="Too hot", severity="critical", screen=dashboards["private"])
    db.session.add_all([alarm, event])
    db.session.commit()
    assert event.to_dict()["tag_id"] == temp.id
    login_as("admin")
    data = client.get("/api/overview").get_json()
    by_name = {s["name"]: s for s in data["screens"]}
    assert set(by_name) == {"Private", "Public"}
    assert by_name["Private"]["alarms"] == 1 and by_name["Private"]["severity"] == "critical"
    assert [v["label"] for v in by_name["Private"]["values"]] == ["Temp", "Setpoint", "Speed"]
    assert data["plcs"]
    client.post("/auth/logout")
    assert client.get("/api/overview").status_code == 401


def test_search_api(app, client, login_as, dashboards):
    """/api/search finds pages, screens, tags and PLCs the user may open."""
    login_as("admin")
    labels = {r["label"]: r["kind"] for r in client.get("/api/search?q=temp").get_json()["results"]}
    assert labels.get("Temp") == "tag"
    kinds = {r["kind"] for r in client.get("/api/search?q=").get_json()["results"]}
    assert {"page", "screen"} <= kinds and "tag" not in kinds
    assert any(r["kind"] == "plc" for r in client.get("/api/search?q=plc1").get_json()["results"])
    client.post("/auth/logout")
    login_as("viewer")
    results = client.get("/api/search?q=temp").get_json()["results"]
    assert not any(r["kind"] == "tag" for r in results)


def test_hp_tiles(app, client, login_as, dashboards, db):
    """high-performance: hp grid, range bar for tags with limits, Change… button, trend for logged tags."""
    sp = dashboards["points"]["setpoint"]
    sp.min_value, sp.max_value = 0, 100
    sp.log_enabled = True
    db.session.commit()
    login_as("admin")
    activate(client, "high-performance")
    html = client.get("/screens/private").get_data(as_text=True)
    assert "hmi-grid-hp" in html and 'class="hmi-hp-range"' in html and "hmi-change" in html
    assert 'class="hmi-spark"' in html and "data-series-url=\"/history/api/series\"" in html
    assert 'class="hmi-write' not in html  # dialogs instead of inline forms
    activate(client, "classic")
    html = client.get("/screens/private").get_data(as_text=True)
    assert "hmi-grid-hp" not in html and "hmi-write" in html


def test_alarm_strip_in_full_screen_frame_only_for_screens(client, login_as, dashboards):
    """The alarm strip is shown in the full-screen frame of a screen but not in other embedded pages."""
    login_as("admin")
    activate(client, "alarm-focus")
    assert 'id="alarm-strip"' in client.get("/screens/private?embed=1").get_data(as_text=True)
    tag = dashboards["points"]["temp"]
    assert 'id="alarm-strip"' not in client.get(f"/tags/{tag.id}/edit?embed=1").get_data(as_text=True)


def test_tag_panel_and_bulk_actions(app, client, login_as, dashboards, db):
    """engineering: side panel and bulk bar; bulk add to screen and bulk delete."""
    login_as("admin")
    activate(client, "engineering")
    html = client.get("/tags/").get_data(as_text=True)
    assert 'id="tag-panel"' in html and 'id="bulk-bar"' in html and "embed=1" in html
    pts = dashboards["points"]
    ids = [str(pts["pub"].id), str(pts["name"].id)]
    client.post("/tags/bulk", data={"ids": ids, "action": "screen", "screen_id": dashboards["public"].id})
    assert {dp.label for dp in db.session.get(Dashboard, dashboards["public"].id).datapoints} >= {"Public value", "Name"}
    db.session.add(Alarm(name="Name alarm", datapoint=pts["name"], operator="eq", setpoint="x"))
    db.session.commit()
    page = client.post("/tags/bulk", data={"ids": ids, "action": "delete"}, follow_redirects=True).get_data(as_text=True)
    assert "Kept because alarms use them: Name" in page
    assert db.session.get(DataPoint, int(ids[0])) is None and db.session.get(DataPoint, int(ids[1])) is not None
    assert "Select one or more tags" in client.post("/tags/bulk", data={"action": "delete"},
                                                    follow_redirects=True).get_data(as_text=True)


@pytest.mark.parametrize("tid", BUILTIN)
def test_every_template_renders_main_pages(client, login_as, dashboards, tid):
    """Every built-in template renders the main pages (admin and visitor)."""
    login_as("admin")
    activate(client, tid)
    for url in ("/screens/", "/screens/private", "/screens/public", "/overview", "/tags/", "/admin/",
                "/admin/ui-templates", "/alarms/", "/auth/profile", "/screens/private?embed=1"):
        resp = client.get(url)
        assert resp.status_code == 200, (tid, url)
        assert f'data-ui-template="{tid}"' in resp.get_data(as_text=True)
    client.post("/auth/logout")
    assert client.get("/auth/login").status_code == 200
    assert client.get("/screens/public").status_code in (200, 302, 403)  # public access is off by default


def test_features_table_is_documented():
    """Every feature has a label (shown on the admin page)."""
    from app.ui_templates.registry import (  # noqa: PLC0415
        FEATURE_LABELS,
        FEATURE_VALUE_LABELS,
    )

    assert set(FEATURE_LABELS) == set(FEATURES)
    for values in FEATURES.values():
        assert all(v in FEATURE_VALUE_LABELS for v in values)


def test_documented_example_installs(app, client, login_as):
    """The example template of docs/UI_TEMPLATES.md is a valid package."""
    import os  # noqa: PLC0415

    root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "examples", "ui-templates", "example-plant")
    files = {}
    for name in os.listdir(root):
        with open(os.path.join(root, name), "rb") as fh:
            files[name] = fh.read()
    login_as("admin")
    upload(client, package(files, top="example-plant"))
    tpl = all_templates()["example-plant"]
    assert tpl.feature("nav") == "sidebar" and tpl.js == ["theme.js"] and tpl.title("he") == "מתקן לדוגמה"
    assert client.get("/ui-templates/example-plant/README.md").status_code == 404
