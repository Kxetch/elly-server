"""add_scheduling_to_habits

Revision ID: 7f9e2bde8ee3
Revises: b5058d2200f7
Create Date: 2026-07-06 04:12:09.468778

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7f9e2bde8ee3'
down_revision: Union[str, Sequence[str], None] = 'b5058d2200f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('habits') as batch_op:
        batch_op.add_column(sa.Column('scheduled_start', sa.String(length=5), nullable=True))
        batch_op.add_column(sa.Column('scheduled_end', sa.String(length=5), nullable=True))
        batch_op.add_column(sa.Column('scheduled_days', sa.String(length=15), nullable=True))
        batch_op.add_column(sa.Column('auto_event', sa.Boolean(), nullable=False, server_default=sa.text('1')))

    with op.batch_alter_table('events') as batch_op:
        batch_op.add_column(sa.Column('habit_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_events_habit_id', 'habits', ['habit_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('events') as batch_op:
        batch_op.drop_constraint('fk_events_habit_id', type_='foreignkey')
        batch_op.drop_column('habit_id')

    with op.batch_alter_table('habits') as batch_op:
        batch_op.drop_column('auto_event')
        batch_op.drop_column('scheduled_days')
        batch_op.drop_column('scheduled_end')
        batch_op.drop_column('scheduled_start')
