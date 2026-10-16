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
