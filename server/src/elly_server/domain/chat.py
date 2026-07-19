from __future__ import annotations

import json
import uuid
from typing import Any, AsyncGenerator

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from elly_server.db.models import ChatMessage
from elly_server.db.serialize import model_to_dict
from elly_server.domain import budget, calendar, habits, insights, memory as mem, notes, tasks
from elly_server.domain.llm_client import LlmNotConfiguredError, describe_llm_error, get_llm_client
from elly_server.timeutil import now


def _tool(name: str, description: str, props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required or []},
        },
    }


_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}


def _build_tools() -> list[dict[str, Any]]:
    """OpenAI tool definitions -- full parity with what the UI can do by hand."""
    return [
        # ---- Notes / diary -------------------------------------------------
        _tool("create_note", "Create a notebook note (type='note') or a dated diary/journal entry (type='diary'). Diary entries can include mood and energy on a 1-9 scale.", {
            "body": {**_S, "description": "Content of the note or diary entry"},
            "type": {**_S, "enum": ["note", "diary"]},
            "title": _S,
            "mood": {**_I, "minimum": 1, "maximum": 9},
            "energy": {**_I, "minimum": 1, "maximum": 9},
            "tags": {"type": "array", "items": _S},
        }, ["body"]),
        _tool("update_note", "Edit an existing note or diary entry. Only pass fields you want to change.", {
            "note_id": _I, "body": _S, "title": _S,
            "mood": {**_I, "minimum": 1, "maximum": 9},
            "energy": {**_I, "minimum": 1, "maximum": 9},
            "tags": {"type": "array", "items": _S},
        }, ["note_id"]),
        _tool("delete_note", "Delete a note or diary entry permanently. Confirm with the user before deleting.", {"note_id": _I}, ["note_id"]),
        _tool("search_notes", "Search notes and diary entries by text, type, tag, or date range.", {
            "query": _S, "type": {**_S, "enum": ["note", "diary"]}, "tag": _S,
            "since": {**_S, "description": "ISO date, e.g. 2026-07-01"},
            "until": _S, "limit": {**_I, "default": 10},
        }),
        _tool("get_recent_notes", "Get the most recent notes or diary entries.", {
            "type": {**_S, "enum": ["note", "diary"]}, "limit": {**_I, "default": 5},
        }),
        # ---- Calendar --------------------------------------------------------
        _tool("create_event", "Add a calendar event/time-block. Use ISO-8601 local datetimes (e.g. 2026-07-06T15:00:00). Prefer setting a concrete end_at.", {
            "title": _S,
            "start_at": {**_S, "description": "Start datetime, ISO-8601"},
            "end_at": {**_S, "description": "End datetime, ISO-8601"},
            "description": _S,
        }, ["title", "start_at"]),
        _tool("reschedule_event", "Move an existing event to a new start (and optionally end) time.", {
            "event_id": _I, "start_at": _S, "end_at": _S,
        }, ["event_id", "start_at"]),
        _tool("delete_event", "Delete a calendar event by its ID. NEVER delete an event the user hasn't explicitly confirmed. When asked to delete events matching a description, use search_events first, show the matches, and ask which to delete.", {"event_id": _I}, ["event_id"]),
        _tool("search_events", "Search calendar events whose title contains the given text (case-insensitive). Use this BEFORE deleting to find events matching a description.", {
            "query": _S,
            "start": {**_S, "description": "ISO date to search from (default: today)"},
            "end": {**_S, "description": "ISO date to search until (default: 1 year from start)"},
        }, ["query"]),
        _tool("list_today", "List today's calendar events.", {}),
        _tool("list_events_range", "List calendar events between two ISO datetimes -- use this to see any day or week, past or future.", {
            "start": {**_S, "description": "e.g. 2026-07-07T00:00:00"},
            "end": {**_S, "description": "e.g. 2026-07-07T23:59:59"},
        }, ["start", "end"]),
        # ---- Tasks -----------------------------------------------------------
        _tool("create_task", "Create a task. Keep titles concrete and action-oriented.", {
            "title": _S, "due_at": {**_S, "description": "ISO datetime, optional"},
            "priority": {**_S, "enum": ["low", "medium", "high"]},
            "estimate_minutes": _I,
            "parent_task_id": {**_I, "description": "Make this a subtask of another task"},
        }, ["title"]),
        _tool("update_task", "Edit a task's title, due date, estimate, or priority.", {
            "task_id": _I, "title": _S, "due_at": _S,
            "estimate_minutes": _I, "priority": {**_S, "enum": ["low", "medium", "high"]},
        }, ["task_id"]),
        _tool("breakdown_task", "Break a task into small concrete subtasks. YOU propose the steps -- make the FIRST one tiny enough to start in under 5 minutes. Each subtask: {title, estimate_minutes}.", {
            "task_id": _I,
            "subtasks": {"type": "array", "items": {"type": "object", "properties": {
                "title": _S, "estimate_minutes": _I, "priority": _S,
            }, "required": ["title"]}},
        }, ["task_id", "subtasks"]),
        _tool("list_pending_tasks", "List all open/incomplete tasks.", {}),
        _tool("complete_task", "Mark a task as done.", {"task_id": _I}, ["task_id"]),
        _tool("delete_task", "Delete a task (and its subtasks). Confirm with the user before deleting.", {"task_id": _I}, ["task_id"]),
        # ---- Habits ----------------------------------------------------------
        _tool("create_habit", "Create a habit to track. Supports two types: 'simple' (no schedule, just log it) or 'routine'/'fitness' (a scheduled time block on certain days, e.g. work hours 9-5 weekdays — set label='routine' or 'fitness' + scheduled_start/scheduled_end/scheduled_days). ALWAYS suggest a tiny_version. For income/bills (salary, rent, subscriptions), use the budget tools instead -- habits don't track amounts, budget entries do.", {
            "name": _S,
            "cadence": {**_S, "enum": ["daily", "weekly"]},
            "tiny_version": {**_S, "description": "Smallest possible version that still counts"},
            "label": {**_S, "description": "'routine' or 'fitness' for time-blocked habits, or omit for simple habits", "enum": ["routine", "fitness"]},
            "scheduled_start": {**_S, "description": "Start time (HH:MM) for routine/fitness habits"},
            "scheduled_end": {**_S, "description": "End time (HH:MM) for routine/fitness habits"},
            "scheduled_days": {**_S, "description": "Comma-separated day numbers (0=Mon..4=Fri, or 0-6)"},
            "auto_event": _B,
            "color": {**_S, "description": "Calendar colour for this habit's events: blue, emerald, amber, violet, rose, cyan, lime, pink, indigo, teal, orange, or sky"},
        }, ["name"]),
        _tool("update_habit", "Update an existing habit's name, tiny_version, cadence, label, or scheduling fields.", {
            "habit_id": _I, "name": _S, "tiny_version": _S, "cadence": _S,
            "label": {**_S, "description": "'routine' or 'fitness' for time-blocked, or null for simple", "enum": ["routine", "fitness"]},
            "scheduled_start": _S, "scheduled_end": _S, "scheduled_days": _S,
            "auto_event": _B,
            "color": {**_S, "description": "Calendar colour: blue, emerald, amber, violet, rose, cyan, lime, pink, indigo, teal, orange, or sky"},
        }, ["habit_id"]),
        _tool("archive_habit", "Archive a habit (keeps all history, just stops showing it). Never frame this as quitting or failing.", {"habit_id": _I}, ["habit_id"]),
        _tool("delete_habit", "Permanently delete a habit and all its logs + calendar events. This cannot be undone.", {"habit_id": _I}, ["habit_id"]),
        _tool("log_habit", "Log a habit completion for today.", {
            "habit_name": {**_S, "description": "Habit name, partial match ok"},
            "note": _S,
        }, ["habit_name"]),
        _tool("get_habit_status", "Streak/consistency status for all habits.", {}),
        # ---- Budget (income/expense tracking) ---------------------------------
        _tool("log_expense", "Log a one-off expense that just happened (e.g. 'log that I spent $12 on lunch'). amount is a plain number in the user's chosen currency (see get_budget_summary's response, or just don't mention a currency symbol). For a recurring bill, use create_recurring_budget_entry instead.", {
            "category": _S, "amount": {"type": "number"}, "note": _S,
        }, ["category", "amount"]),
        _tool("log_income", "Log a one-off income entry that just happened (e.g. a freelance payment landing). For recurring income (e.g. salary), use create_recurring_budget_entry instead.", {
            "category": _S, "amount": {"type": "number"}, "note": _S,
        }, ["category", "amount"]),
        _tool("create_recurring_budget_entry", "Set up a recurring monthly income or expense (salary, rent, a subscription) -- generates calendar events for each future occurrence, same as a routine habit's scheduling.", {
            "kind": {**_S, "enum": ["income", "expense"]},
            "category": _S, "amount": {"type": "number"},
            "recurrence_day_of_month": {**_I, "description": "1-31, clamped to the last day of shorter months"},
            "note": _S, "auto_event": _B,
        }, ["kind", "category", "amount", "recurrence_day_of_month"]),
        _tool("list_budget_entries", "List income/expense entries, most recent first. Filter by kind and/or a date range.", {
            "kind": {**_S, "enum": ["income", "expense"]},
            "since": {**_S, "description": "ISO date, e.g. 2026-07-01"}, "until": _S,
            "limit": {**_I, "default": 50},
        }),
        _tool("get_budget_summary", "Total income, total expenses, net, and an expense-by-category breakdown for a period -- defaults to the current calendar month if since/until aren't given. Use this to answer questions like 'how much did I spend on groceries this month?' or 'am I in the black this month?'.", {
            "since": _S, "until": _S,
        }),
        _tool("delete_budget_entry", "Permanently delete an income/expense entry and any calendar events generated from it. Confirm with the user before deleting.", {"entry_id": _I}, ["entry_id"]),
        # ---- Insights --------------------------------------------------------
        _tool("mood_trend", "Daily average mood/energy from diary entries over the last N days.", {"days": {**_I, "default": 14}}),
        _tool("correlate_metrics", "Correlate two daily metrics (mood, energy, or habit_completions) over the last N days. Returns Pearson correlation plus paired daily series.", {
            "metric_a": {**_S, "enum": ["mood", "energy", "habit_completions"]},
            "metric_b": {**_S, "enum": ["mood", "energy", "habit_completions"]},
            "days": {**_I, "default": 30},
        }, ["metric_a", "metric_b"]),
        _tool("weekly_review", "Structured data for a weekly reflection (mood, notes, tasks, habits over 7 days). Narrate it warmly, never as a performance review.", {}),
        # ---- Memory ----------------------------------------------------------
        _tool("remember", "Save a fact, goal, or preference about the user for future conversations.", {
            "content": _S, "type": {**_S, "enum": ["fact", "goal", "preference", "general"]},
        }, ["content"]),
        _tool("recall", "Recall previously saved facts/goals/preferences matching a query.", {
            "query": _S, "limit": {**_I, "default": 5},
        }, ["query"]),
    ]


