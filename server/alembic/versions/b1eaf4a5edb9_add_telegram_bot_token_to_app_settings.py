"""add_telegram_bot_token_to_app_settings

Revision ID: b1eaf4a5edb9
Revises: 3e0cec013a14
Create Date: 2026-07-12 20:20:57.283215

Adds an encrypted column so the Telegram bot token can be configured
from the Settings UI (see telegram_bot/process_manager.py) instead of
only via a `.env` var. A brand new nullable column with no existing
rows to touch, so no data migration is needed here -- unlike
3e0cec013a14, which had to encrypt pre-existing plaintext content.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1eaf4a5edb9'
down_revision: Union[str, Sequence[str], None] = '3e0cec013a14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('app_settings') as batch_op:
        batch_op.add_column(sa.Column('telegram_bot_token', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('app_settings') as batch_op:
        batch_op.drop_column('telegram_bot_token')
