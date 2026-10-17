"""rename the dashboard permissions to screen permissions

A "dashboard" is called a *screen* everywhere in the user interface, so the
permission codes follow:

* ``manage_dashboards``      -> ``manage_screens``
* ``view_dashboards``        -> ``view_screens``
* ``view_public_dashboards`` -> ``view_public_screens``

The rows are renamed in place, so every role keeps its permissions.  If a row
with the new code already exists (e.g. ``flask seed`` ran with the new code
before this migration), the role links are moved to it and the old row is
removed.

Revision ID: c3a9d5e7f1b2
Revises: b8e4f1a6c2d9
Create Date: 2026-10-17 10:30:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3a9d5e7f1b2'
down_revision = 'b8e4f1a6c2d9'
branch_labels = None
depends_on = None

#: old code -> (new code, new description)
RENAMES = {
    "manage_dashboards": ("manage_screens", "Create and edit screens and their tags"),
    "view_dashboards": ("view_screens", "Open every screen"),
    "view_public_dashboards": ("view_public_screens", "Open screens marked as public"),
}


def _rename(conn, old: str, new: str, description: str | None) -> None:
    """Rename one permission code, merging into an existing row with the new code.

    Args:
        conn: Database connection.
        old: Current code.
        new: New code.
        description: New description (``None`` = keep).
    """
    old_id = conn.execute(sa.text("SELECT id FROM permissions WHERE code = :c"), {"c": old}).scalar()
    if old_id is None:
        return
    new_id = conn.execute(sa.text("SELECT id FROM permissions WHERE code = :c"), {"c": new}).scalar()
    if new_id is None:
        conn.execute(sa.text("UPDATE permissions SET code = :n WHERE id = :i"), {"n": new, "i": old_id})
        new_id = old_id
    else:  # both exist: move the role links to the new row, drop the old one
        conn.execute(sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) SELECT role_id, :n FROM role_permissions "
            "WHERE permission_id = :o AND role_id NOT IN (SELECT role_id FROM role_permissions WHERE permission_id = :n)"),
            {"n": new_id, "o": old_id})
        conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :o"), {"o": old_id})
        conn.execute(sa.text("DELETE FROM permissions WHERE id = :o"), {"o": old_id})
    if description is not None:
        conn.execute(sa.text("UPDATE permissions SET description = :d WHERE id = :i"), {"d": description, "i": new_id})


def upgrade():
    """Rename the three permission codes."""
    conn = op.get_bind()
    for old, (new, description) in RENAMES.items():
        _rename(conn, old, new, description)
    conn.execute(sa.text("UPDATE permissions SET description = 'Write values to writable tags' "
                         "WHERE code = 'write_values'"))


def downgrade():
    """Restore the old permission codes."""
    conn = op.get_bind()
    for old, (new, _description) in RENAMES.items():
        _rename(conn, new, old, None)
