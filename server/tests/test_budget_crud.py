"""Tests for domain/budget.py's CRUD surface -- create/update/delete
validation and cents<->float conversion. Scheduling/summary/trend math
is covered separately in test_budget_scheduling.py."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import BudgetEntry, Event, Habit, HabitLog
from elly_server.domain import budget


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(BudgetEntry))


# ---- create_entry validation -----------------------------------------


def test_create_entry_rejects_invalid_kind() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="kind must be one of"):
            budget.create_entry(session, kind="savings", category="Test", amount=10.0)


def test_create_entry_rejects_blank_category() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="can't be empty"):
            budget.create_entry(session, kind="expense", category="   ", amount=10.0)


def test_create_entry_rejects_zero_amount() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="greater than 0"):
            budget.create_entry(session, kind="expense", category="Test", amount=0)


def test_create_entry_rejects_negative_amount() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="greater than 0"):
            budget.create_entry(session, kind="expense", category="Test", amount=-5)


def test_create_entry_recurring_requires_day_of_month() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="recurrence_day_of_month is required"):
            budget.create_entry(session, kind="expense", category="Rent", amount=1200, is_recurring=True)


def test_create_entry_rejects_out_of_range_day_of_month() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="between 1 and 31"):
            budget.create_entry(
                session,
                kind="expense",
                category="Rent",
                amount=1200,
                is_recurring=True,
                recurrence_day_of_month=32,
            )


# ---- quantity / unit_label (dev note #4, Sprint 6) ------------------------


def test_create_entry_defaults_quantity_to_one_with_no_unit_label() -> None:
    """Backward-compatible default -- nothing about existing behavior
    changes for an entry that doesn't care about quantity at all."""
    with get_session() as session:
        entry = budget.create_entry(session, kind="expense", category="Coffee", amount=4.5)
    assert entry["quantity"] == 1
    assert entry["unit_label"] is None


def test_create_entry_with_quantity_and_unit_label() -> None:
    with get_session() as session:
        entry = budget.create_entry(
            session, kind="expense", category="Coke Zero", amount=4.5, quantity=3, unit_label="bottle"
        )
    assert entry["quantity"] == 3
    assert entry["unit_label"] == "bottle"
    # amount_cents is ALWAYS the total, never a per-unit price -- see
    # BudgetEntry's own docstring. $4.50 total for 3 bottles, not $4.50
    # each.
    assert entry["amount"] == 4.5


def test_create_entry_rejects_quantity_below_one() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="quantity must be at least 1"):
            budget.create_entry(session, kind="expense", category="Coffee", amount=4.5, quantity=0)


def test_update_entry_can_change_quantity_and_unit_label() -> None:
    with get_session() as session:
        entry = budget.create_entry(session, kind="expense", category="Coke Zero", amount=1.5)
    with get_session() as session:
        updated = budget.update_entry(session, entry["id"], amount=4.5, quantity=3, unit_label="bottle")
    assert updated["quantity"] == 3
    assert updated["unit_label"] == "bottle"
    assert updated["amount"] == 4.5


def test_update_entry_rejects_quantity_below_one() -> None:
    with get_session() as session:
        entry = budget.create_entry(session, kind="expense", category="Coffee", amount=4.5)
    with pytest.raises(ValueError, match="quantity must be at least 1"):
        with get_session() as session:
            budget.update_entry(session, entry["id"], quantity=0)


def test_update_entry_without_touching_quantity_leaves_it_unchanged() -> None:
    with get_session() as session:
        entry = budget.create_entry(session, kind="expense", category="Coke Zero", amount=4.5, quantity=3)
    with get_session() as session:
        updated = budget.update_entry(session, entry["id"], amount=5.0)
    assert updated["quantity"] == 3


# ---- cents <-> float conversion ---------------------------------------


def test_amount_round_trips_through_cents() -> None:
    with get_session() as session:
        entry = budget.log_expense(session, category="Coffee", amount=4.5)
    assert entry["amount"] == 4.5


def test_amount_rounds_to_nearest_cent() -> None:
    with get_session() as session:
        entry = budget.log_expense(session, category="Odd", amount=4.999)
    assert entry["amount"] == 5.0


# ---- log_expense / log_income convenience wrappers ---------------------


def test_log_expense_creates_one_off_expense() -> None:
    with get_session() as session:
        entry = budget.log_expense(session, category="Groceries", amount=45.20, note="weekly shop")
    assert entry["kind"] == "expense"
    assert entry["is_recurring"] is False
    assert entry["note"] == "weekly shop"


def test_log_income_creates_one_off_income() -> None:
    with get_session() as session:
        entry = budget.log_income(session, category="Freelance", amount=250.0)
    assert entry["kind"] == "income"
    assert entry["is_recurring"] is False


