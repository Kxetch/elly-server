"""migrate_finance_habits_to_budget_entries

Revision ID: f3efbc673773
Revises: f99c14eb9d34
Create Date: 2026-07-13 01:47:21.840063

A genuine one-time DATA migration (see 3e0cec013a14 for the precedent
of using raw SQL for this rather than the ORM): the Budget page
replaces "finance"-labelled habits entirely (bill/salary calendar
reminders that never actually tracked an amount) -- every existing
`Habit` row with `label = 'finance'` becomes a recurring `BudgetEntry`
instead (same day-of-month), defaulted to kind="expense" with
amount_cents=0 and a note flagging it needs a real amount, since no
amount was ever tracked before and there's no way to infer one. The
old habit, its calendar events, and any habit_logs are then removed --
fresh calendar events for the new budget entry get generated the next
time domain/dashboard.py's today_snapshot() runs (the same lazy,
idempotent, dedup-by-id-and-date pattern habits already use), not
synchronously here.

Deliberately a one-way migration: downgrade() does NOT attempt to
recreate the original habits (there'd be no way to tell a migrated
entry apart from a budget entry the user created afterward), so it's a
documented no-op rather than a silent/misleading "restoration."
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from elly_server.domain.crypto import encrypt_text

# revision identifiers, used by Alembic.
revision: str = 'f3efbc673773'
down_revision: Union[str, Sequence[str], None] = 'f99c14eb9d34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MIGRATION_NOTE = "Migrated from a habit -- set a real amount."


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()

    finance_habits = conn.execute(
        sa.text(
            "SELECT id, name, scheduled_day_of_month, auto_event, created_at "
            "FROM habits WHERE label = 'finance'"
        )
    ).fetchall()

    for habit_id, name, day_of_month, auto_event, created_at in finance_habits:
        conn.execute(
            sa.text(
                "INSERT INTO budget_entries "
                "(kind, category, color, amount_cents, note, is_recurring, "
                " recurrence_day_of_month, auto_event, created_at) "
                "VALUES "
                "('expense', :category, NULL, 0, :note, 1, :day_of_month, :auto_event, :created_at)"
            ),
            {
                "category": name,
                "note": encrypt_text(_MIGRATION_NOTE),
                "day_of_month": day_of_month,
                "auto_event": auto_event,
                "created_at": created_at,
            },
        )
        conn.execute(sa.text("DELETE FROM events WHERE habit_id = :habit_id"), {"habit_id": habit_id})
        conn.execute(
            sa.text("DELETE FROM habit_logs WHERE habit_id = :habit_id"), {"habit_id": habit_id}
        )
        conn.execute(sa.text("DELETE FROM habits WHERE id = :habit_id"), {"habit_id": habit_id})


def downgrade() -> None:
    """Downgrade schema -- deliberately a no-op, see module docstring."""
    pass