_TOOLS = _build_tools()

# ---- Ollama-only tool-catalog narrowing ------------------------------------
#
# Small local models (the kind that actually fit on modest consumer
# hardware, which is most of what "run it fully local/private" realistically
# means for this app) get measurably less reliable and slower as the number
# of available tools grows. Confirmed empirically against a real Ollama
# instance (qwen2.5:3b, 5 trials each, identical request "Create a task
# called Buy groceries"): 100% success / ~2.0s average against a 6-tool
# task-only subset, vs. 80% success / ~6.1s average (with a 24s outlier)
# against Elly's full 34-tool catalog. OpenAI's cloud models don't show this
# problem at this tool count, so this narrowing applies to the "ollama"
# provider only -- "openai" always gets the full, unrestricted _TOOLS list,
# completely unchanged from before this existed.
#
# Deliberately simple, deterministic keyword matching rather than a second
# LLM call for classification -- an extra round-trip would roughly double
# latency on the provider that's already the slower one, defeating the
# point. Always falls back to the FULL tool set when no category's keywords
# match (a broad/ambiguous request like "plan my day" or "how am I doing?"
# -- exactly the built-in quick-start prompts in the chat panel, which
# genuinely need cross-category access), so this can only ever narrow
# things down from today's behavior for a clearly single-domain request,
# never make a broad request less capable than before.
_TOOL_CATEGORIES: dict[str, set[str]] = {
    "notes": {"create_note", "update_note", "delete_note", "search_notes", "get_recent_notes"},
    "calendar": {"create_event", "reschedule_event", "delete_event", "search_events", "list_today", "list_events_range"},
    "tasks": {"create_task", "update_task", "breakdown_task", "list_pending_tasks", "complete_task", "delete_task"},
    "habits": {"create_habit", "update_habit", "archive_habit", "delete_habit", "log_habit", "get_habit_status"},
    "budget": {"log_expense", "log_income", "create_recurring_budget_entry", "list_budget_entries", "get_budget_summary", "delete_budget_entry"},
    "insights": {"mood_trend", "correlate_metrics", "weekly_review"},
    "memory": {"remember", "recall"},
}

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "notes": ("note", "notes", "diary", "journal", "wrote", "write down", "jot"),
    "calendar": ("event", "calendar", "schedule", "meeting", "appointment", "reschedule", "book "),
    "tasks": ("task", "todo", "to-do", "to do", "break down", "breakdown", "finish", "complete"),
    "habits": ("habit", "streak", "routine", "archive"),
    "budget": ("spent", "spend", "expense", "income", "budget", "money", "cost", "bought", "paid", "bill", "salary", "$"),
    "insights": ("mood", "energy", "correlat", "trend", "weekly review", "reflection", "insight"),
    "memory": ("remember", "recall", "prefer", "forget"),
}

