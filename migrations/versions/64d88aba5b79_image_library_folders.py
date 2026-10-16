"""image library folders

Adds ``asset_folders`` (one row per library folder, parents included) and
widens ``assets.category`` – which now holds the folder path such as
``Animations/HVAC`` – from 40 to 200 characters.  Every existing category
becomes a folder.

Revision ID: 64d88aba5b79
Revises: 6d354327b47d
Create Date: 2026-10-15 23:10:00.000000

"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '64d88aba5b79'
down_revision = '6d354327b47d'
branch_labels = None
depends_on = None


def upgrade():
    """Create ``asset_folders``, widen ``assets.category`` and add a folder for each category."""
    folders = op.create_table('asset_folders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('path', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('path')
    )
    with op.batch_alter_table('assets', schema=None) as batch_op:
        batch_op.alter_column('category', existing_type=sa.String(length=40), type_=sa.String(length=200),
                              existing_nullable=False)
        batch_op.create_index(batch_op.f('ix_assets_category'), ['category'], unique=False)

    conn = op.get_bind()
    paths = set()
    for (category,) in conn.execute(sa.text("SELECT DISTINCT category FROM assets")):
        parts = [p.strip() for p in (category or "").split("/") if p.strip()]
        paths.update("/".join(parts[: i + 1]) for i in range(len(parts)))
    now = datetime.now(timezone.utc)
    if paths:
        op.bulk_insert(folders, [{"path": p, "created_at": now} for p in sorted(paths)])


def downgrade():
    """Drop ``asset_folders`` and shorten ``assets.category`` again (long paths are cut)."""
    op.execute("UPDATE assets SET category = substr(category, 1, 40)")
    with op.batch_alter_table('assets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assets_category'))
        batch_op.alter_column('category', existing_type=sa.String(length=200), type_=sa.String(length=40),
                              existing_nullable=False)
    op.drop_table('asset_folders')
