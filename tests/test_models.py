"""Tests for the database models."""

import pytest

from app.models import Dashboard, DataPoint, Perm, Role, User
from app.models.dashboard import slugify


def test_password_is_hashed(app, users):
    """Passwords are stored as bcrypt hashes and verified correctly."""
    user = users["viewer"]
    assert user.password_hash.startswith("$2")
    assert user.check_password("secret-pass-123")
    assert not user.check_password("wrong")


def test_password_not_readable(app, users):
    """The ``password`` attribute is write-only."""
    with pytest.raises(AttributeError):
        _ = users["viewer"].password


def test_salts_differ(app, db):
    """Two users with the same password get different hashes."""
    a = User(username="a", password="same-password")
    b = User(username="b", password="same-password")
    assert a.password_hash != b.password_hash


def test_default_roles(app):
    """The three default roles exist with the expected permissions."""
    admin = Role.query.filter_by(name="Admin").one()
    viewer = Role.query.filter_by(name="Viewer").one()
    guest = Role.query.filter_by(name="Guest").one()
    assert admin.permission_codes == set(Perm.all())
    assert viewer.has_permission(Perm.VIEW_SCREENS)
    assert not viewer.has_permission(Perm.WRITE_VALUES)
    assert guest.permission_codes == {Perm.VIEW_PUBLIC_SCREENS}


def test_insert_defaults_keeps_custom_permissions(app, db):
    """Re-seeding keeps customised permissions unless reset is asked."""
    viewer = Role.query.filter_by(name="Viewer").one()
    viewer.set_permissions([Perm.VIEW_SCREENS, Perm.WRITE_VALUES])
    db.session.commit()
    Role.insert_defaults()
    assert viewer.has_permission(Perm.WRITE_VALUES)
    Role.insert_defaults(reset_permissions=True)
    assert not viewer.has_permission(Perm.WRITE_VALUES)


def test_new_user_default_role(app, db):
    """A user created without a role becomes a Viewer."""
    user = User(username="x", password="password1")
    assert user.role.name == "Viewer"


def test_user_can(app, users):
    """``User.can`` follows the role's permissions."""
    assert users["admin"].can(Perm.MANAGE_PLCS)
    assert users["admin"].is_admin
    assert not users["viewer"].can(Perm.MANAGE_PLCS)
    assert not users["guest"].is_admin


def test_can_view_dashboard(app, dashboards, users):
    """Guests see only public dashboards; viewers see all."""
    private, public = dashboards["private"], dashboards["public"]
    assert users["viewer"].can_view_dashboard(private)
    assert users["guest"].can_view_dashboard(public)
    assert not users["guest"].can_view_dashboard(private)


@pytest.mark.parametrize("text,slug", [("Line 1 – Overview", "line-1-overview"), ("!!!", "dashboard"), ("ABC", "abc")])
def test_slugify(text, slug):
    """Names are turned into URL slugs."""
    assert slugify(text) == slug


def test_unique_slug(app, db):
    """Duplicate names get numbered slugs; 'new' is reserved."""
    db.session.add(Dashboard(name="Main", slug="main"))
    db.session.commit()
    assert Dashboard.unique_slug("Main") == "main-2"
    assert Dashboard.unique_slug("New") == "new-dashboard"


def test_datapoint_scaling(app, dashboards):
    """Engineering conversion and its inverse."""
    speed = dashboards["points"]["speed"]  # INT, scale 0.1
    assert speed.to_engineering(1234) == 123.4
    assert speed.to_raw(123.4) == 1234
    with pytest.raises(ValueError):
        speed.to_raw(99999)  # 999990 > INT max


def test_datapoint_format(app, dashboards):
    """Values are formatted with unit and boolean texts."""
    pts = dashboards["points"]
    assert pts["temp"].format_value(21.5) == "21.5 °C"
    assert pts["run"].format_value(True) == "ON"
    assert pts["run"].format_value(None) == "—"
    assert pts["run"].to_raw("false") is False


def test_datapoint_address_roundtrip(app, dashboards):
    """The stored fields produce the canonical address string."""
    pts = dashboards["points"]
    assert pts["temp"].address == "DB1.DBD0"
    assert pts["run"].address == "M0.0"
    assert pts["name"].s7_address.size == 12


def test_cascade_delete(app, db, dashboards):
    """Deleting a screen deletes its tag links; the tags stay in the tag table."""
    from app.models import ScreenTag  # noqa: PLC0415
    db.session.delete(dashboards["private"])
    db.session.commit()
    assert DataPoint.query.count() == 6
    assert ScreenTag.query.count() == 1  # only the public screen's link remains
