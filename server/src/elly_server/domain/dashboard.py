"""Composed read-only snapshots that span more than one domain module.

These aren't a new data concept of their own -- just a convenience
composition (events + tasks + habits, all "as of right now") so the MCP
resource and the REST API return the exact same shape from the exact
same function, instead of each layer assembling it independently.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from elly_server.domain import budget, calendar, habits, tasks


def today_snapshot(session: Session) -> dict[str, Any]:
    """Everything relevant to "right now": today's events, open tasks
    (as a parent/children hierarchy -- see get_task_tree(), same shape
    the dedicated Tasks page already uses, so an AI-broken-down task
    shows its steps nested under it here too instead of as flat
    siblings), and habit status. Both `elly://today` (MCP) and
    `GET /api/today` (REST) return exactly this."""
    habits.generate_scheduled_events(session)
    budget.generate_scheduled_budget_events(session)
    return {
        "events": calendar.list_today(session),
        "pending_tasks": tasks.get_task_tree(session),
        "habits": habits.list_all_habit_statuses(session),
    }
