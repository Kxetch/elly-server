"""add_budget_entries

Revision ID: f99c14eb9d34
Revises: b1eaf4a5edb9
Create Date: 2026-07-13 01:46:49.964888

Schema-only migration: the new `budget_entries` table (the Budget page's
income/expense log, replacing "finance"-labelled habits), a
`budget_entry_id` FK on `events` (mirrors the existing `habit_id` FK --
a recurring budget entry generates calendar events the same way a
recurring habit does), and `app_settings.currency` (one global
ISO 4217 currency code for the whole app). The actual data migration
(moving existing finance-labelled habits forward into this table) is a
separate follow-up revision, same split as 473ce36ea4ce (schema) /
3e0cec013a14 (data) for the encryption work.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f99c14eb9d34'
down_revision: Union[str, Sequence[str], None] = 'b1eaf4a5edb9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'budget_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=False),
        sa.Column('color', sa.String(length=16), nullable=True),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('is_recurring', sa.Boolean(), nullable=False),
        sa.Column('recurrence_day_of_month', sa.Integer(), nullable=True),
        sa.Column('auto_event', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    with op.batch_alter_table('events') as batch_op:
        batch_op.add_column(sa.Column('budget_entry_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_events_budget_entry_id', 'budget_entries', ['budget_entry_id'], ['id']
        )

    with op.batch_alter_table('app_settings') as batch_op:
        batch_op.add_column(
            sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD')
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('app_settings') as batch_op:
        batch_op.drop_column('currency')

    with op.batch_alter_table('events') as batch_op:
        batch_op.drop_constraint('fk_events_budget_entry_id', type_='foreignkey')
        batch_op.drop_column('budget_entry_id')

    op.drop_table('budget_entries')
