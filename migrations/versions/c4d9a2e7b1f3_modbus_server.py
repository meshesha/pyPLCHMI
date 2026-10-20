"""Modbus TCP server: tags published to other SCADA systems

* ``modbus_exports`` – one row per published tag: area (``reg``/``bit``),
  0-based address, Modbus type, scale, STRING length and whether other SCADA
  systems may write the tag (``writable``).  The server settings
  are stored in ``settings`` (keys ``modbus_server.*``).

Revision ID: c4d9a2e7b1f3
Revises: b8e4f1a3d6c9
Create Date: 2026-10-19 21:00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d9a2e7b1f3'
down_revision = 'b8e4f1a3d6c9'
branch_labels = None
depends_on = None


def upgrade():
    """Create ``modbus_exports``."""
    op.create_table(
        'modbus_exports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('datapoint_id', sa.Integer(), nullable=False),
        sa.Column('area', sa.String(length=3), nullable=False),
        sa.Column('address', sa.Integer(), nullable=False),
        sa.Column('mb_type', sa.String(length=8), nullable=False),
        sa.Column('scale', sa.Float(), nullable=False),
        sa.Column('string_length', sa.Integer(), nullable=False),
        sa.Column('writable', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['datapoint_id'], ['datapoints.id'], name='fk_modbus_exports_datapoint',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name='pk_modbus_exports'),
        sa.UniqueConstraint('datapoint_id', name='uq_modbus_exports_datapoint_id'),
    )


def downgrade():
    """Drop ``modbus_exports``."""
    op.drop_table('modbus_exports')
