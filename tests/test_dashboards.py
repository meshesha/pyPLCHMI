"""Tests for dashboard and data point configuration views."""

import pytest

from app.models import Dashboard, DataPoint


def point_data(plc, **overrides):
    """Build POST data for the data point form.

    Args:
        plc: The PLC to use.
        **overrides: Fields to change.

    Returns:
        Form data dict.
    """
    data = {"label": "Level", "plc_id": plc.id, "address": "DB1.DBD0", "data_type": "REAL",
            "string_length": 20, "widget": "value", "unit": "m", "scale": 1, "offset": 0,
            "decimals": 2, "true_text": "ON", "false_text": "OFF", "width": 1, "position": 0}
    data.update(overrides)
    return data


def test_create_dashboard(client, login_as):
    """Creating a dashboard assigns a slug and opens the editor."""
    login_as("admin")
    resp = client.post("/screens/new", data={"name": "Boiler room", "refresh_ms": 1000, "is_public": "y"})
    dash = Dashboard.query.filter_by(slug="boiler-room").one()
    assert dash.is_public and resp.location.endswith(f"/screens/{dash.id}/edit")


def test_refresh_minimum(client, login_as):
    """Refresh intervals below 250 ms are rejected."""
    login_as("admin")
    client.post("/screens/new", data={"name": "Fast", "refresh_ms": 10})
    assert Dashboard.query.filter_by(name="Fast").first() is None


def test_add_point(client, login_as, dashboards, plc):
    """A data point is stored with its parsed address."""
    login_as("admin")
    dash = dashboards["private"]
    resp = client.post(f"/screens/{dash.id}/points/new", data=point_data(plc, address="DB7.DBD12"))
    assert resp.status_code == 302
    dp = DataPoint.query.filter_by(label="Level").one()
    assert (dp.area, dp.db_number, dp.start, dp.data_type) == ("DB", 7, 12, "REAL")


def test_type_suffix_overrides_select(client, login_as, dashboards, plc):
    """``:TYPE`` in the address wins over the drop-down."""
    login_as("admin")
    client.post(f"/screens/{dashboards['private'].id}/points/new",
                data=point_data(plc, address="MD4:DINT", data_type="REAL"))
    assert DataPoint.query.filter_by(label="Level").one().data_type == "DINT"


def test_invalid_address_rejected(client, login_as, dashboards, plc):
    """Inconsistent addresses show a form error."""
    login_as("admin")
    resp = client.post(f"/screens/{dashboards['private'].id}/points/new",
                       data=point_data(plc, address="DB1.DBW0", data_type="REAL"))
    assert b"size letter" in resp.data


def test_widget_validation(client, login_as, dashboards, plc):
    """Indicator needs BOOL; gauge needs min < max; switch must be writable."""
    login_as("admin")
    url = f"/screens/{dashboards['private'].id}/points/new"
    assert b"need a BOOL" in client.post(url, data=point_data(plc, widget="indicator")).data
    assert b"Min &lt; Max" in client.post(url, data=point_data(plc, widget="gauge")).data
    assert b"must be writable" in client.post(url, data=point_data(plc, address="M0.0", data_type="BOOL", widget="switch")).data


def test_edit_point(client, login_as, dashboards, plc):
    """Existing points can be edited; GET pre-fills the form."""
    login_as("admin")
    dp = dashboards["points"]["temp"]
    url = f"/screens/{dashboards['private'].id}/points/{dp.id}/edit"
    assert b"DB1.DBD0" in client.get(url).data
    client.post(url, data=point_data(plc, label="Temp 2", address="DB1.DBD0:REAL"))
    assert DataPoint.query.get(dp.id).label == "Temp 2"


def test_point_of_other_dashboard_404(client, login_as, dashboards):
    """A point cannot be edited through another dashboard's URL."""
    login_as("admin")
    dp = dashboards["points"]["pub"]
    assert client.get(f"/screens/{dashboards['private'].id}/points/{dp.id}/edit").status_code == 404