def test_log_expense_does_not_generate_a_calendar_event() -> None:
    with get_session() as session:
        budget.log_expense(session, category="Coffee", amount=4.5)
    with get_session() as session:
        events = session.query(Event).all()
    assert events == []


# ---- update_entry -------------------------------------------------------


def test_update_entry_partial_update() -> None:
    with get_session() as session:
        entry = budget.log_expense(session, category="Coffee", amount=4.5)
    with get_session() as session:
        updated = budget.update_entry(session, entry["id"], amount=5.0)
    assert updated["category"] == "Coffee"
    assert updated["amount"] == 5.0


def test_update_entry_not_found_raises() -> None:
    with get_session() as session:
        with pytest.raises(ValueError, match="not found"):
            budget.update_entry(session, 999, amount=10.0)


# ---- delete_entry ---------------------------------------------------------


def test_delete_entry_returns_true_and_removes_it() -> None:
    with get_session() as session:
        entry = budget.log_expense(session, category="Coffee", amount=4.5)
    with get_session() as session:
        assert budget.delete_entry(session, entry["id"]) is True
    with get_session() as session:
        with pytest.raises(ValueError, match="not found"):
            budget.get_entry(session, entry["id"])


def test_delete_entry_missing_returns_false() -> None:
    with get_session() as session:
        assert budget.delete_entry(session, 999) is False


def test_delete_entry_removes_associated_calendar_events() -> None:
    with get_session() as session:
        entry = budget.create_entry(
            session, kind="expense", category="Rent", amount=1200, is_recurring=True, recurrence_day_of_month=1
        )
    with get_session() as session:
        events_before = session.query(Event).filter(Event.budget_entry_id == entry["id"]).count()
    assert events_before > 0
    with get_session() as session:
        budget.delete_entry(session, entry["id"])
    with get_session() as session:
        events_after = session.query(Event).filter(Event.budget_entry_id == entry["id"]).count()
    assert events_after == 0


# ---- list_entries ---------------------------------------------------------


def test_list_entries_filters_by_kind() -> None:
    with get_session() as session:
        budget.log_expense(session, category="Coffee", amount=4.5)
        budget.log_income(session, category="Salary", amount=3000.0)
    with get_session() as session:
        expenses = budget.list_entries(session, kind="expense")
    assert len(expenses) == 1
    assert expenses[0]["category"] == "Coffee"


def test_list_entries_respects_limit() -> None:
    with get_session() as session:
        for i in range(5):
            budget.log_expense(session, category=f"Item{i}", amount=1.0)
    with get_session() as session:
        result = budget.list_entries(session, limit=2)
    assert len(result) == 2


# ---- list_recent (dedup for "tap to repeat" chips) ------------------------


def test_list_recent_deduplicates_by_category_and_amount() -> None:
    with get_session() as session:
        budget.log_expense(session, category="Coffee", amount=4.5)
        budget.log_expense(session, category="Coffee", amount=4.5)
        budget.log_expense(session, category="Coffee", amount=4.5)
    with get_session() as session:
        recent = budget.list_recent(session)
    assert len(recent) == 1


def test_list_recent_keeps_distinct_amounts_separate() -> None:
    with get_session() as session:
        budget.log_expense(session, category="Coffee", amount=4.5)
        budget.log_expense(session, category="Coffee", amount=5.5)
    with get_session() as session:
        recent = budget.list_recent(session)
    assert len(recent) == 2


def test_list_recent_excludes_recurring_entries() -> None:
    with get_session() as session:
        budget.create_entry(
            session, kind="expense", category="Rent", amount=1200, is_recurring=True, recurrence_day_of_month=1
        )
        budget.log_expense(session, category="Coffee", amount=4.5)
    with get_session() as session:
        recent = budget.list_recent(session)
    assert len(recent) == 1
    assert recent[0]["category"] == "Coffee"


def test_list_recent_respects_kind_filter() -> None:
    with get_session() as session:
        budget.log_income(session, category="Freelance", amount=250.0)
    with get_session() as session:
        recent_expenses = budget.list_recent(session, kind="expense")
        recent_income = budget.list_recent(session, kind="income")
    assert recent_expenses == []
    assert len(recent_income) == 1


# ---- list_categories -----------------------------------------------------


def test_list_categories_returns_sorted_distinct_names() -> None:
    with get_session() as session:
        budget.log_expense(session, category="Zebra", amount=1.0)
        budget.log_expense(session, category="Apple", amount=1.0)
        budget.log_expense(session, category="Apple", amount=2.0)
    with get_session() as session:
        cats = budget.list_categories(session)
    assert cats == ["Apple", "Zebra"]


def test_list_categories_respects_kind_filter() -> None:
    with get_session() as session:
        budget.log_expense(session, category="Coffee", amount=1.0)
        budget.log_income(session, category="Salary", amount=1.0)
    with get_session() as session:
        expense_cats = budget.list_categories(session, kind="expense")
    assert expense_cats == ["Coffee"]
