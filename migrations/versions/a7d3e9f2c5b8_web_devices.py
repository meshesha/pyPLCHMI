"""web devices (HTTP/HTTPS and WebSocket, JSON or XML)

* ``plcs.url`` – device URL (``http://…``, ``https://…``, ``ws://…``, ``wss://…``).
* ``plcs.data_format`` – ``json`` or ``xml``.
* ``plcs.poll_interval`` – seconds between requests.
* ``plcs.http_mode`` – ``auto`` or ``http09`` (one-line request for very old devices).
* ``plcs.verify_tls`` – check HTTPS / WSS certificates.
* ``plcs.ws_message`` – text sent to a WebSocket device after connecting.
* ``datapoints.path`` – key path of a web device tag (``meter.power``, ``status/temp``).

Revision ID: a7d3e9f2c5b8
Revises: 8c4bf9b99fd7
Create Date: 2026-10-18 10:00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7d3e9f2c5b8'
down_revision = '8c4bf9b99fd7'
branch_labels = None
depends_on = None


def upgrade():
    """Add the web device columns to ``plcs`` and the key path to ``datapoints``."""
    with op.batch_alter_table('plcs') as batch:
        batch.add_column(sa.Column('url', sa.String(length=500), nullable=False, server_default=''))
        batch.add_column(sa.Column('data_format', sa.String(length=4), nullable=False, server_default='json'))
        batch.add_column(sa.Column('poll_interval', sa.Float(), nullable=False, server_default='1.0'))
        batch.add_column(sa.Column('http_mode', sa.String(length=8), nullable=False, server_default='auto'))
        batch.add_column(sa.Column('verify_tls', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column('ws_message', sa.Text(), nullable=False, server_default=''))
    with op.batch_alter_table('datapoints') as batch:
        batch.add_column(sa.Column('path', sa.String(length=255), nullable=False, server_default=''))


def downgrade():
    """Remove the web device columns."""
    with op.batch_alter_table('datapoints') as batch:
        batch.drop_column('path')
    with op.batch_alter_table('plcs') as batch:
        for name in ('ws_message', 'verify_tls', 'http_mode', 'poll_interval', 'data_format', 'url'):
            batch.drop_column(name)