# Small, generically useful, cheap-context tools kept available regardless
# of which category(ies) matched -- high odds of being genuinely relevant
# to almost any request, so excluding them would save little context but
# risk a lot of usefulness.
_ALWAYS_INCLUDE_TOOLS = {"list_today", "remember", "recall"}


def _select_tools_for_provider(content: str, provider: str) -> list[dict[str, Any]]:
    """The tool list to offer the model for this turn -- full catalog for
    "openai" (unchanged), a keyword-narrowed subset for "ollama" (see the
    module-level comment above _TOOL_CATEGORIES for why)."""
    if provider != "ollama":
        return _TOOLS

    lowered = content.lower()
    matched_categories = {
        category for category, keywords in _CATEGORY_KEYWORDS.items()
        if any(kw in lowered for kw in keywords)
    }
    if not matched_categories:
        return _TOOLS

    wanted_names = set(_ALWAYS_INCLUDE_TOOLS)
    for category in matched_categories:
        wanted_names |= _TOOL_CATEGORIES[category]

    return [t for t in _TOOLS if t["function"]["name"] in wanted_names]


def _last_user_content(oai_messages: list[dict[str, Any]]) -> str:
    """The most recent user turn in a built message list -- used to base
    tool-catalog narrowing on, since _agentic_loop()/_run_sync_tool_round()
    only ever see the already-built oai_messages, not a raw `content`
    string directly (true both for a fresh send and for resuming after a
    destructive-tool confirm/decline)."""
    for msg in reversed(oai_messages):
        if msg.get("role") == "user":
            return msg.get("content") or ""
    return ""


# Map function names to domain functions (callable with a session and kwargs)
_FN_MAP: dict[str, Any] = {
    "create_note": notes.create_note,
    "update_note": notes.update_note,
    "delete_note": notes.delete_note,
    "search_notes": notes.search_notes,
    "get_recent_notes": notes.get_recent_notes,
    "create_event": calendar.create_event,
    "reschedule_event": calendar.reschedule_event,
    "delete_event": calendar.delete_event,
    "search_events": calendar.search_events,
    "list_today": calendar.list_today,
    "list_events_range": calendar.list_events_range,
    "create_task": tasks.create_task,
    "update_task": tasks.update_task,
    "breakdown_task": tasks.breakdown_task,
    "list_pending_tasks": tasks.list_pending_tasks,
    "complete_task": tasks.complete_task,
    "delete_task": tasks.delete_task,
    "create_habit": habits.create_habit,
    "update_habit": habits.update_habit,
    "archive_habit": lambda session, habit_id: habits.set_habit_active(session, habit_id, False),
    "delete_habit": habits.delete_habit,
    "log_habit": habits.log_habit,
    "get_habit_status": habits.get_habit_status,
    "log_expense": budget.log_expense,
    "log_income": budget.log_income,
    "create_recurring_budget_entry": lambda session, **kwargs: budget.create_entry(
        session, is_recurring=True, **kwargs
    ),
    "list_budget_entries": budget.list_entries,
    "get_budget_summary": budget.get_summary,
    "delete_budget_entry": budget.delete_entry,
    "mood_trend": insights.mood_trend,
    "correlate_metrics": insights.correlate,
    "weekly_review": insights.weekly_review,
    "remember": mem.remember,
    "recall": mem.recall,
}

