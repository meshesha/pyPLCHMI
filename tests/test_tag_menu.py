"""Tests for the right-click menu of screen values: "Show graph" data and the Details API."""

import json
import re
from datetime import datetime, timezone

from flask import g

from app.models import Dashboard, TagSample
from app.models.setting import PUBLIC_HOME_SCREEN, set_setting


def menu_data(html: str) -> dict:
    """Extract the ``#tag-menu-data`` JSON of a screen page.

    Args:
        html: Page HTML.

    Returns:
        The parsed menu data.
    """
    m = re.search(r'<script type="application/json" id="tag-menu-data">(.*?)</script>', html, re.S)
    assert m, "tag menu data missing"
    return json.loads(m.group(1))


def test_grid_screen_menu_data(client, login_as, dashboards, db):
    """Grid screens carry the menu data; history flags come from logging or saved samples."""
    pts = dashboards["points"]
    pts["temp"].log_enabled = True
    db.session.add(TagSample.from_value(pts["speed"].id, datetime.now(timezone.utc), 5))
    db.session.commit()
    login_as("viewer")
    html = client.get("/screens/private").get_data(as_text=True)
    data = menu_data(html)
    assert "hmi-tagmenu.js" in html and data["can_history"] is True
    assert data["points"][str(pts["temp"].id)]["history"] is True
    assert data["points"][str(pts["speed"].id)]["history"] is True
    assert data["points"][str(pts["run"].id)]["history"] is False
    assert data["urls"]["series"] == "/history/api/series" and len(data["urls"]["chartjs"]) == 2


def test_image_screen_menu_data_and_guest(client, login_as, dashboards, db):
    """Image screens include the menu; users without view_history get no graph option."""
    screen = dashboards["public"]
    screen.kind = "image"
    db.session.commit()
    login_as("guest")
    html = client.get("/screens/public").get_data(as_text=True)
    assert 'id="mimic-data"' in html
    data = menu_data(html)
    assert data["can_history"] is False and all(not p["history"] for p in data["points"].values())


def test_details_api(client, login_as, dashboards, db):
    """Details describe the tag, PLC and history; access follows the screens."""
    pts = dashboards["points"]
    pts["temp"].log_enabled = True
    pts["temp"].log_interval = 30
    db.session.add(TagSample.from_value(pts["temp"].id, datetime(2026, 1, 1, tzinfo=timezone.utc), 21.5))
    db.session.commit()
    login_as("viewer")
    d = client.get(f"/api/datapoints/{pts['temp'].id}/details").get_json()
    assert d["label"] == "Temp" and d["plc"] == "PLC1" and d["address"] == "DB1.DBD0"
    assert d["data_type"] == "REAL" and d["unit"] == "°C" and d["endpoint"] == "10.0.0.1:102"
    assert d["screens"] == ["Private"] and d["writable"] is False
    assert d["history"] == {"enabled": True, "interval": 30, "count": 1, "first": d["history"]["first"],
                            "last": d["history"]["last"]}
    assert d["history"]["first"].startswith("2026-01-01T00:00:00")
    name = client.get(f"/api/datapoints/{pts['name'].id}/details").get_json()
    assert name["data_type"] == "STRING[10]"


def test_details_access(client, login_as, dashboards, users, db):
    """Guests cannot see tags of private screens; visitors on public screens get no network address."""
    pts = dashboards["points"]
    login_as("guest")
    assert client.get(f"/api/datapoints/{pts['temp'].id}/details").status_code == 403
    d = client.get(f"/api/datapoints/{pts['pub'].id}/details").get_json()
    assert d["label"] == "Public value" and d["history"] is None
    client.post("/auth/logout")
    set_setting(PUBLIC_HOME_SCREEN, dashboards["public"].id)
    db.session.commit()
    g.pop("_public_home", None)  # the test app context keeps g between requests
    resp = client.get(f"/api/datapoints/{pts['pub'].id}/details")
    d = resp.get_json()
    assert resp.status_code == 200, d
    assert d["endpoint"] is None and d["alarms"] == []
    assert client.get(f"/api/datapoints/{pts['temp'].id}/details").status_code in (401, 403)
    assert client.get("/api/datapoints/9999/details").status_code == 404


def test_dummy_details(client, login_as, db, users):
    """Dummy tags show 'internal' instead of an address and their kind."""
    from app.models import PLC, DataPoint  # noqa: PLC0415
    from app.plc.dummy import DummyAddress  # noqa: PLC0415

    plc = PLC(name="Sim", ip_address="", protocol="dummy")
    screen = Dashboard(name="S", slug="s")
    db.session.add_all([plc, screen])
    db.session.commit()
    tag = DataPoint(plc=plc, label="mine", dummy_scope="local")
    tag.set_address(DummyAddress(start=1, data_type="INT", string_length=0))
    db.session.add(tag)
    screen.add_tag(tag)
    db.session.commit()
    login_as("admin")
    d = client.get(f"/api/datapoints/{tag.id}/details").get_json()
    assert d["address"].startswith("internal") and d["dummy"] == "local" and d["endpoint"] == "no device"
