"""Check that the Flask-Migrate scripts produce exactly the current models."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade

from app import create_app
from app.extensions import db


def test_migrations_match_models(tmp_path, monkeypatch):
    """``flask db upgrade`` on an empty database yields the model schema."""
    monkeypatch.setenv("TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "m.sqlite"))
    from config import TestingConfig

    monkeypatch.setattr(TestingConfig, "SQLALCHEMY_DATABASE_URI", "sqlite:///" + str(tmp_path / "m.sqlite"))
    app = create_app("testing")
    with app.app_context():
        upgrade()
        with db.engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"render_as_batch": True}), db.metadata)
        db.session.remove()
        db.engine.dispose()
        assert diff == [], diff


def test_dashboard_permissions_renamed_to_screens(tmp_path, monkeypatch):
    """An existing database with the old *dashboard* permission codes keeps every role's permissions."""
    import sqlalchemy as sa  # noqa: PLC0415

    url = "sqlite:///" + str(tmp_path / "old.sqlite")
    from config import TestingConfig  # noqa: PLC0415

    monkeypatch.setattr(TestingConfig, "SQLALCHEMY_DATABASE_URI", url)
    app = create_app("testing")
    with app.app_context():
        upgrade(revision="b8e4f1a6c2d9")  # schema before the rename, with old-style permission rows
        with db.engine.begin() as conn:
            for code in ("manage_dashboards", "view_dashboards", "view_public_dashboards"):
                if not conn.execute(sa.text("SELECT 1 FROM permissions WHERE code = :c"), {"c": code}).first():
                    conn.execute(sa.text("INSERT INTO permissions (code, description) VALUES (:c, '')"), {"c": code})
            conn.execute(sa.text("INSERT INTO roles (name, description) VALUES ('Old viewer', '')"))
            conn.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                "WHERE r.name = 'Old viewer' AND p.code IN ('view_dashboards', 'view_public_dashboards')"))
        upgrade()
        with db.engine.connect() as conn:
            codes = {r[0] for r in conn.execute(sa.text("SELECT code FROM permissions"))}
            viewer = {r[0] for r in conn.execute(sa.text(
                "SELECT p.code FROM permissions p JOIN role_permissions rp ON rp.permission_id = p.id "
                "JOIN roles r ON r.id = rp.role_id WHERE r.name = 'Old viewer'"))}
        db.session.remove()
        db.engine.dispose()
    assert {"manage_screens", "view_screens", "view_public_screens"} <= codes
    assert not codes & {"manage_dashboards", "view_dashboards", "view_public_dashboards"}
    assert viewer == {"view_screens", "view_public_screens"}