# Tools that permanently destroy data. Unlike every other tool call
# (which the model can execute freely -- see the system prompt's "act,
# don't ask permission" guidance), these always pause for an explicit
# user confirmation via the UI before they run, mirroring the
# useConfirm() dialog gate that already exists on every manual delete
# button elsewhere in the app. Previously this was enforced only by
# prompt wording ("confirm with the user before deleting"), i.e. the
# LLM's discretion -- a real gap for a tool that can permanently delete
# a habit's entire history with no undo.
DESTRUCTIVE_TOOLS = {"delete_note", "delete_event", "delete_task", "delete_habit", "delete_budget_entry"}

_SYSTEM_PROMPT = """You are Elly, a non-judgmental executive-function companion \
for someone with ADHD. You live inside their personal notebook/diary/calendar/\
habit/budget app and can read and write everything in it through your tools -- \
notes and diary entries, calendar events, tasks, habits, and income/expense \
tracking (the Budget page) are all in scope.

Core principles:
- Never shame, guilt-trip, or nag about missed habits, incomplete tasks, or \
spending. A missed day is normal and barely affects long-term habit formation.
- Prefer tiny, concrete next steps over big plans. If a task feels heavy, \
offer to break it down with breakdown_task -- the first step should take \
under 5 minutes.
- Be concrete about dates and times (time blindness is real). "Tomorrow \
at 15:00" not "later".
- Support autonomy: offer options, ask what they want, don't prescribe.
- Keep responses SHORT and warm -- this is a quick chat panel, not an essay.
- Use correlate_metrics to find connections between mood, energy, and habit \
completions when you notice something interesting.

Act, don't ask permission for obvious things: if the user says "add lunch \
with Sam tomorrow at noon", just create the event and confirm. Deletes \
(delete_note, delete_event, delete_task, delete_habit, delete_budget_entry) \
always pause for the user to confirm in a dialog before they actually run -- \
you don't need to ask twice in words first, just call the tool normally when \
a delete is clearly what's being asked for; the confirmation happens \
automatically.

When the user mentions something worth remembering (a preference, goal, or \
fact about themselves), save it with `remember`. Use `search_notes`/`recall` \
instead of asking them to repeat things they've already told you.

When asked to delete events matching a description ("delete all events with X in the title"),
ALWAYS search first with `search_events` to find the matching events, then list them to the user
and ask which ones to delete before calling `delete_event`.

HABIT TYPES (use create_habit with the right label + schedule fields):
1. Simple habit — just a name + tiny_version, no schedule fields. Good for \
daily check-ins like "drink water".
2. Routine/fitness — a recurring time block on certain days. Set \
label='routine' (or 'fitness' for workout/movement habits specifically), \
scheduled_start='09:00', scheduled_end='17:00', scheduled_days='0,1,2,3,4' \
(Mon-Fri). Generates calendar events automatically.

BUDGET (income/expense tracking -- amounts, not habits, use these instead of \
create_habit for anything money-related):
1. One-off: use log_expense/log_income the moment something happens ("log \
that I spent $12 on lunch"). amount is a plain number, no currency symbol.
2. Recurring: use create_recurring_budget_entry for salary, rent, \
subscriptions -- set recurrence_day_of_month, and it generates calendar \
events automatically just like a routine habit does.
3. Use get_budget_summary to answer spending/income questions ("how much \
did I spend on groceries this month?", "am I in the black?") rather than \
guessing -- it returns real totals and a category breakdown."""



def _parse_tool_args(args: Any) -> dict[str, Any]:
    """Parse tool arguments, whether they're already a dict or a JSON string."""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        return json.loads(args)
    return {}


def _execute_tool(session: Session, name: str, raw_args: Any) -> str:
    """Execute a domain tool and return a string result for the LLM."""
    fn = _FN_MAP.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        args = _parse_tool_args(raw_args)
        # Special handling: log_habit uses habit_name -> name
        if name == "log_habit" and "habit_name" in args:
            args["name"] = args.pop("habit_name")
        result = fn(session, **args)
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _pending_tool_calls(
    session: Session, conversation_id: str
) -> tuple[list[dict[str, Any]], set[str]] | None:
    """If this conversation is mid-round with tool calls the model
    requested but that haven't all been resolved yet (because it
    paused for confirmation on a destructive one), return
    (that round's full tool_calls list, the set of call ids already
    resolved). Returns None if nothing is pending -- either the
    conversation never had a round, or its last round completed fully.
    """
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.id)
    )
    last_round: list[dict[str, Any]] | None = None
    resolved_ids: set[str] = set()
    for msg in session.scalars(stmt).all():
        if msg.role == "assistant" and msg.tool_arguments:
            last_round = msg.tool_arguments
            resolved_ids = set()
        elif msg.role == "tool" and last_round is not None:
            resolved_ids.add(msg.tool_call_id or "")
        elif msg.role == "user" and last_round is not None:
            # A new user turn started after that round was left
            # unresolved -- send_message_stream's own pre-check
            # auto-declines before this ever happens in practice, but
            # guard here too so this helper is correct on its own.
            last_round = None
            resolved_ids = set()

    if last_round is None:
        return None
    all_ids = {tc["id"] for tc in last_round}
    if resolved_ids >= all_ids:
        return None
    return last_round, resolved_ids


def _decline_result(reason: str) -> str:
    return json.dumps({
        "declined": True,
        "message": reason,
    })


