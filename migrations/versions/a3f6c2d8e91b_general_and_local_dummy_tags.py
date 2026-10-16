"""general and local dummy tags

* ``datapoints.dummy_scope`` – ``general`` (default, all existing tags) or
  ``local`` (value kept by each browser window).
* ``dummy_values`` – the values of general dummy tags are now stored in the
  database instead of the web server's memory.

Revision ID: a3f6c2d8e91b
Revises: 64d88aba5b79
Create Date: 2026-10-16 01:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3f6c2d8e91b'
down_revision = '64d88aba5b79'
branch_labels = None
depends_on = None


def upgrade():
    """Add ``datapoints.dummy_scope`` and the ``dummy_values`` table."""
    op.create_table('dummy_values',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('plc_id', sa.Integer(), nullable=False),
    sa.Column('slot', sa.Integer(), nullable=False),
    sa.Column('data_type', sa.String(length=10), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['plc_id'], ['plcs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('plc_id', 'slot', name='uq_dummy_values_plc_slot')
    )
    with op.batch_alter_table('dummy_values', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dummy_values_plc_id'), ['plc_id'], unique=False)
    with op.batch_alter_table('datapoints', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dummy_scope', sa.String(length=10), server_default='general', nullable=False))


def downgrade():
    """Remove ``datapoints.dummy_scope`` and ``dummy_values``."""
    with op.batch_alter_table('datapoints', schema=None) as batch_op:
        batch_op.drop_column('dummy_scope')
    with op.batch_alter_table('dummy_values', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dummy_values_plc_id'))
    op.drop_table('dummy_values')
