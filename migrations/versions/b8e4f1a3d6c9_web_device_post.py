"""web devices: POST requests and extra headers

* ``plcs.http_method`` – ``GET`` or ``POST``.
* ``plcs.http_body`` – body sent with ``POST`` (parameters, JSON / XML command).
* ``plcs.http_content_type`` – content type of the body.
* ``plcs.http_headers`` – extra request headers (``Name: value`` per line).

Revision ID: b8e4f1a3d6c9
Revises: a7d3e9f2c5b8
Create Date: 2026-10-18 20:30:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8e4f1a3d6c9'
down_revision = 'a7d3e9f2c5b8'
branch_labels = None
depends_on = None


def upgrade():
    """Add the request method, body, content type and extra headers of web devices."""
    with op.batch_alter_table('plcs') as batch:
        batch.add_column(sa.Column('http_method', sa.String(length=6), nullable=False, server_default='GET'))
        batch.add_column(sa.Column('http_body', sa.Text(), nullable=False, server_default=''))
        batch.add_column(sa.Column('http_content_type', sa.String(length=100), nullable=False,
                                   server_default='application/x-www-form-urlencoded'))
        batch.add_column(sa.Column('http_headers', sa.Text(), nullable=False, server_default=''))


def downgrade():
    """Remove the columns."""
    with op.batch_alter_table('plcs') as batch:
        for name in ('http_headers', 'http_content_type', 'http_body', 'http_method'):
            batch.drop_column(name)