def _auto_decline_stale_pending(session: Session, conversation_id: str) -> None:
    """If the user sends a fresh message while a destructive action is
    still awaiting confirmation (they moved on rather than responding
    to the dialog), resolve it as declined rather than leaving the
    conversation permanently stuck mid-round."""
    pending = _pending_tool_calls(session, conversation_id)
    if pending is None:
        return
    tool_calls, resolved_ids = pending
    for tc in tool_calls:
        if tc["id"] in resolved_ids:
            continue
        session.add(ChatMessage(
            conversation_id=conversation_id,
            role="tool",
            content=_decline_result(
                "The user moved on without confirming this action. Do not perform it."
            ),
            tool_name=tc["function"]["name"],
            tool_arguments=_parse_tool_args(tc["function"]["arguments"]),
            tool_call_id=tc["id"],
        ))
    session.flush()


def _describe_destructive_action(name: str, args: dict[str, Any]) -> str:
    """Plain-language description of a paused destructive tool call, for
    channels with no confirmation-dialog UI (Telegram) -- mirrors
    ChatPanel.tsx's describeDestructiveAction() so the same action reads
    the same way regardless of which channel asked about it. Always ends
    with an explicit yes/no prompt since Telegram has nothing but plain
    text to work with."""
    descriptions = {
        "delete_task": lambda a: f"Delete this task? Task #{a.get('task_id')} will be removed permanently.",
        "delete_habit": lambda a: f"Delete this habit? Habit #{a.get('habit_id')} and all its history/calendar events will be removed permanently.",
        "delete_event": lambda a: f"Delete this event? Event #{a.get('event_id')} will be removed from your calendar.",
        "delete_note": lambda a: f"Delete this note? Note #{a.get('note_id')} will be removed permanently.",
        "delete_budget_entry": lambda a: f"Delete this budget entry? Entry #{a.get('entry_id')} will be removed permanently.",
    }
    build = descriptions.get(name)
    base = build(args) if build else f"Confirm this action? {name} -- this can't be undone."
    return f'{base} Reply "yes" to confirm, or "no" to cancel.'


_AFFIRMATIVE_REPLIES = {
    "yes", "y", "yeah", "yep", "yup", "confirm", "confirmed", "do it",
    "go ahead", "sure", "please do", "ok", "okay", "correct", "affirmative",
}
_NEGATIVE_REPLIES = {
    "no", "n", "nope", "nah", "cancel", "don't", "dont", "stop",
    "never mind", "nevermind", "negative", "no thanks", "no thank you",
}


def _classify_yes_no(text: str) -> str | None:
    """Classify a plain-text reply to a pending confirmation prompt as
    "confirm", "decline", or None (ambiguous -- caller should ask again
    rather than guess, per the project's autonomy-over-prescription
    principle: never silently assume what a destructive action's reply
    meant)."""
    normalized = text.strip().lower().rstrip(".!")
    if normalized in _AFFIRMATIVE_REPLIES:
        return "confirm"
    if normalized in _NEGATIVE_REPLIES:
        return "decline"
    return None


def _build_oai_messages(session: Session, conversation_id: str) -> list[dict[str, Any]]:
    """Reconstruct the OpenAI-shaped message list from persisted
    ChatMessage history -- shared by the fresh-message and resume-
    after-confirmation entry points so they build identical context."""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.id)
    )
    history = session.scalars(stmt).all()

    oai_messages: list[dict[str, Any]] = []
    current = now()
    oai_messages.append({
        "role": "system",
        "content": f"Current date and time: {current.strftime('%A, %Y-%m-%d %H:%M')}. "
        "Resolve relative dates ('tomorrow', 'next Tuesday') against this.",
    })
    for msg in history:
        if msg.role == "system":
            oai_messages.append({"role": "system", "content": msg.content})
        elif msg.role == "user":
            oai_messages.append({"role": "user", "content": msg.content or ""})
        elif msg.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_arguments:
                entry["tool_calls"] = msg.tool_arguments
            oai_messages.append(entry)
        elif msg.role == "tool":
            oai_messages.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id or "",
                "content": msg.content or "",
            })
    return oai_messages


