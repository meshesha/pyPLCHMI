"""user colour theme

Adds ``users.theme`` – the colour theme each user chose (``dark``, ``light``
or ``auto``; ``NULL`` = dark).

Revision ID: f2c8a4e6b1d3
Revises: e5b7c1f04a92
Create Date: 2026-10-16 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2c8a4e6b1d3'
down_revision = 'e5b7c1f04a92'
branch_labels = None
depends_on = None


def upgrade():
    """Add ``users.theme``."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('theme', sa.String(length=10), nullable=True))


def downgrade():
    """Remove ``users.theme``."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('theme')
