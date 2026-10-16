"""tag history and trend charts

* ``datapoints.log_enabled`` / ``datapoints.log_interval`` – save samples of
  a tag every *N* seconds.
* ``tag_samples`` – the saved samples (UTC date and time + value).
* ``trend_charts`` – saved charts (series of logged tags with colours).
* New permission ``view_history``, granted to the *Admin* and *Viewer* roles.

Revision ID: e5b7c1f04a92
Revises: 0af7bdbd629e
Create Date: 2026-10-16 07:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5b7c1f04a92'
down_revision = '0af7bdbd629e'
branch_labels = None
depends_on = None

PERMISSION = "view_history"
DESCRIPTION = "View tag history and trend charts, export history to Excel"


def upgrade():
    """Add the history columns and tables and the ``view_history`` permission."""
    with op.batch_alter_table('datapoints', schema=None) as batch_op:
        batch_op.add_column(sa.Column('log_enabled', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('log_interval', sa.Integer(), server_default='60', nullable=False))

    op.create_table('tag_samples',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('datapoint_id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('value_num', sa.Float(), nullable=True),
    sa.Column('value_text', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['datapoint_id'], ['datapoints.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tag_samples', schema=None) as batch_op:
        batch_op.create_index('ix_tag_samples_tag_ts', ['datapoint_id', 'ts'], unique=False)
        batch_op.create_index(batch_op.f('ix_tag_samples_ts'), ['ts'], unique=False)

    op.create_table('trend_charts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=255), server_default='', nullable=False),
    sa.Column('series_json', sa.Text(), server_default='[]', nullable=False),
    sa.Column('range_key', sa.String(length=10), server_default='1h', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )

    # New permission for the Admin and Viewer roles (other roles: Admin → Roles).
    conn = op.get_bind()
    permissions = sa.table('permissions', sa.column('id', sa.Integer), sa.column('code', sa.String),
                           sa.column('description', sa.String))
    roles = sa.table('roles', sa.column('id', sa.Integer), sa.column('name', sa.String))
    links = sa.table('role_permissions', sa.column('role_id', sa.Integer), sa.column('permission_id', sa.Integer))
    perm_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.code == PERMISSION)).scalar()
    if perm_id is None:
        conn.execute(permissions.insert().values(code=PERMISSION, description=DESCRIPTION))
        perm_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.code == PERMISSION)).scalar()
    for (role_id,) in conn.execute(sa.select(roles.c.id).where(roles.c.name.in_(["Admin", "Viewer"]))).fetchall():
        exists = conn.execute(sa.select(links.c.role_id).where(links.c.role_id == role_id,
                                                               links.c.permission_id == perm_id)).first()
        if exists is None:
            conn.execute(links.insert().values(role_id=role_id, permission_id=perm_id))


def downgrade():
    """Remove the history tables, columns and permission."""
    conn = op.get_bind()
    permissions = sa.table('permissions', sa.column('id', sa.Integer), sa.column('code', sa.String))
    links = sa.table('role_permissions', sa.column('role_id', sa.Integer), sa.column('permission_id', sa.Integer))
    perm_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.code == PERMISSION)).scalar()
    if perm_id is not None:
        conn.execute(links.delete().where(links.c.permission_id == perm_id))
        conn.execute(permissions.delete().where(permissions.c.id == perm_id))

    op.drop_table('trend_charts')
    with op.batch_alter_table('tag_samples', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tag_samples_ts'))
        batch_op.drop_index('ix_tag_samples_tag_ts')
    op.drop_table('tag_samples')
    with op.batch_alter_table('datapoints', schema=None) as batch_op:
        batch_op.drop_column('log_interval')
        batch_op.drop_column('log_enabled')