async def _agentic_loop(
    session: Session,
    conversation_id: str,
    oai_messages: list[dict[str, Any]],
    client: Any,
    model: str,
    provider: str,
) -> AsyncGenerator[str, None]:
    """The shared multi-turn tool-calling loop, used both for a fresh
    user message and for resuming after a destructive tool call was
    confirmed/declined. Pauses (yields `tool:confirm_needed` and stops
    entirely, without a `done` event) the moment it hits an unresolved
    call to a tool in DESTRUCTIVE_TOOLS -- the caller is responsible
    for later resuming via resolve_pending_tool() once the user
    responds.

    Commits (not just flushes) after every round's writes, right before
    the next blocking client.chat.completions.create() call -- see
    _run_sync_tool_round()'s docstring for the full reasoning (same fix,
    same reproduced failure, applied to this async/streaming path too)."""
    max_tool_rounds = 10
    tools = _select_tools_for_provider(_last_user_content(oai_messages), provider)
    try:
        for _round in range(max_tool_rounds):
            stream = await client.chat.completions.create(
                model=model,
                messages=oai_messages,
                tools=tools,
                stream=True,
            )

            content_buffer = ""
            tool_calls_buffer: dict[int, dict[str, Any]] = {}

            async for chunk in stream:
                choice = chunk.choices[0]
                delta = choice.delta

                if delta.content:
                    content_buffer += delta.content
                    yield f"event: token\ndata: {json.dumps({'text': delta.content})}\n\n"

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_buffer:
                            tool_calls_buffer[idx] = {
                                "id": tc.id or "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc.function:
                            if tc.function.name:
                                tool_calls_buffer[idx]["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_buffer[idx]["function"]["arguments"] += tc.function.arguments

            if not tool_calls_buffer:
                session.add(ChatMessage(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=content_buffer,
                ))
                session.commit()
                yield f"event: done\ndata: {json.dumps({'conversation_id': conversation_id})}\n\n"
                return

            assistant_tool_calls = list(tool_calls_buffer.values())
            oai_messages.append({
                "role": "assistant",
                "content": content_buffer or "",
                "tool_calls": assistant_tool_calls,
            })
            session.add(ChatMessage(
                conversation_id=conversation_id,
                role="assistant",
                content=content_buffer,
                tool_arguments=assistant_tool_calls,
            ))
            session.commit()

            paused = False
            for tc in assistant_tool_calls:
                name = tc["function"]["name"]
                args_raw = tc["function"]["arguments"]

                if name in DESTRUCTIVE_TOOLS:
                    yield f"event: tool:confirm_needed\ndata: {json.dumps({'conversation_id': conversation_id, 'call_id': tc['id'], 'name': name, 'args': _parse_tool_args(args_raw)})}\n\n"
                    paused = True
                    break

                yield f"event: tool:call\ndata: {json.dumps({'name': name, 'args': _parse_tool_args(args_raw)})}\n\n"
                tool_result = _execute_tool(session, name, args_raw)
                yield f"event: tool:result\ndata: {json.dumps({'name': name, 'result': tool_result})}\n\n"

                oai_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })
                session.add(ChatMessage(
                    conversation_id=conversation_id,
                    role="tool",
                    content=tool_result,
                    tool_name=name,
                    tool_arguments=_parse_tool_args(args_raw),
                    tool_call_id=tc["id"],
                ))
            # Commit here, before looping back around to the next
            # blocking client.chat.completions.create() call above.
            session.commit()

            if paused:
                return

    except Exception as e:
        yield f"event: error\ndata: {json.dumps({'message': describe_llm_error(e, provider, model)})}\n\n"
        return

    yield f"event: done\ndata: {json.dumps({'conversation_id': conversation_id, 'note': 'Max tool rounds exceeded'})}\n\n"


async def resolve_pending_tool(
    session: Session,
    conversation_id: str,
    call_id: str,
    decision: str,
) -> AsyncGenerator[str, None]:
    """Resume a conversation after the user responds to a
    `tool:confirm_needed` pause -- either executing the destructive
    tool for real (decision="confirm") or recording that it was
    declined (decision="decline"), then continuing the agentic loop
    exactly as if that tool call had resolved normally."""
    pending = _pending_tool_calls(session, conversation_id)
    if pending is None:
        yield f"event: error\ndata: {json.dumps({'message': 'Nothing is awaiting confirmation in this conversation.'})}\n\n"
        return

    tool_calls, resolved_ids = pending
    target = next((tc for tc in tool_calls if tc["id"] == call_id and tc["id"] not in resolved_ids), None)
    if target is None:
        yield f"event: error\ndata: {json.dumps({'message': 'That action is no longer awaiting confirmation.'})}\n\n"
        return

    name = target["function"]["name"]
    args_raw = target["function"]["arguments"]

    if decision == "confirm":
        yield f"event: tool:call\ndata: {json.dumps({'name': name, 'args': _parse_tool_args(args_raw)})}\n\n"
        tool_result = _execute_tool(session, name, args_raw)
        yield f"event: tool:result\ndata: {json.dumps({'name': name, 'result': tool_result})}\n\n"
    else:
        tool_result = _decline_result(
            "The user declined this action. Do not perform it -- ask what they'd "
            "like to do instead if that seems useful."
        )
        yield f"event: tool:declined\ndata: {json.dumps({'name': name})}\n\n"

    session.add(ChatMessage(
        conversation_id=conversation_id,
        role="tool",
        content=tool_result,
        tool_name=name,
        tool_arguments=_parse_tool_args(args_raw),
        tool_call_id=call_id,
    ))
    session.commit()

    # If the same round had more tool calls queued up after this one,
    # keep working through them (in case the model batched several
    # calls together) -- pausing again immediately if another
    # destructive one comes up next.
    remaining = [tc for tc in tool_calls if tc["id"] != call_id and tc["id"] not in resolved_ids]
    for tc in remaining:
        rname = tc["function"]["name"]
        rargs_raw = tc["function"]["arguments"]
        if rname in DESTRUCTIVE_TOOLS:
            yield f"event: tool:confirm_needed\ndata: {json.dumps({'conversation_id': conversation_id, 'call_id': tc['id'], 'name': rname, 'args': _parse_tool_args(rargs_raw)})}\n\n"
            return
        yield f"event: tool:call\ndata: {json.dumps({'name': rname, 'args': _parse_tool_args(rargs_raw)})}\n\n"
        rresult = _execute_tool(session, rname, rargs_raw)
        yield f"event: tool:result\ndata: {json.dumps({'name': rname, 'result': rresult})}\n\n"
        session.add(ChatMessage(
            conversation_id=conversation_id,
            role="tool",
            content=rresult,
            tool_name=rname,
            tool_arguments=_parse_tool_args(rargs_raw),
            tool_call_id=tc["id"],
        ))
    # Commit (not just flush) before _agentic_loop()'s own blocking LLM
    # call -- see that function's docstring for why.
    session.commit()

    try:
        client, model, provider = get_llm_client(session, async_mode=True)
    except LlmNotConfiguredError as e:
        yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
        return

    oai_messages = _build_oai_messages(session, conversation_id)
    async for event in _agentic_loop(session, conversation_id, oai_messages, client, model, provider):
        yield event


