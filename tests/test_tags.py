"""Tests for the tag table (/tags) and the shared table helper."""

import re

import pytest
from werkzeug.datastructures import MultiDict

from app.models import Alarm, Dashboard, DataPoint
from app.plc.addressing import parse_address
from app.utils.tables import PAGE_SIZES, TableState, parse_state, table_page
from tests.conftest import add_point


# ---------------------------------------------------------------- table helper
def test_parse_state_validates_everything():
    """Unknown sort keys, directions, page sizes and pages fall back to defaults."""
    state = parse_state(MultiDict({"sort": "evil", "dir": "sideways", "per_page": "7", "page": "-3",
                                   "q": "  x  ", "plc": " 2 ", "other": "1"}),
                        {"label": None}, "label", filter_names=("plc", "screen"))
    assert (state.sort, state.direction, state.per_page, state.page, state.q) == ("label", "asc", 25, 1, "x")
    assert state.filters == {"plc": "2"}
    state = parse_state(MultiDict({"per_page": "100", "page": "abc", "dir": "desc"}), {"a": None}, "a")
    assert state.per_page == 100 and state.page == 1 and state.direction == "desc"
    assert 10 in PAGE_SIZES and 100 in PAGE_SIZES


def test_state_args_and_sort_toggle():
    """``sort_args`` toggles the direction for the active column and resets the page."""
    state = TableState(q="pump", sort="label", direction="asc", page=3, per_page=10, filters={"plc": "1"})
    assert state.sort_args("label") == {"q": "pump", "sort": "label", "dir": "desc", "page": 1, "per_page": 10, "plc": "1"}
    assert state.sort_args("type")["dir"] == "asc"
    assert "q" not in state.args(q=None)


def test_table_page_clamps_to_last_page(app, dashboards):
    """Asking for a page beyond the end shows the last page."""
    state = TableState(sort="label", per_page=10, page=99)
    page = table_page(DataPoint.query, state, {"label": DataPoint.label}, (DataPoint.label,))
    assert page.page == 1 and state.page == 1 and page.total == 6


# ---------------------------------------------------------------- permissions
@pytest.mark.parametrize("who", ["viewer", "guest"])
def test_tags_admin_only(client, login_as, dashboards, who):
    """Viewers and guests get 403 on all tag pages and the values API."""
    login_as(who)
    tag = dashboards["points"]["temp"]
    assert client.get("/tags/").status_code == 403
    assert client.get("/tags/new").status_code == 403
    assert client.get(f"/tags/{tag.id}/edit").status_code == 403
    assert client.post(f"/tags/{tag.id}/delete").status_code == 403
    assert client.get(f"/api/tags/values?ids={tag.id}").status_code == 403


def test_tags_requires_login(client, dashboards):
    """Anonymous visitors are sent to the login page."""
    resp = client.get("/tags/")
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_admin_nav_links(client, login_as, users):
    """Admins see Tag table and Alarms in the Admin menu; viewers do not."""
    login_as("admin")
    html = client.get("/screens/").get_data(as_text=True)
    assert 'id="nav-tags"' in html and 'id="nav-alarms"' in html and 'id="events-btn"' in html
    client.post("/auth/logout")
    login_as("viewer")
    html = client.get("/screens/").get_data(as_text=True)
    assert 'id="nav-tags"' not in html and 'id="events-btn"' in html


# ---------------------------------------------------------------- list
def _labels(html: str) -> list[str]:
    """Tag labels in table order.

    Args:
        html: Page HTML.

    Returns:
        List of labels.
    """
    return re.findall(r'<tr data-tag-id="\d+">.*?<td><strong>([^<]+)</strong>', html, re.S)


def test_list_search_sort_filter_paginate(client, login_as, dashboards, plc, db):
    """Search, sorting, PLC/screen/writable filters and page size work together."""
    login_as("admin")
    add_point(None, plc, "Loose tag", "MW40:INT")
    html = client.get("/tags/").get_data(as_text=True)
    assert _labels(html) == sorted(_labels(html), key=str.lower) and len(_labels(html)) == 7
    assert "Loose tag" in html and 'data-tag-value="' in html

    assert _labels(client.get("/tags/?q=speed").get_data(as_text=True)) == ["Speed"]
    assert _labels(client.get("/tags/?q=DB1.DBD4").get_data(as_text=True)) == ["Setpoint"]   # by address
    assert _labels(client.get("/tags/?q=public").get_data(as_text=True)) == ["Public value"]  # by screen name

    desc = _labels(client.get("/tags/?sort=label&dir=desc").get_data(as_text=True))
    assert desc == sorted(desc, key=str.lower, reverse=True)

    assert _labels(client.get("/tags/?screen=none").get_data(as_text=True)) == ["Loose tag"]
    pub = dashboards["public"].id
    assert _labels(client.get(f"/tags/?screen={pub}").get_data(as_text=True)) == ["Public value"]
    writable = _labels(client.get("/tags/?writable=yes").get_data(as_text=True))
    assert set(writable) == {"Setpoint", "Speed", "Run", "Name"}

    page1 = client.get("/tags/?per_page=10&sort=label").get_data(as_text=True)
    assert "1–7 of 7" in page1
    for i in range(8):
        add_point(None, plc, f"Extra {i:02d}", f"MW{100 + 2 * i}:INT")
    page2 = client.get("/tags/?per_page=10&sort=label&page=2").get_data(as_text=True)
    assert "11–15 of 15" in page2 and len(_labels(page2)) == 5
    # Sorting links keep the search/filter state.
    html = client.get("/tags/?q=extra&writable=no").get_data(as_text=True)
    assert re.search(r'href="/tags/\?[^"]*q=extra[^"]*writable=no', html.replace("&amp;", "&"))


