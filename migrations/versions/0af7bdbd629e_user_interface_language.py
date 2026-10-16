"""user interface language

Adds ``users.language`` – the language each user chose for the user
interface (``NULL`` = the default language).

Revision ID: 0af7bdbd629e
Revises: a3f6c2d8e91b
Create Date: 2026-10-16 02:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0af7bdbd629e'
down_revision = 'a3f6c2d8e91b'
branch_labels = None
depends_on = None


def upgrade():
    """Add ``users.language``."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('language', sa.String(length=16), nullable=True))


def downgrade():
    """Remove ``users.language``."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('language')