def create_conversation(session: Session) -> str:
    """Create a new conversation and return its ID."""
    conv_id = str(uuid.uuid4())
    session.add(ChatMessage(
        conversation_id=conv_id,
        role="system",
        content=_SYSTEM_PROMPT,
    ))
    session.flush()
    return conv_id


def _run_sync_tool_round(
    session: Session,
    conversation_id: str,
    oai_messages: list[dict[str, Any]],
    client: Any,
    model: str,
    provider: str,
) -> dict[str, Any]:
    """The shared multi-turn tool-calling loop for the synchronous
    (non-streaming) send path -- used both for a fresh user message and
    for resuming after a destructive tool call was confirmed/declined
    via a plain yes/no reply (the path Telegram uses, since it has no
    dialog UI to intercept the SSE `tool:confirm_needed` event the way
    the web chat panel does). Returns immediately, without executing
    anything, the moment it hits an unresolved call to a tool in
    DESTRUCTIVE_TOOLS -- mirrors _agentic_loop()'s pause behavior for
    the streaming path. Deliberately does NOT persist the confirmation
    prompt itself as a ChatMessage (same as the streaming path doesn't
    persist its `tool:confirm_needed` event) -- only the eventual tool
    role response, once resolved, is real conversation history.

    Commits (not just flushes) after every round's writes, right before
    control returns to the top of the loop for the next blocking LLM
    call -- a FastAPI request-scoped session (api/deps.py::get_db) spans
    the whole request as one transaction by default, which would
    otherwise mean the SQLite write lock stays held for as long as
    *every* LLM round-trip in a multi-tool exchange takes combined, not
    just the DB writes themselves. Reproduced for real: a slow Ollama
    response left the background notification scheduler's own unrelated
    write hitting `database is locked` moments later. Committing
    progressively also matches the actual user-facing semantics better,
    not just the concurrency fix -- if round 2 of a 3-round exchange
    fails, whatever round 1 already did for real (e.g. a task that
    genuinely got created) should stay done, not vanish, since the user
    already effectively saw it happen."""
    max_tool_rounds = 10
    tools = _select_tools_for_provider(_last_user_content(oai_messages), provider)
    try:
        for _round in range(max_tool_rounds):
            response = client.chat.completions.create(
                model=model,
                messages=oai_messages,
                tools=tools,
            )

            choice = response.choices[0]
            msg = choice.message

            if not msg.tool_calls:
                final_content = msg.content or ""
                session.add(ChatMessage(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=final_content,
                ))
                session.commit()
                return {"role": "assistant", "content": final_content}

            assistant_tool_calls = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
            oai_messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": assistant_tool_calls,
            })
            session.add(ChatMessage(
                conversation_id=conversation_id,
                role="assistant",
                content=msg.content or "",
                tool_arguments=assistant_tool_calls,
            ))
            session.commit()

            for tc in assistant_tool_calls:
                name = tc["function"]["name"]
                args_raw = tc["function"]["arguments"]

                if name in DESTRUCTIVE_TOOLS:
                    session.commit()
                    return {"role": "assistant", "content": _describe_destructive_action(name, _parse_tool_args(args_raw))}

                tool_result = _execute_tool(session, name, args_raw)
                oai_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })
                session.add(ChatMessage(
                    conversation_id=conversation_id,
                    role="tool",
                    content=tool_result,
                    tool_name=name,
                    tool_arguments=_parse_tool_args(args_raw),
                    tool_call_id=tc["id"],
                ))
            # Commit here, before looping back around to the next
            # blocking client.chat.completions.create() call above.
            session.commit()

    except Exception as e:
        return {"role": "assistant", "content": describe_llm_error(e, provider, model)}

    return {"role": "assistant", "content": "I had trouble processing that. Could you try rephrasing?"}


def _resolve_pending_tool_sync(
    session: Session,
    conversation_id: str,
    decision: str,
    tool_calls: list[dict[str, Any]],
    resolved_ids: set[str],
    target: dict[str, Any],
) -> dict[str, Any]:
    """Synchronous counterpart to resolve_pending_tool() -- executes or
    declines the confirmed destructive call, works through any other
    tool calls batched into the same round (pausing again if another
    destructive one comes up), then continues the loop for a real
    follow-up completion. Used by send_message() when the caller (only
    Telegram today) replies with a plain yes/no instead of hitting
    POST /chat/messages/resolve-tool."""
    name = target["function"]["name"]
    args_raw = target["function"]["arguments"]

    if decision == "confirm":
        tool_result = _execute_tool(session, name, args_raw)
    else:
        tool_result = _decline_result(
            "The user declined this action. Do not perform it -- ask what they'd "
            "like to do instead if that seems useful."
        )
    session.add(ChatMessage(
        conversation_id=conversation_id,
        role="tool",
        content=tool_result,
        tool_name=name,
        tool_arguments=_parse_tool_args(args_raw),
        tool_call_id=target["id"],
    ))
    session.commit()

    remaining = [tc for tc in tool_calls if tc["id"] != target["id"] and tc["id"] not in resolved_ids]
    for tc in remaining:
        rname = tc["function"]["name"]
        rargs_raw = tc["function"]["arguments"]
        if rname in DESTRUCTIVE_TOOLS:
            return {"role": "assistant", "content": _describe_destructive_action(rname, _parse_tool_args(rargs_raw))}
        rresult = _execute_tool(session, rname, rargs_raw)
        session.add(ChatMessage(
            conversation_id=conversation_id,
            role="tool",
            content=rresult,
            tool_name=rname,
            tool_arguments=_parse_tool_args(rargs_raw),
            tool_call_id=tc["id"],
        ))
    # Commit (not just flush) before _run_sync_tool_round()'s own
    # blocking LLM call -- see that function's docstring for why.
    session.commit()

    try:
        client, model, provider = get_llm_client(session, async_mode=False)
    except LlmNotConfiguredError as e:
        return {"role": "assistant", "content": str(e)}

    oai_messages = _build_oai_messages(session, conversation_id)
    return _run_sync_tool_round(session, conversation_id, oai_messages, client, model, provider)


