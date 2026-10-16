"""information library and audit log

* ``info_files`` – documents (PDFs, images) with their SHA-1 hash (unique).
* ``plc_files`` – files attached to PLCs / devices.
* ``audit_log`` – who changed what and when, with values before and after.
* New permission ``view_audit``, granted to the *Admin* role.

Revision ID: a7d3e9c2b4f6
Revises: f2c8a4e6b1d3
Create Date: 2026-10-16 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7d3e9c2b4f6'
down_revision = 'f2c8a4e6b1d3'
branch_labels = None
depends_on = None

PERMISSION = "view_audit"
DESCRIPTION = "View the audit log (who changed what and when)"


def upgrade():
    """Create the tables and the ``view_audit`` permission."""
    op.create_table('info_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('original_name', sa.String(length=255), nullable=False),
    sa.Column('filename', sa.String(length=64), nullable=False),
    sa.Column('mimetype', sa.String(length=60), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('sha1', sa.String(length=40), nullable=False),
    sa.Column('description', sa.String(length=500), server_default='', nullable=False),
    sa.Column('uploaded_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['uploaded_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('filename')
    )
    with op.batch_alter_table('info_files', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_info_files_sha1'), ['sha1'], unique=True)

    op.create_table('plc_files',
    sa.Column('plc_id', sa.Integer(), nullable=False),
    sa.Column('file_id', sa.Integer(), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['file_id'], ['info_files.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['plc_id'], ['plcs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('plc_id', 'file_id')
    )

    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('username', sa.String(length=64), server_default='', nullable=False),
    sa.Column('ip', sa.String(length=45), server_default='', nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('object_type', sa.String(length=40), server_default='', nullable=False),
    sa.Column('object_id', sa.Integer(), nullable=True),
    sa.Column('object_label', sa.String(length=200), server_default='', nullable=False),
    sa.Column('changes_json', sa.Text(), server_default='[]', nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_log_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_log_object_type'), ['object_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_log_ts'), ['ts'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_log_user_id'), ['user_id'], unique=False)

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
    """Drop the tables and the permission."""
    conn = op.get_bind()
    permissions = sa.table('permissions', sa.column('id', sa.Integer), sa.column('code', sa.String))
    links = sa.table('role_permissions', sa.column('role_id', sa.Integer), sa.column('permission_id', sa.Integer))
    perm_id = conn.execute(sa.select(permissions.c.id).where(permissions.c.code == PERMISSION)).scalar()
    if perm_id is not None:
        conn.execute(links.delete().where(links.c.permission_id == perm_id))
        conn.execute(permissions.delete().where(permissions.c.id == perm_id))
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_log_user_id'))
        batch_op.drop_index(batch_op.f('ix_audit_log_ts'))
        batch_op.drop_index(batch_op.f('ix_audit_log_object_type'))
        batch_op.drop_index(batch_op.f('ix_audit_log_action'))
    op.drop_table('audit_log')
    op.drop_table('plc_files')
    with op.batch_alter_table('info_files', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_info_files_sha1'))
    op.drop_table('info_files')
