"""automations

* ``automations`` – "when … and if … then do …" rules (triggers, conditions and
  actions as JSON), mode, and the administrator's approval for writing tags.
* ``automation_runs`` – run history with a step-by-step trace.
* ``alarm_events.source`` – ``alarm`` or ``automation`` (notifications sent by
  automations; they close when acknowledged).
* New permission ``manage_automations``, granted to the *Admin* role.

Revision ID: 8c4bf9b99fd7
Revises: c3a9d5e7f1b2
Create Date: 2026-10-17 15:00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8c4bf9b99fd7'
down_revision = 'c3a9d5e7f1b2'
branch_labels = None
depends_on = None

PERMISSION = "manage_automations"
DESCRIPTION = "Create, edit, run and delete automations"


def upgrade():
    """Create the automation tables, ``alarm_events.source`` and the ``manage_automations`` permission."""
    op.create_table('automations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=500), server_default='', nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('mode', sa.String(length=10), server_default='single', nullable=False),
    sa.Column('max_runs', sa.Integer(), server_default='10', nullable=False),
    sa.Column('config_json', sa.Text(), nullable=False),
    sa.Column('allow_writes', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_triggered_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('automation_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('automation_id', sa.Integer(), nullable=False),
    sa.Column('trigger', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('error', sa.String(length=500), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('trace_json', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['automation_id'], ['automations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('automation_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_automation_runs_automation_id'), ['automation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_automation_runs_started_at'), ['started_at'], unique=False)

    with op.batch_alter_table('alarm_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=20), server_default='alarm', nullable=False))

    conn = op.get_bind()
    permissions = sa.table('permissions', sa.column('id', sa.Integer), sa.column('code', sa.String),
                           sa.column('description', sa.String))
    roles = sa.table('roles', sa.column('id', sa.Integer), sa.column('name', sa.String))
    links = sa.table('role_permissions', sa.column('role_id', sa.Integer), sa.column('permission_id', sa.Integer))
    perm_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.code == PERMISSION)).scalar()
    if perm_id is None:
        conn.execute(permissions.insert().values(code=PERMISSION, description=DESCRIPTION))
        perm_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.code == PERMISSION)).scalar()
    for (role_id,) in conn.execute(sa.select(roles.c.id).where(roles.c.name == "Admin")).fetchall():
        exists = conn.execute(sa.select(links.c.role_id).where(links.c.role_id == role_id,
                                                               links.c.permission_id == perm_id)).first()
        if exists is None:
            conn.execute(links.insert().values(role_id=role_id, permission_id=perm_id))


def downgrade():
    """Drop the tables, the column and the permission."""
    conn = op.get_bind()
    permissions = sa.table('permissions', sa.column('id', sa.Integer), sa.column('code', sa.String))
    links = sa.table('role_permissions', sa.column('role_id', sa.Integer), sa.column('permission_id', sa.Integer))
    perm_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.code == PERMISSION)).scalar()
    if perm_id is not None:
        conn.execute(links.delete().where(links.c.permission_id == perm_id))
        conn.execute(permissions.delete().where(permissions.c.id == perm_id))
    with op.batch_alter_table('alarm_events', schema=None) as batch_op:
        batch_op.drop_column('source')

    with op.batch_alter_table('automation_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_automation_runs_started_at'))
        batch_op.drop_index(batch_op.f('ix_automation_runs_automation_id'))

    op.drop_table('automation_runs')
    op.drop_table('automations')