def send_message(
    session: Session,
    conversation_id: str,
    content: str,
) -> dict[str, Any]:
    """Send a user message and get the assistant's response (one round
    of tool calls). Also the Telegram bot's sole message-processing
    path, which has no dialog UI -- so unlike a truly fresh message,
    this checks first whether a destructive tool call is still awaiting
    confirmation from a previous turn, and if so, tries to read `content`
    as that yes/no answer instead of treating it as a new request. On an
    ambiguous reply, asks again rather than guessing (never silently
    assume what a reply to a destructive-action prompt meant) --
    deliberately does not persist that exchange, so the pending round's
    DB state (a tool_calls-bearing assistant message with no tool
    response yet) stays exactly as _pending_tool_calls() expects until a
    real decision resolves it."""
    pending = _pending_tool_calls(session, conversation_id)
    if pending is not None:
        tool_calls, resolved_ids = pending
        target = next((tc for tc in tool_calls if tc["id"] not in resolved_ids), None)
        if target is not None:
            decision = _classify_yes_no(content)
            if decision is None:
                name = target["function"]["name"]
                args_raw = target["function"]["arguments"]
                clarify = _describe_destructive_action(name, _parse_tool_args(args_raw))
                return {"role": "assistant", "content": f"Sorry, I need a clear yes or no. {clarify}"}
            return _resolve_pending_tool_sync(session, conversation_id, decision, tool_calls, resolved_ids, target)

    # Save the user message
    session.add(ChatMessage(
        conversation_id=conversation_id,
        role="user",
        content=content,
    ))
    session.flush()
    # Commit (not just flush) before the blocking LLM call that's about to
    # happen in _run_sync_tool_round() -- see the module-level note above
    # _run_sync_tool_round() for why holding the write lock across a slow
    # provider response is a real problem, reproduced with a real Ollama
    # instance colliding with the background scheduler.
    session.commit()

    try:
        client, model, provider = get_llm_client(session, async_mode=False)
    except LlmNotConfiguredError as e:
        return {"role": "assistant", "content": str(e)}

    oai_messages = _build_oai_messages(session, conversation_id)
    return _run_sync_tool_round(session, conversation_id, oai_messages, client, model, provider)


def get_history(session: Session, conversation_id: str) -> list[dict[str, Any]]:
    """Get all messages in a conversation."""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.id)
    )
    messages = []
    for msg in session.scalars(stmt).all():
        d = model_to_dict(msg)
        if d["role"] == "system":
            continue
        messages.append(d)
    return messages


def list_conversations(session: Session) -> list[dict[str, Any]]:
    """List all conversations with their first user message as a summary."""
    min_id_subq = (
        select(
            ChatMessage.conversation_id,
            func.min(ChatMessage.id).label("min_id"),
        )
        .where(ChatMessage.role == "user")
        .group_by(ChatMessage.conversation_id)
        .subquery()
    )
    stmt = (
        select(ChatMessage)
        .join(min_id_subq, ChatMessage.id == min_id_subq.c.min_id)
        .order_by(desc(ChatMessage.created_at))
    )
    convs: list[dict[str, Any]] = []
    for msg in session.scalars(stmt).all():
        convs.append({
            "id": msg.conversation_id,
            "summary": (msg.content or "").strip()[:80],
            "created_at": str(msg.created_at),
        })
    return convs


def delete_conversation(session: Session, conversation_id: str) -> bool:
    """Delete all messages in a conversation (i.e. delete the conversation)."""
    msgs = session.query(ChatMessage).where(ChatMessage.conversation_id == conversation_id).all()
    if not msgs:
        raise ValueError(f"Conversation {conversation_id} not found")
    for msg in msgs:
        session.delete(msg)
    return True


def get_conversation_id_by_message(session: Session, message_id: int) -> str | None:
    """Get the conversation ID that a message belongs to."""
    msg = session.get(ChatMessage, message_id)
    if msg is None:
        return None
    return msg.conversation_id


async def send_message_stream(
    session: Session,
    conversation_id: str,
    content: str,
) -> AsyncGenerator[str, None]:
    """Stream a user message and get the assistant's response via SSE.

    Yields SSE-formatted events:
    - event: token, data: <text chunk>
    - event: tool:call, data: {"name": "...", "args": {...}}
    - event: tool:result, data: {"name": "...", "result": "..."}
    - event: tool:confirm_needed, data: {"conversation_id", "call_id",
      "name", "args"} -- a destructive tool call is waiting on the
      user; the stream ends here without a `done` event. Call
      resolve_pending_tool() with the user's decision to continue.
    - event: done, data: {"conversation_id": "..."}
    """
    try:
        client, model, provider = get_llm_client(session, async_mode=True)
    except LlmNotConfiguredError as e:
        yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
        return

    # If a previous turn paused on a destructive tool call and the user
    # is now sending something new instead of responding to it, resolve
    # it as declined so the conversation never gets permanently stuck.
    _auto_decline_stale_pending(session, conversation_id)

    session.add(ChatMessage(
        conversation_id=conversation_id,
        role="user",
        content=content,
    ))
    # Commit (not just flush) before _agentic_loop()'s own blocking LLM
    # call -- see that function's docstring for why.
    session.commit()

    oai_messages = _build_oai_messages(session, conversation_id)
    async for event in _agentic_loop(session, conversation_id, oai_messages, client, model, provider):
        yield event
