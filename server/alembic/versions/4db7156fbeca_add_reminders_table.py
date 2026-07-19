"""add_reminders_table

Revision ID: 4db7156fbeca
Revises: ba3b9a1fe7ae
Create Date: 2026-07-15 19:53:35.990773

New table for Sprint 4's reminders/alarms engine (see PLAN.md section 0.2).
`target_type`/`target_id` are a polymorphic reference to Task/Event/Habit
-- no single FK is possible across three different tables, so this is
plain columns, not a ForeignKey; domain/reminders.py owns keeping this in
sync (cascade-delete, recompute-on-reschedule).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4db7156fbeca'
down_revision: Union[str, Sequence[str], None] = 'ba3b9a1fe7ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'reminders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('target_type', sa.String(length=16), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('offset_minutes', sa.Integer(), nullable=False),
        sa.Column('trigger_at', sa.DateTime(), nullable=False),
        sa.Column('fired_at', sa.DateTime(), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('reminders')
