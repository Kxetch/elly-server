"""Tests for domain/chat.py's tool dispatch layer: _parse_tool_args and
_execute_tool -- the glue between OpenAI's function-calling format and
Elly's domain functions. Does not call any real LLM API."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import BudgetEntry, Event, Habit, HabitLog, Task
from elly_server.domain.chat import _execute_tool, _parse_tool_args  # noqa: SLF001


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(Task))
        session.execute(delete(Event))
        session.execute(delete(BudgetEntry))


def test_parse_tool_args_passes_through_dict() -> None:
    assert _parse_tool_args({"a": 1}) == {"a": 1}


def test_parse_tool_args_decodes_json_string() -> None:
    assert _parse_tool_args('{"title": "Buy milk"}') == {"title": "Buy milk"}


def test_parse_tool_args_returns_empty_dict_for_other_types() -> None:
    assert _parse_tool_args(None) == {}
    assert _parse_tool_args(123) == {}


def test_execute_tool_unknown_name_returns_error_json() -> None:
    with get_session() as session:
        result = _execute_tool(session, "not_a_real_tool", {})
    assert json.loads(result) == {"error": "Unknown tool: not_a_real_tool"}


def test_execute_tool_creates_a_real_task() -> None:
    with get_session() as session:
        result = _execute_tool(session, "create_task", {"title": "From the LLM"})
    parsed = json.loads(result)
    assert parsed["title"] == "From the LLM"
    assert parsed["status"] == "open"


def test_execute_tool_accepts_json_string_args() -> None:
    """OpenAI streams tool call arguments as accumulated JSON strings,
    not dicts -- confirm that path works too, not just direct dicts."""
    with get_session() as session:
        result = _execute_tool(session, "create_task", '{"title": "Streamed args"}')
    parsed = json.loads(result)
    assert parsed["title"] == "Streamed args"


def test_execute_tool_log_habit_renames_habit_name_to_name() -> None:
    with get_session() as session:
        _execute_tool(session, "create_habit", {"name": "Drink water"})

    with get_session() as session:
        result = _execute_tool(session, "log_habit", {"habit_name": "water"})
    parsed = json.loads(result)
    assert "error" not in parsed
    assert parsed["name"] == "Drink water"
    assert parsed["total_completions"] == 1


def test_execute_tool_catches_domain_errors_as_error_json() -> None:
    with get_session() as session:
        result = _execute_tool(session, "complete_task", {"task_id": 999999})
    parsed = json.loads(result)
    assert "error" in parsed
    assert "not found" in parsed["error"]


def test_execute_tool_delete_habit_actually_deletes() -> None:
    with get_session() as session:
        habit = json.loads(_execute_tool(session, "create_habit", {"name": "Temp habit"}))

    with get_session() as session:
        result = json.loads(_execute_tool(session, "delete_habit", {"habit_id": habit["id"]}))
    assert result is True

    with get_session() as session:
        status = json.loads(_execute_tool(session, "get_habit_status", {}))
    assert status["habits"] == []


# ---- Budget tools -- regression coverage for a real bug: these were never
# wired into chat.py's tool list/dispatch map at all when the Budget page
# was built, so the in-app chat/Telegram LLM had no idea it existed. ----


def test_execute_tool_log_expense_creates_a_real_entry() -> None:
    with get_session() as session:
        result = json.loads(_execute_tool(session, "log_expense", {"category": "Coffee", "amount": 4.5}))
    assert "error" not in result
    assert result["kind"] == "expense"
    assert result["category"] == "Coffee"
    assert result["amount"] == 4.5
    assert result["is_recurring"] is False


def test_execute_tool_log_income_creates_a_real_entry() -> None:
    with get_session() as session:
        result = json.loads(_execute_tool(session, "log_income", {"category": "Freelance", "amount": 250}))
    assert "error" not in result
    assert result["kind"] == "income"


def test_execute_tool_create_recurring_budget_entry_forces_is_recurring_true() -> None:
    """The chat tool name doesn't map 1:1 to the domain function name/args
    (create_recurring_budget_entry -> budget.create_entry(is_recurring=True,
    ...)) -- confirm the dispatch wiring actually sets that flag, matching
    the equivalent MCP tool's behavior."""
    with get_session() as session:
        result = json.loads(_execute_tool(
            session,
            "create_recurring_budget_entry",
            {"kind": "income", "category": "Salary", "amount": 3000, "recurrence_day_of_month": 25},
        ))
    assert "error" not in result
    assert result["is_recurring"] is True
    assert result["recurrence_day_of_month"] == 25


def test_execute_tool_get_budget_summary_reflects_logged_entries() -> None:
    with get_session() as session:
        _execute_tool(session, "log_expense", {"category": "Coffee", "amount": 4.5})
    with get_session() as session:
        result = json.loads(_execute_tool(session, "get_budget_summary", {}))
    assert result["total_expenses"] == 4.5


def test_execute_tool_delete_budget_entry_actually_deletes() -> None:
    with get_session() as session:
        entry = json.loads(_execute_tool(session, "log_expense", {"category": "Temp", "amount": 1}))

    with get_session() as session:
        result = json.loads(_execute_tool(session, "delete_budget_entry", {"entry_id": entry["id"]}))
    assert result is True

    with get_session() as session:
        entries = json.loads(_execute_tool(session, "list_budget_entries", {}))
    assert entries == []
