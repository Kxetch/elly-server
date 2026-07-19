"""Full data export -- a self-hosted app's users need a way to back up
their own data without touching the SQLite file directly (and touching
it directly wouldn't even work well here, since sensitive fields are
encrypted at rest -- see domain/crypto.py). This reuses the same
domain functions everything else in the app already calls, so the
export is always in sync with what search/get_recent_notes/etc. would
return -- there's no separate, divergent query path to keep correct.

Deliberately NOT included: the local access token, the encryption key,
Telegram chat linkage, and chat conversation history -- an export is
meant to move/backup a user's *content*, not the security material
that protects it or their AI conversation transcripts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from elly_server.domain import budget, calendar, habits, memory, notes, tasks
from elly_server.timeutil import now

_FIVE_YEARS = timedelta(days=365 * 5)


def export_all_data(session: Session) -> dict[str, Any]:
    """Everything a user would reasonably want in a personal backup,
    as one JSON-serializable dict."""
    today = now()
    # A wide-enough window to capture essentially all real usage
    # without needing a true "no bound" query path in calendar.py.
    range_start = (today - _FIVE_YEARS).isoformat()
    range_end = (today + _FIVE_YEARS).isoformat()

    return {
        "exported_at": today.isoformat(),
        "notes": notes.search_notes(session, limit=100_000),
        "tasks": {
            "open": tasks.list_pending_tasks(session),
            "completed": tasks.list_completed_tasks(session, limit=100_000),
        },
        "habits": {
            "active": habits.list_all_habit_statuses(session),
            "archived": habits.list_archived_habits(session),
        },
        "events": calendar.list_events_range(session, range_start, range_end),
        "memory": memory.get_profile_summary(session),
        # Added after the Budget page shipped (income/expense tracking) --
        # was missing here for a while, meaning "Export my data" silently
        # left out everything on the Budget page despite claiming to be
        # a full backup. list_entries() with no `kind` filter returns
        # both income and expenses, one-off and recurring.
        "budget_entries": budget.list_entries(session, limit=100_000),
    }