def test_live_values_api(client, login_as, dashboards):
    """The values API returns readings for the requested ids only."""
    login_as("admin")
    pts = dashboards["points"]
    data = client.get(f"/api/tags/values?ids={pts['temp'].id},{pts['run'].id},x").get_json()
    assert set(data["values"]) == {str(pts["temp"].id), str(pts["run"].id)}
    assert data["values"][str(pts["temp"].id)]["quality"] == "good"


# ---------------------------------------------------------------- CRUD
def test_create_tag_without_screen(client, login_as, plc, db):
    """A tag can be created without a screen and gets its address text."""
    login_as("admin")
    resp = client.post("/tags/new", data={
        "label": "Pressure", "plc_id": plc.id, "address": "DB2.DBD10", "data_type": "REAL",
        "widget": "value", "width": 1, "scale": 1, "offset": 0, "decimals": 2,
        "true_text": "ON", "false_text": "OFF"})
    assert resp.status_code == 302, resp.get_data(as_text=True)
    tag = DataPoint.query.filter_by(label="Pressure").one()
    assert tag.screens == [] and tag.address_text == "DB2.DBD10"


def test_create_tag_on_screens_and_change(client, login_as, dashboards, plc, db):
    """Ticked screens show the tag (at the end); unticking removes it from that screen only."""
    login_as("admin")
    pub, private = dashboards["public"], dashboards["private"]
    base = {"label": "Flow", "plc_id": plc.id, "address": "DB3.DBW0", "data_type": "INT", "widget": "value",
            "width": 1, "scale": 1, "offset": 0, "decimals": 0, "true_text": "ON", "false_text": "OFF"}
    assert client.post("/tags/new", data={**base, "screens": [pub.id, private.id]}).status_code == 302
    tag = DataPoint.query.filter_by(label="Flow").one()
    assert {s.id for s in tag.screens} == {pub.id, private.id}
    assert db.session.get(Dashboard, pub.id).datapoints[-1].id == tag.id
    html = client.get(f"/tags/{tag.id}/edit").get_data(as_text=True)
    assert re.search(rf'<input[^>]*checked[^>]*value="{pub.id}"|<input[^>]*value="{pub.id}"[^>]*checked', html)
    assert client.post(f"/tags/{tag.id}/edit", data={**base, "screens": [private.id]}).status_code == 302
    db.session.expire_all()
    tag = db.session.get(DataPoint, tag.id)
    assert [s.id for s in tag.screens] == [private.id]
    assert client.post(f"/tags/{tag.id}/edit", data=base).status_code == 302
    db.session.expire_all()
    assert db.session.get(DataPoint, tag.id).screens == []


def test_create_tag_invalid_address(client, login_as, plc):
    """Invalid addresses are reported on the form."""
    login_as("admin")
    resp = client.post("/tags/new", data={"label": "Bad", "plc_id": plc.id, "address": "XYZ",
                                          "data_type": "INT", "widget": "value", "width": 1, "position": 0,
                                          "scale": 1, "offset": 0, "decimals": 0})
    assert resp.status_code == 200 and "is-invalid" in resp.get_data(as_text=True)


def test_new_tag_without_plc_redirects(client, login_as):
    """Without an enabled PLC the form is not offered."""
    login_as("admin")
    resp = client.get("/tags/new")
    assert resp.status_code == 302


def test_delete_tag_and_protection(client, login_as, dashboards, db):
    """Tags are deleted unless an alarm uses them."""
    login_as("admin")
    pts = dashboards["points"]
    assert client.post(f"/tags/{pts['name'].id}/delete").status_code == 302
    assert db.session.get(DataPoint, pts["name"].id) is None

    db.session.add(Alarm(name="Hot", datapoint=pts["temp"], operator="gt", setpoint="50"))
    db.session.commit()
    resp = client.post(f"/tags/{pts['temp'].id}/delete", follow_redirects=True)
    assert "used by alarm" in resp.get_data(as_text=True)
    assert db.session.get(DataPoint, pts["temp"].id) is not None


def test_address_text_backfill(client, login_as, dashboards, db):
    """Tags without address text get it filled in when the table is opened."""
    login_as("admin")
    tag = dashboards["points"]["speed"]
    tag.address_text = ""
    db.session.commit()
    client.get("/tags/")
    db.session.refresh(tag)
    assert tag.address_text == "DB1.DBW8"


def test_set_address_updates_text(dashboards):
    """``set_address`` keeps ``address_text`` in sync."""
    tag = dashboards["points"]["temp"]
    tag.set_address(parse_address("DB9.DBD12:REAL"))
    assert tag.address_text == "DB9.DBD12"
