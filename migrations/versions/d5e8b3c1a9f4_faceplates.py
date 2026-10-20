"""faceplates: one template screen for many identical devices

* ``faceplates`` – device type: name, template screen, reference tag prefix.
* ``faceplate_instances`` – one device: name, tag prefix, hand-made bindings (JSON).

Revision ID: d5e8b3c1a9f4
Revises: c4d9a2e7b1f3
Create Date: 2026-10-20 23:45:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5e8b3c1a9f4'
down_revision = 'c4d9a2e7b1f3'
branch_labels = None
depends_on = None


def upgrade():
    """Create ``faceplates`` and ``faceplate_instances``."""
    op.create_table(
        'faceplates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('screen_id', sa.Integer(), nullable=True),
        sa.Column('prefix', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['screen_id'], ['dashboards.id'], name='fk_faceplates_screen_id', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name='pk_faceplates'),
        sa.UniqueConstraint('name', name='uq_faceplates_name'),
    )
    op.create_table(
        'faceplate_instances',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('faceplate_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('prefix', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('description', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('overrides_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['faceplate_id'], ['faceplates.id'], name='fk_faceplate_instances_faceplate_id',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name='pk_faceplate_instances'),
        sa.UniqueConstraint('faceplate_id', 'name', name='uq_faceplate_instances_name'),
    )
    op.create_index('ix_faceplate_instances_faceplate_id', 'faceplate_instances', ['faceplate_id'])


def downgrade():
    """Drop the faceplate tables."""
    op.drop_index('ix_faceplate_instances_faceplate_id', table_name='faceplate_instances')
    op.drop_table('faceplate_instances')
    op.drop_table('faceplates')
