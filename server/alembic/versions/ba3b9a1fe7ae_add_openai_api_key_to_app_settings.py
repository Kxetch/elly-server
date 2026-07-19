"""add_openai_api_key_to_app_settings

Revision ID: ba3b9a1fe7ae
Revises: f3efbc673773
Create Date: 2026-07-15 15:52:45.761312

Adds an encrypted column so the OpenAI API key can be configured from
the Settings UI (see domain/llm_client.py) instead of only via a `.env`
var -- same pattern as b1eaf4a5edb9's telegram_bot_token column. A
brand new nullable column with no existing rows to touch, so no data
migration is needed here.

Note: the underlying SQLite column type is plain Text -- EncryptedText
is a Python-side TypeDecorator (transparent encrypt/decrypt), not a
distinct SQL type, so `sa.Text()` here (not the EncryptedText class
itself) matches how every other encrypted column's migration already
does this (see b1eaf4a5edb9, 3e0cec013a14).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba3b9a1fe7ae'
down_revision: Union[str, Sequence[str], None] = 'f3efbc673773'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('app_settings') as batch_op:
        batch_op.add_column(sa.Column('openai_api_key', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('app_settings') as batch_op:
        batch_op.drop_column('openai_api_key')
