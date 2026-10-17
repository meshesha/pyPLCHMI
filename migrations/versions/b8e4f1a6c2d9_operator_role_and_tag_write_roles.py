"""operator role and per-tag write roles

* ``datapoints.write_access`` – ``all`` (everyone allowed to write values) or
  ``roles`` (only the roles linked in ``datapoint_write_roles``).
* ``datapoint_write_roles`` – tag ↔ role links.
* The built-in **Operator** role (screens, trends, write values) for existing
  installations.  A fresh database gets it from ``flask seed`` instead, because
  its permission rows do not exist yet when the migrations run.

Revision ID: b8e4f1a6c2d9
Revises: a7d3e9c2b4f6
Create Date: 2026-10-17 10:00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8e4f1a6c2d9'
down_revision = 'a7d3e9c2b4f6'
branch_labels = None
depends_on = None

#: Permissions of the built-in Operator role (see app.models.user.DEFAULT_ROLES).
OPERATOR_PERMISSIONS = ("view_dashboards", "view_public_dashboards", "view_history", "write_values")


def upgrade():
    """Add the write-access column and table, and the Operator role."""
    with op.batch_alter_table('datapoints', schema=None) as batch_op:
        batch_op.add_column(sa.Column('write_access', sa.String(length=10), nullable=False, server_default='all'))
    op.create_table(
        'datapoint_write_roles',
        sa.Column('datapoint_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['datapoint_id'], ['datapoints.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('datapoint_id', 'role_id'),
    )

    conn = op.get_bind()
    perms = conn.execute(sa.text("SELECT id, code FROM permissions")).fetchall()
    exists = conn.execute(sa.text("SELECT id FROM roles WHERE name = 'Operator'")).fetchone()
    if perms and exists is None:
        conn.execute(sa.text("INSERT INTO roles (name, description) VALUES ('Operator', :d)"),
                     {"d": "Operates the plant: all screens and trends, writes values to writable tags"})
        role_id = conn.execute(sa.text("SELECT id FROM roles WHERE name = 'Operator'")).scalar()
        for perm_id, code in perms:
            if code in OPERATOR_PERMISSIONS:
                conn.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"),
                             {"r": role_id, "p": perm_id})


def downgrade():
    """Remove the write-access table and column (the Operator role is kept, it may have users)."""
    op.drop_table('datapoint_write_roles')
    with op.batch_alter_table('datapoints', schema=None) as batch_op:
        batch_op.drop_column('write_access')
