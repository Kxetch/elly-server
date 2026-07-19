"""Elly MCP server: exposes the domain layer as MCP tools/resources/prompts.

This is the ONLY way an LLM touches your data. Both the future PWA's
REST API and this MCP server call into `elly_server.domain.*` -- never
duplicate logic here, just translate between MCP's calling convention
and the domain functions.

Run directly with `uv run elly-mcp` (stdio transport, for OpenCode /
Claude Desktop). See the repo README for wiring instructions.
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from elly_server.db.base import get_session, init_db
from elly_server.domain import budget, calendar, dashboard, habits, insights, memory, notes, notifications, tasks

INSTRUCTIONS = """\
Elly is a self-hosted life companion (notebook + diary + calendar + \
habits) for someone with ADHD who is not currently medicated. Keep \
these principles in mind in every interaction:

- Never shame, guilt-trip, or nag about missed habits, streaks, or \
incomplete tasks. A missed day is normal and barely affects long-term \
habit formation -- celebrate consistency, never punish lapses.
- Prefer tiny, concrete next steps over big ambitious plans. If a \
task feels heavy, help break it into the smallest possible first step \
(a few minutes) with breakdown_task, rather than describing the \
"ideal" full version.
- Support autonomy: offer options and ask what the user wants rather \
than prescribing what they "should" do.
- Externalize time: be concrete about dates and specific time blocks \
rather than vague references like "this afternoon" -- time blindness \
is common in ADHD, so vagueness is genuinely unhelpful here.
- When reflecting on logged data (mood_trend, weekly_review, \
correlate_metrics), describe patterns warmly and descriptively, like \
a friend noticing things -- never as a performance review.
- Use these tools to actually read and write data rather than asking \
the user to repeat information you could look up (start with the \
elly://today or elly://profile resources for context).
"""

mcp = FastMCP("elly", instructions=INSTRUCTIONS)


# --------------------------------------------------------------------------
# Notes / diary
# --------------------------------------------------------------------------


@mcp.tool()
def create_note(
    body: str,
    type: str = "note",
    title: Optional[str] = None,
    mood: Optional[int] = None,
    energy: Optional[int] = None,
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Create a notebook note or a diary/journal entry.

    Use type="diary" for a dated journal entry -- optionally with mood
    and energy on a 1-9 scale. Use type="note" (the default) for
    freeform notebook content with no emotional check-in attached.
    """
    with get_session() as session:
        return notes.create_note(
            session, body=body, type=type, title=title, mood=mood, energy=energy, tags=tags
        )


@mcp.tool()
def update_note(
    note_id: int,
    body: Optional[str] = None,
    title: Optional[str] = None,
    mood: Optional[int] = None,
    energy: Optional[int] = None,
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Update an existing note or diary entry. Only pass the fields you want to change."""
    with get_session() as session:
        return notes.update_note(
            session, note_id, body=body, title=title, mood=mood, energy=energy, tags=tags
        )


@mcp.tool()
def search_notes(
    query: Optional[str] = None,
    type: Optional[str] = None,
    tag: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search notes/diary entries by text, type ("note"/"diary"), tag, or ISO date range."""
    with get_session() as session:
        return notes.search_notes(
            session, query=query, type=type, tag=tag, since=since, until=until, limit=limit
        )


@mcp.tool()
def get_recent_notes(type: Optional[str] = None, limit: int = 10) -> list[dict[str, Any]]:
    """Get the most recent notes or diary entries (newest first)."""
    with get_session() as session:
        return notes.get_recent_notes(session, type=type, limit=limit)


@mcp.tool()
def delete_note(note_id: int) -> bool:
    """Delete a note or diary entry permanently. Confirm with the user before deleting."""
    with get_session() as session:
        return notes.delete_note(session, note_id=note_id)


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


@mcp.tool()
def create_event(
    title: str,
    start_at: str,
    end_at: Optional[str] = None,
    description: Optional[str] = None,
    habit_id: Optional[int] = None,
    color: Optional[str] = None,
) -> dict[str, Any]:
    """Create a calendar event/time-block. Use ISO-8601 for dates
    (e.g. "2026-07-06T09:30:00"). Prefer a concrete end_at over leaving
    it open -- specific time-blocks help with time blindness. Pass
    habit_id to link this event to a habit (e.g. a scheduled routine).
    Optionally set a color (one of: blue, emerald, amber, violet, rose,
    cyan, lime, pink, indigo, teal, orange, sky)."""
    with get_session() as session:
        return calendar.create_event(
            session, title=title, start_at=start_at, end_at=end_at, description=description, habit_id=habit_id, color=color
        )


@mcp.tool()
def list_today() -> list[dict[str, Any]]:
    """List today's calendar events in order. Start here for any planning conversation."""
    with get_session() as session:
        return calendar.list_today(session)


@mcp.tool()
def list_events_range(start: str, end: str) -> list[dict[str, Any]]:
    """List calendar events between two ISO-8601 dates/datetimes."""
    with get_session() as session:
        return calendar.list_events_range(session, start=start, end=end)


@mcp.tool()
def reschedule_event(
    event_id: int, start_at: str, end_at: Optional[str] = None
) -> dict[str, Any]:
    """Move an existing event to a new start (and optionally end) time."""
    with get_session() as session:
        return calendar.reschedule_event(session, event_id=event_id, start_at=start_at, end_at=end_at)


@mcp.tool()
def search_events(query: str, start: Optional[str] = None, end: Optional[str] = None) -> list[dict[str, Any]]:
    """Search calendar events whose title contains the given text (case-insensitive)."""
    with get_session() as session:
        return calendar.search_events(session, query=query, start=start, end=end)


@mcp.tool()
def delete_event(event_id: int) -> bool:
    """Delete a calendar event. Returns false if it didn't exist."""
    with get_session() as session:
        return calendar.delete_event(session, event_id=event_id)


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------


@mcp.tool()
def create_task(
    title: str,
    due_at: Optional[str] = None,
    estimate_minutes: Optional[int] = None,
    priority: Optional[str] = None,
    parent_task_id: Optional[int] = None,
) -> dict[str, Any]:
    """Create a task. Keep titles concrete and action-oriented (e.g.
    "Email landlord about lease" rather than "Landlord stuff") -- vague
    tasks are exactly what causes initiation paralysis."""
    with get_session() as session:
        return tasks.create_task(
            session,
            title=title,
            due_at=due_at,
            estimate_minutes=estimate_minutes,
            priority=priority,
            parent_task_id=parent_task_id,
        )


@mcp.tool()
def update_task(
    task_id: int,
    title: Optional[str] = None,
    due_at: Optional[str] = None,
    estimate_minutes: Optional[int] = None,
    priority: Optional[str] = None,
) -> dict[str, Any]:
    """Edit a task's title, due date, estimate, or priority."""
    with get_session() as session:
        return tasks.update_task(
            session,
            task_id=task_id,
            title=title,
            due_at=due_at,
            estimate_minutes=estimate_minutes,
            priority=priority,
        )


@mcp.tool()
def complete_task(task_id: int) -> dict[str, Any]:
    """Mark a task as done."""
    with get_session() as session:
        return tasks.complete_task(session, task_id=task_id)


@mcp.tool()
def reopen_task(task_id: int) -> dict[str, Any]:
    """Undo a completion -- moves a task back to open. Use this when the
    user says they completed something by mistake, or aren't actually
    done yet."""
    with get_session() as session:
        return tasks.reopen_task(session, task_id=task_id)


@mcp.tool()
def delete_task(task_id: int) -> bool:
    """Delete a task (and its subtasks). Confirm with the user before deleting."""
    with get_session() as session:
        return tasks.delete_task(session, task_id=task_id)


@mcp.tool()
def list_pending_tasks(due_before: Optional[str] = None) -> list[dict[str, Any]]:
    """List open/incomplete tasks, optionally only those due before a given ISO date."""
    with get_session() as session:
        return tasks.list_pending_tasks(session, due_before=due_before)


@mcp.tool()
def breakdown_task(task_id: int, subtasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Save a breakdown of a task into smaller, concrete subtasks.

    YOU should come up with the subtask list before calling this --
    small, concrete steps, each with a realistic estimate_minutes. Each
    subtask dict may have: title (required), estimate_minutes, due_at,
    priority. Make the FIRST subtask tiny enough to start in under 5
    minutes -- that's the point of breaking it down at all.
    """
    with get_session() as session:
        return tasks.breakdown_task(session, task_id=task_id, subtasks=subtasks)


# --------------------------------------------------------------------------
# Habits
# --------------------------------------------------------------------------


@mcp.tool()
def create_habit(
    name: str,
    cadence: str = "daily",
    tiny_version: Optional[str] = None,
    label: Optional[str] = None,
    scheduled_start: Optional[str] = None,
    scheduled_end: Optional[str] = None,
    scheduled_days: Optional[str] = None,
    auto_event: bool = True,
    color: Optional[str] = None,
) -> dict[str, Any]:
    """Create a habit to track. Always suggest a tiny_version -- the
    smallest possible version of this habit that still counts (e.g.
    "put on running shoes" instead of "run 5k"), per BJ Fogg's Tiny
    Habits approach. cadence is "daily" or "weekly".

    Use label="routine" or label="fitness" for time-blocked daily/weekly
    habits (work hours, meals, exercise -- "fitness" gets its own
    colour-coded grouping in the dashboard, useful for workout/movement
    habits specifically) with scheduled_start/scheduled_end in "HH:MM"
    format and scheduled_days as comma-separated weekday numbers
    (0=Monday, 6=Sunday).

    For income/bills (salary, rent, subscriptions), use the Budget
    tools instead (log_income/log_expense/create_recurring_budget_entry)
    -- habits don't track amounts, budget entries do.

    auto_event=True generates calendar events automatically.
    Optionally set a color (one of: blue, emerald, amber, violet, rose,
    cyan, lime, pink, indigo, teal, orange, sky)."""
    with get_session() as session:
        return habits.create_habit(
            session,
            name=name,
            cadence=cadence,
            tiny_version=tiny_version,
            label=label,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            scheduled_days=scheduled_days,
            auto_event=auto_event,
            color=color,
        )


@mcp.tool()
def update_habit(
    habit_id: int,
    name: Optional[str] = None,
    tiny_version: Optional[str] = None,
    cadence: Optional[str] = None,
    label: Optional[str] = None,
    scheduled_start: Optional[str] = None,
    scheduled_end: Optional[str] = None,
    scheduled_days: Optional[str] = None,
    auto_event: Optional[bool] = None,
    color: Optional[str] = None,
) -> dict[str, Any]:
    """Update an existing habit's name, tiny_version, cadence, label,
    scheduling fields, or colour. Only pass the fields you want to change."""
    with get_session() as session:
        return habits.update_habit(
            session,
            habit_id=habit_id,
            name=name,
            tiny_version=tiny_version,
            cadence=cadence,
            label=label,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            scheduled_days=scheduled_days,
            auto_event=auto_event,
            color=color,
        )


@mcp.tool()
def archive_habit(habit_id: int) -> dict[str, Any]:
    """Archive a habit (keeps all history, just stops showing it).
    Never frame this as quitting or failing."""
    with get_session() as session:
        return habits.set_habit_active(session, habit_id, False)


@mcp.tool()
def unarchive_habit(habit_id: int) -> dict[str, Any]:
    """Bring an archived habit back -- it starts showing up again,
    with all its history intact."""
    with get_session() as session:
        return habits.set_habit_active(session, habit_id, True)


@mcp.tool()
def list_archived_habits() -> list[dict[str, Any]]:
    """List archived habits, most recently archived first. Pairs with
    unarchive_habit to bring one back."""
    with get_session() as session:
        return habits.list_archived_habits(session)


@mcp.tool()
def log_habit(
    habit_id: Optional[int] = None,
    name: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Log a habit completion for today. Identify the habit by id or by
    (partial, case-insensitive) name."""
    with get_session() as session:
        return habits.log_habit(session, habit_id=habit_id, name=name, note=note)


@mcp.tool()
def get_habit_status(
    habit_id: Optional[int] = None, name: Optional[str] = None
) -> dict[str, Any]:
    """Get streak/consistency status for one habit (by id or name), or
    all habits if neither is given. Streaks are intentionally
    forgiving -- a single missed day does not reset progress to zero,
    so never describe it as "broken" or "failed"."""
    with get_session() as session:
        return habits.get_habit_status(session, habit_id=habit_id, name=name)


@mcp.tool()
def delete_habit(habit_id: int) -> bool:
    """Permanently delete a habit and all its logs + calendar events.
    This cannot be undone. Use archive_habit instead if you just want
    to hide it."""
    with get_session() as session:
        return habits.delete_habit(session, habit_id=habit_id)


@mcp.tool()
def generate_scheduled_events() -> list[dict[str, Any]]:
    """Auto-create today's calendar events for habits with scheduling
    configured and auto_event=True. Safe to call repeatedly -- it skips
    dates where an event already exists for that habit."""
    with get_session() as session:
        return habits.generate_scheduled_events(session)


# --------------------------------------------------------------------------
# Budget (income/expense tracking)
# --------------------------------------------------------------------------


@mcp.tool()
def log_expense(
    category: str,
    amount: float,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Log a one-off expense that just happened (e.g. "log that I spent
    $12 on lunch"). amount is a plain number in the user's chosen
    currency (see their Settings) -- don't include a currency symbol.
    For a recurring bill (rent, a subscription), use
    create_recurring_budget_entry instead."""
    with get_session() as session:
        return budget.log_expense(session, category=category, amount=amount, note=note)


@mcp.tool()
def log_income(
    category: str,
    amount: float,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Log a one-off income entry that just happened (e.g. a freelance
    payment landing). amount is a plain number in the user's chosen
    currency. For recurring income (e.g. salary), use
    create_recurring_budget_entry instead."""
    with get_session() as session:
        return budget.log_income(session, category=category, amount=amount, note=note)


@mcp.tool()
def create_recurring_budget_entry(
    kind: str,
    category: str,
    amount: float,
    recurrence_day_of_month: int,
    note: Optional[str] = None,
    auto_event: bool = True,
) -> dict[str, Any]:
    """Set up a recurring monthly income or expense (salary, rent, a
    subscription) -- kind is "income" or "expense",
    recurrence_day_of_month is 1-31 (clamped to the last day of shorter
    months). auto_event=True generates calendar events for each future
    occurrence, same as a habit's scheduling."""
    with get_session() as session:
        return budget.create_entry(
            session,
            kind=kind,
            category=category,
            amount=amount,
            note=note,
            is_recurring=True,
            recurrence_day_of_month=recurrence_day_of_month,
            auto_event=auto_event,
        )


@mcp.tool()
def list_budget_entries(
    kind: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List income/expense entries, most recent first. Filter by
    kind ("income"/"expense") and/or a date range."""
    with get_session() as session:
        return budget.list_entries(session, kind=kind, since=since, until=until, limit=limit)


@mcp.tool()
def get_budget_summary(since: Optional[str] = None, until: Optional[str] = None) -> dict[str, Any]:
    """Get total income, total expenses, net, and an expense-by-category
    breakdown for a period -- defaults to the current calendar month if
    since/until aren't given. Use this to answer questions like "how
    much did I spend on groceries this month?" or "am I in the black
    this month?"."""
    with get_session() as session:
        return budget.get_summary(session, since=since, until=until)


@mcp.tool()
def delete_budget_entry(entry_id: int) -> bool:
    """Permanently delete an income/expense entry and any calendar
    events generated from it. This cannot be undone."""
    with get_session() as session:
        return budget.delete_entry(session, entry_id=entry_id)


# --------------------------------------------------------------------------
# Insights
# --------------------------------------------------------------------------


@mcp.tool()
def mood_trend(days: int = 14) -> dict[str, Any]:
    """Get daily average mood/energy from diary entries over the last N days."""
    with get_session() as session:
        return insights.mood_trend(session, days=days)


@mcp.tool()
def correlate_metrics(metric_a: str, metric_b: str, days: int = 30) -> dict[str, Any]:
    """Correlate two daily metrics ("mood", "energy", or
    "habit_completions") over the last N days. Returns a Pearson
    correlation plus the paired daily series so you can describe the
    *shape* of the relationship, not just report a number."""
    with get_session() as session:
        return insights.correlate(session, metric_a=metric_a, metric_b=metric_b, days=days)


@mcp.tool()
def weekly_review() -> dict[str, Any]:
    """Get structured data for a weekly reflection (mood, notes, tasks,
    habits over the last 7 days). Turn this into a warm, descriptive
    summary -- never a performance scorecard."""
    with get_session() as session:
        return insights.weekly_review(session)


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------


@mcp.tool()
def remember(content: str, type: str = "general", importance: Optional[float] = None) -> dict[str, Any]:
    """Remember a fact, goal, or preference about the user for future
    conversations (type: "fact", "goal", "preference", or "general")."""
    with get_session() as session:
        return memory.remember(session, content=content, type=type, importance=importance)


@mcp.tool()
def recall(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Recall previously remembered facts/goals/preferences matching a query."""
    with get_session() as session:
        return memory.recall(session, query=query, limit=limit)


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------


@mcp.tool()
def get_notification_prefs() -> dict[str, Any]:
    """Get current notification preferences (enabled, morning/evening times)."""
    with get_session() as session:
        return notifications.get_prefs(session)


@mcp.tool()
def update_notification_prefs(
    enabled: Optional[bool] = None,
    morning_time: Optional[str] = None,
    evening_time: Optional[str] = None,
) -> dict[str, Any]:
    """Update notification preferences. Set enabled=false to silence all
    notifications, or adjust morning_time/evening_time (HH:MM format) to
    change when they appear."""
    with get_session() as session:
        return notifications.update_prefs(session, enabled=enabled, morning_time=morning_time, evening_time=evening_time)


@mcp.tool()
def send_test_notification() -> dict[str, str]:
    """Send a test macOS notification to verify notifications are working."""
    with get_session() as session:
        notifications.send_test(session)
        return {"status": "sent"}


# --------------------------------------------------------------------------
# Resources (read-only context, no explicit tool call needed)
# --------------------------------------------------------------------------


@mcp.resource("elly://today")
def today_resource() -> dict[str, Any]:
    """Snapshot of today: events, pending tasks, and habit status."""
    with get_session() as session:
        return dashboard.today_snapshot(session)


@mcp.resource("elly://recent-notes")
def recent_notes_resource() -> list[dict[str, Any]]:
    """The 10 most recent notes/diary entries."""
    with get_session() as session:
        return notes.get_recent_notes(session, limit=10)


@mcp.resource("elly://profile")
def profile_resource() -> dict[str, list[str]]:
    """Everything remembered about the user, grouped by type."""
    with get_session() as session:
        return memory.get_profile_summary(session)


# --------------------------------------------------------------------------
# Prompts (reusable conversation starters)
# --------------------------------------------------------------------------


@mcp.prompt()
def morning_planning() -> str:
    """Start a gentle morning planning conversation."""
    return (
        "Help me plan today. Look at elly://today (events, pending tasks, "
        "habit status) first. Ask what my energy feels like right now "
        "rather than assuming, then suggest a realistic, lightly "
        "time-blocked plan with the most important thing done early. "
        "Keep it short -- a few concrete blocks, not an exhaustive list."
    )


@mcp.prompt()
def evening_reflection() -> str:
    """Start a gentle end-of-day reflection / diary prompt."""
    return (
        "Check in on how today went. Ask an open, low-pressure question "
        "(not \"did you complete everything\") and offer to save my "
        "answer as a diary entry (create_note with type=\"diary\") with "
        "a mood/energy rating if I want. If I mention doing any habits, "
        "offer to log them with log_habit."
    )


@mcp.prompt()
def weekly_review_prompt() -> str:
    """Start a weekly reflection conversation."""
    return (
        "Call weekly_review and mood_trend, then reflect the patterns "
        "back to me warmly and descriptively -- like a thoughtful friend "
        "noticing things, not a performance report. Highlight one thing "
        "that went well before anything that didn't. After sharing the "
        "reflection, offer to save it as a note (use create_note with "
        'type="note" and tags=["weekly-reflection"]) so it appears on '
        "the This Week dashboard. End by asking if I want to adjust any "
        "habit or plan for next week."
    )


def init() -> None:
    """Ensure the SQLite schema exists before serving requests."""
    init_db()


def main() -> None:
    init()
    mcp.run()


if __name__ == "__main__":
    main()
