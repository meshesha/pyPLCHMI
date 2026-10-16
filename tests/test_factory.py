"""Tests for the application factory and configuration classes."""

import pytest

from app import create_app


def test_testing_config(app):
    """The testing config uses an in-memory DB and simulation."""
    assert app.testing
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite://"
    assert app.config["PLC_SIMULATION"] is True


def test_blueprints_registered(app):
    """All feature blueprints are attached."""
    assert {"auth", "main", "admin", "dashboards", "api"} <= set(app.blueprints)


def test_plc_manager_created(app):
    """The factory creates a PLC connection manager in simulation mode."""
    manager = app.extensions["plc_manager"]
    assert manager.simulation is True


def test_two_apps_are_independent():
    """Each factory call returns a new, separate application."""
    a, b = create_app("testing"), create_app("testing")
    assert a is not b  # (no DB connection is opened, nothing to dispose)
    assert a.extensions["plc_manager"] is not b.extensions["plc_manager"]


def test_production_requires_secret_key(monkeypatch):
    """Production refuses to start with the default secret key."""
    from config import ProductionConfig

    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "dev-insecure-change-me")
    with pytest.raises(RuntimeError):
        create_app("production")


def test_production_with_secret(monkeypatch):
    """Production starts when a real secret key is configured."""
    from config import ProductionConfig

    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "x" * 40)
    monkeypatch.setattr(ProductionConfig, "SQLALCHEMY_DATABASE_URI", "sqlite://")
    app = create_app("production")
    assert not app.debug


def test_cli_seed_and_create_admin(app):
    """``flask seed`` and ``flask create-admin`` work."""
    from app.models import User

    runner = app.test_cli_runner()
    assert "up to date" in runner.invoke(args=["seed"]).output
    result = runner.invoke(args=["create-admin", "--username", "boss", "--password", "longpassword"])
    assert "created" in result.output
    assert User.query.filter_by(username="boss").one().is_admin
    dup = runner.invoke(args=["create-admin", "--username", "boss", "--password", "longpassword"])
    assert dup.exit_code != 0
    again = runner.invoke(args=["create-admin", "--username", "boss", "--password", "otherpassword", "--if-missing"])
    assert again.exit_code == 0 and "not changed" in again.output
    assert User.query.filter_by(username="boss").one().check_password("longpassword")
    env = runner.invoke(args=["create-admin", "--username", "chief", "--if-missing"],
                        env={"HMI_ADMIN_PASSWORD": "from-env-pass"})
    assert env.exit_code == 0 and User.query.filter_by(username="chief").one().check_password("from-env-pass")


def test_cli_demo(app):
    """``flask demo`` creates a dashboard with data points."""
    from app.models import Dashboard

    runner = app.test_cli_runner()
    runner.invoke(args=["demo"])
    dash = Dashboard.query.filter_by(slug="demo-overview").one()
    assert len(dash.datapoints) >= 5