def test_move_duplicate_delete(client, login_as, dashboards):
    """Reordering, duplicating and deleting points."""
    login_as("admin")
    dash = dashboards["private"]
    first, second = dash.datapoints[0], dash.datapoints[1]
    client.post(f"/screens/{dash.id}/points/{second.id}/move/up")
    assert dash.datapoints[0].id == second.id
    count = len(dash.datapoints)
    client.post(f"/screens/{dash.id}/points/{first.id}/duplicate")
    assert len(Dashboard.query.get(dash.id).datapoints) == count + 1
    client.post(f"/screens/{dash.id}/points/{first.id}/delete")
    db_dash = Dashboard.query.get(dash.id)
    assert not db_dash.has_tag(first.id)                  # removed from the screen…
    assert DataPoint.query.get(first.id) is not None      # …but kept in the tag table


def test_delete_dashboard(client, login_as, dashboards):
    """Dashboards can be deleted."""
    login_as("admin")
    client.post(f"/screens/{dashboards['public'].id}/delete")
    assert Dashboard.query.filter_by(slug="public").first() is None


def test_view_renders_tiles(client, login_as, dashboards):
    """The run-time screen contains one tile per data point."""
    login_as("viewer")
    html = client.get("/screens/private").data.decode()
    assert html.count('class="hmi-tile span') == len(dashboards["private"].datapoints)
    assert "hmi-write" not in html  # viewer has no write permission


class TestDisabledPLCs:
    """Disabled PLCs must not be offered in PLC selection lists."""

    @pytest.fixture()
    def plcs(self, db, plc):
        """One enabled (``PLC1``) and one disabled (``OldPLC``) PLC.

        Returns:
            Tuple ``(enabled, disabled)``.
        """
        from app.models import PLC

        off = PLC(name="OldPLC", ip_address="10.0.0.99", enabled=False)
        db.session.add(off)
        db.session.commit()
        return plc, off

    def test_new_point_form_hides_disabled(self, client, login_as, dashboards, plcs):
        """The add-data-point form lists only enabled PLCs."""
        login_as("admin")
        html = client.get(f"/screens/{dashboards['private'].id}/points/new").data.decode()
        assert "PLC1" in html and "OldPLC" not in html

    def test_posting_disabled_plc_rejected(self, client, login_as, dashboards, plcs):
        """A disabled PLC id posted by hand is refused."""
        login_as("admin")
        resp = client.post(f"/screens/{dashboards['private'].id}/points/new", data=point_data(plcs[1]))
        assert resp.status_code == 200 and b"Not a valid choice" in resp.data
        assert DataPoint.query.filter_by(label="Level").first() is None

    def test_existing_point_keeps_its_disabled_plc(self, client, login_as, db, dashboards, plcs):
        """Editing a point on a now-disabled PLC keeps that PLC (marked) instead of switching."""
        login_as("admin")
        dp = dashboards["points"]["temp"]
        dp.plc_id = plcs[1].id
        db.session.commit()
        url = f"/screens/{dashboards['private'].id}/points/{dp.id}/edit"
        assert "OldPLC (10.0.0.99:102) (disabled)" in client.get(url).data.decode()
        client.post(url, data=point_data(plcs[1], label="Temp kept", address="DB1.DBD0:REAL"))
        assert DataPoint.query.get(dp.id).plc_id == plcs[1].id
        # ...but other points still cannot pick it
        other = dashboards["points"]["speed"]
        html = client.get(f"/screens/{dashboards['private'].id}/points/{other.id}/edit").data.decode()
        assert "OldPLC" not in html

    def test_no_enabled_plc_message(self, client, login_as, db, dashboards, plcs):
        """With every PLC disabled, adding a point explains why."""
        plcs[0].enabled = False
        db.session.commit()
        login_as("admin")
        resp = client.get(f"/screens/{dashboards['private'].id}/points/new", follow_redirects=True)
        assert b"No enabled PLC" in resp.data

    def test_designer_and_tag_api_hide_disabled(self, client, login_as, db, plcs):
        """The designer's New-tag dialog lists only enabled PLCs and the API refuses disabled ones."""
        login_as("admin")
        dash = Dashboard(name="Img", slug="img", kind="image")
        db.session.add(dash)
        db.session.commit()
        html = client.get(f"/screens/{dash.id}/designer").data.decode()
        assert "PLC1" in html and "OldPLC" not in html
        resp = client.post(f"/api/dashboards/{dash.id}/datapoints",
                           json={"label": "x", "plc_id": plcs[1].id, "address": "M0.0", "data_type": "BOOL"})
        assert resp.status_code == 400 and "plc_id" in resp.get_json()["errors"]
