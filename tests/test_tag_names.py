"""Tests for the tag name rules: unique, letters/digits/_/. only, not starting with a digit."""

import io

import pytest
from openpyxl import Workbook

from app.models import DataPoint
from app.tags.naming import check, normalize, problems, suggest
from tests.conftest import add_point
from tests.test_tag_features import tag_form


@pytest.mark.parametrize("name", ["Tank_1", "tank_1.level", "_x", "T1", "a.b.c", "משאבה_1", "Pump.Speed2"])
def test_valid_names(app, name):
    """Letters (any language), digits, _ and dots between parts are fine."""
    assert problems(name) == []


@pytest.mark.parametrize("name, words", [
    ("", "Enter a tag name"),
    ("Tank 1", "cannot contain spaces"),
    ("1tank", "cannot start with a digit"),
    ("a+b", "cannot contain +"),
    ("flow-rate", "cannot contain -"),
    ("x*y/z", "cannot contain * /"),
    ("ratio%", "cannot contain %"),
    ("a=b", "cannot contain ="),
    ("[x]", "cannot contain [ ]"),
    ("a,b", "cannot contain ,"),
    (".level", "A dot must stand between"),
    ("level.", "A dot must stand between"),
    ("a..b", "A dot must stand between"),
    ("x" * 101, "too long"),
])
def test_invalid_names_explain_why(app, name, words):
    """Every rule has its own instruction."""
    assert any(words in p for p in problems(name)), problems(name)


def test_unique_case_insensitive_and_legacy(app, db, plc):
    """Duplicates are refused (case ignored); old names stay allowed until changed."""
    add_point(None, plc, "Pump_1", "DB1.DBW0:INT")
    legacy = add_point(None, plc, "Old name", "DB1.DBW2:INT")        # saved before the rules
    assert any("already used by another tag" in p for p in check("pump_1"))
    assert check("Old name", legacy) == []                           # unchanged: allowed
    assert any("spaces" in p for p in check("Old name 2", legacy))   # changed: rules apply
    assert check("Pump_2") == []
    assert suggest("Pump 1") == "Pump_1_2" and normalize("3 Tank (°C)") == "T3_Tank_C"
    assert legacy.name_issues


def test_tag_form_blocks_and_explains(client, login_as, db, plc):
    """The tag form refuses bad names with the instructions; the live check gives the same messages."""
    add_point(None, plc, "Speed", "DB1.DBW8:INT")
    login_as("admin")
    html = client.post("/tags/new", data=tag_form(plc, label="2 speed+x")).get_data(as_text=True)
    assert "cannot start with a digit" in html and "cannot contain spaces" in html and "cannot contain +" in html
    html = client.post("/tags/new", data=tag_form(plc, label="SPEED")).get_data(as_text=True)
    assert "already used by another tag" in html
    assert DataPoint.query.count() == 1
    client.post("/tags/new", data=tag_form(plc, label="Speed_2"))
    assert DataPoint.query.filter_by(label="Speed_2").count() == 1
    r = client.get("/api/tags/check-name?name=speed").get_json()
    assert r["ok"] is False and "already used" in r["problems"][0]
    own = DataPoint.query.filter_by(label="Speed").one()
    assert client.get(f"/api/tags/check-name?name=Speed&id={own.id}").get_json()["ok"] is True
    assert client.get("/api/tags/check-name?name=Good_name").get_json() == {"ok": True, "problems": []}


def test_legacy_name_warning_and_edit(client, login_as, db, plc):
    """Old names are marked in the tag table and the form; saving other fields still works."""
    old = add_point(None, plc, "Old name", "DB1.DBW2:INT")
    login_as("admin")
    assert "tag-name-legacy" in client.get("/tags/").get_data(as_text=True)
    assert 'id="tag-name-legacy"' in client.get(f"/tags/{old.id}/edit").get_data(as_text=True)
    client.post(f"/tags/{old.id}/edit", data=tag_form(plc, label="Old name", address="DB1.DBW2", unit="bar"))
    assert db.session.get(DataPoint, old.id).unit == "bar"
    html = client.post(f"/tags/{old.id}/edit", data=tag_form(plc, label="Old name 3", address="DB1.DBW2")).get_data(as_text=True)
    assert "cannot contain spaces" in html


def test_designer_and_screen_forms(client, login_as, db, plc, dashboards):
    """The designer's New tag dialog and the screen tag form use the same rules."""
    login_as("admin")
    screen = dashboards["private"]
    r = client.post(f"/api/dashboards/{screen.id}/datapoints", json={"label": "Bad name", "plc_id": plc.id,
                                                                      "address": "DB3.DBW0", "data_type": "INT"})
    assert r.status_code == 400 and "spaces" in str(r.get_json()["errors"]["label"])
    r = client.post(f"/api/dashboards/{screen.id}/datapoints", json={"label": "Good_name", "plc_id": plc.id,
                                                                      "address": "DB3.DBW0", "data_type": "INT"})
    assert r.status_code == 201


def test_excel_import_reports_bad_names(client, login_as, db, plc):
    """Rows with invalid or duplicate names are errors; nothing is saved."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Tags"
    ws.append(["Label", "PLC", "Address", "Data type"])
    ws.append(["Good_one", plc.name, "DB1.DBW0", "INT"])
    ws.append(["bad name", plc.name, "DB1.DBW2", "INT"])
    ws.append(["GOOD_ONE", plc.name, "DB1.DBW4", "INT"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    login_as("admin")
    html = client.post("/tags/import", data={"file": (buf, "t.xlsx")}, content_type="multipart/form-data",
                       follow_redirects=True).get_data(as_text=True)
    assert "cannot contain spaces" in html and "already used by another tag" in html
    assert DataPoint.query.count() == 0
