"""Shared pytest fixtures.

Every test gets a fresh application built by the factory with the
``testing`` configuration (in-memory SQLite, CSRF disabled, simulated PLCs).
"""

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import PLC, Dashboard, DataPoint, Role, User
from app.plc.addressing import parse_address
from app.plc.driver import SimulatedConnection
from app.plc.bacnet import SimulatedBACnetAdapter
from app.plc.modbus import SimulatedModbusAdapter

PASSWORD = "secret-pass-123"


@pytest.fixture()
def app(tmp_path):
    """Create an application with a fresh database and default roles.

    Uploaded images, language files and UI templates go to per-test temporary folders.

    Args:
        tmp_path: pytest temporary directory.

    Yields:
        The Flask application inside an application context.
    """
    SimulatedConnection.reset()
    SimulatedModbusAdapter.reset()
    SimulatedBACnetAdapter.reset()
    app = create_app("testing")
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    app.config["LANGUAGES_FOLDER"] = str(tmp_path / "languages")
    app.config["UI_TEMPLATES_FOLDER"] = str(tmp_path / "ui_templates")
    with app.app_context():
        _db.create_all()
        Role.insert_defaults()
        yield app
        app.extensions["plc_manager"].disconnect_all()
        _db.session.remove()
        _db.drop_all()
        _db.engine.dispose()  # close SQLite connections (avoids ResourceWarning)


@pytest.fixture()
def db(app):
    """Expose the SQLAlchemy object.

    Args:
        app: The application fixture.

    Returns:
        ``flask_sqlalchemy.SQLAlchemy`` instance bound to the test app.
    """
    return _db


@pytest.fixture()
def client(app):
    """Flask test client.

    Args:
        app: The application fixture.

    Returns:
        A :class:`flask.testing.FlaskClient`.
    """
    return app.test_client()


def make_user(username: str, role_name: str, active: bool = True) -> User:
    """Create and commit a user with the given role.

    Args:
        username: Login name.
        role_name: ``Admin``, ``Viewer`` or ``Guest``.
        active: Account status.

    Returns:
        The new :class:`User`.
    """
    role = Role.query.filter_by(name=role_name).one()
    user = User(username=username, password=PASSWORD, role=role, active=active)
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture()
def users(app):
    """Create one user per default role.

    Returns:
        Dict ``{"admin": User, "viewer": User, "guest": User}``.
    """
    return {
        "admin": make_user("admin", "Admin"),
        "viewer": make_user("viewer", "Viewer"),
        "guest": make_user("guest", "Guest"),
    }


def login(client, username: str, password: str = PASSWORD):
    """Log a user in through the real login form.

    Args:
        client: Test client.
        username: Login name.
        password: Password.

    Returns:
        The response of the login POST.
    """
    return client.post("/auth/login", data={"username": username, "password": password})


@pytest.fixture()
def login_as(client, users):
    """Return a helper that logs the client in as one of the fixture users.

    Returns:
        Callable ``login_as("admin"|"viewer"|"guest")``.
    """

    def _login(name: str):
        """Log in as ``name``.

        Args:
            name: Key in the ``users`` fixture.

        Returns:
            The login response.
        """
        return login(client, name)

    return _login


@pytest.fixture()
def plc(db):
    """A PLC row (simulated).

    Returns:
        The :class:`PLC`.
    """
    plc = PLC(name="PLC1", ip_address="10.0.0.1", rack=0, slot=1)
    db.session.add(plc)
    db.session.commit()
    return plc


def add_point(dashboard, plc, label, address, **kwargs) -> DataPoint:
    """Create and commit a data point.

    Args:
        dashboard: Screen to show the tag on (``None`` = tag table only).
        plc: Source PLC.
        label: Label text.
        address: Address string, may include ``:TYPE``.
        **kwargs: Extra DataPoint columns.

    Returns:
        The new :class:`DataPoint`.
    """
    position = kwargs.pop("position", None)
    dp = DataPoint(plc=plc, label=label, **kwargs)
    dp.set_address(parse_address(address))
    _db.session.add(dp)
    if dashboard is not None:
        dashboard.add_tag(dp, position)
    _db.session.commit()
    return dp


@pytest.fixture()
def dashboards(db, plc, users):
    """A private and a public dashboard with a few data points.

    Returns:
        Dict ``{"private": Dashboard, "public": Dashboard, "points": {...}}``.
    """
    private = Dashboard(name="Private", slug="private", created_by=users["admin"])
    public = Dashboard(name="Public", slug="public", is_public=True)
    db.session.add_all([private, public])
    db.session.commit()
    points = {
        "temp": add_point(private, plc, "Temp", "DB1.DBD0:REAL", unit="°C", decimals=1),
        "setpoint": add_point(private, plc, "Setpoint", "DB1.DBD4:REAL", writable=True),
        "speed": add_point(private, plc, "Speed", "DB1.DBW8:INT", writable=True, scale=0.1, decimals=1),
        "run": add_point(private, plc, "Run", "M0.0", writable=True, widget="switch"),
        "name": add_point(private, plc, "Name", "DB1.DBB20:STRING[10]", writable=True),
        "pub": add_point(public, plc, "Public value", "MW10:INT"),
    }
    return {"private": private, "public": public, "points": points}
