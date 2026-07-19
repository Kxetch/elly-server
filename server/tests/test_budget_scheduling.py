"""Tests for domain/budget.py's recurring-entry logic: calendar event
generation, the monthly summary (including how recurring entries count
toward a period), the trend series, and upcoming events.

2026-07-13 is used as the fixed "today" throughout via monkeypatching
domain/budget.py's `now` import directly (same pattern as
test_habits_scheduling.py -- it's imported by name into that module's
namespace, not called through an injectable clock).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import BudgetEntry, Event, Habit, HabitLog
from elly_server.domain import budget as budget_domain

TODAY = datetime(2026, 7, 13, 12, 0)


def _backdate(entry_id: int, created_at: datetime) -> None:
    """Directly set a BudgetEntry's created_at, bypassing its
    default=now() column default -- that default resolves through
    db/models.py's own `now` import, a separate binding from
    domain/budget.py's (see _freeze above), so simply monkeypatching
    the latter doesn't affect a freshly-created row's created_at. Only
    needed for tests that simulate "this recurring entry was actually
    set up in an earlier month" -- real usage never needs this since
    `now()` is never mocked outside tests."""
    with get_session() as session:
        entry = session.get(BudgetEntry, entry_id)
        assert entry is not None
        entry.created_at = created_at


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(BudgetEntry))


def _freeze(monkeypatch: pytest.MonkeyPatch, at: datetime = TODAY) -> None:
    monkeypatch.setattr(budget_domain, "now", lambda: at)


# ---- generate_scheduled_budget_events -------------------------------------


def test_recurring_entry_generates_a_calendar_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    with get_session() as session:
        entry = budget_domain.create_entry(
            session, kind="income", category="Salary", amount=3000, is_recurring=True, recurrence_day_of_month=25
        )
    with get_session() as session:
        events = session.query(Event).filter(Event.budget_entry_id == entry["id"]).all()
    assert len(events) > 0
    assert events[0].title == "Salary"


def test_generate_is_idempotent_no_duplicate_events(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    with get_session() as session:
        entry = budget_domain.create_entry(
            session, kind="expense", category="Rent", amount=1200, is_recurring=True, recurrence_day_of_month=1
        )
    with get_session() as session:
        count_before = session.query(Event).filter(Event.budget_entry_id == entry["id"]).count()
    with get_session() as session:
        budget_domain.generate_scheduled_budget_events(session)
    with get_session() as session:
        count_after = session.query(Event).filter(Event.budget_entry_id == entry["id"]).count()
    assert count_before == count_after


def test_auto_event_false_skips_event_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    with get_session() as session:
        entry = budget_domain.create_entry(
            session,
            kind="expense",
            category="Rent",
            amount=1200,
            is_recurring=True,
            recurrence_day_of_month=1,
            auto_event=False,
        )
    with get_session() as session:
        events = session.query(Event).filter(Event.budget_entry_id == entry["id"]).all()
    assert events == []


def test_one_off_entries_never_generate_events(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    with get_session() as session:
        budget_domain.log_expense(session, category="Coffee", amount=4.5)
    with get_session() as session:
        budget_domain.generate_scheduled_budget_events(session)
    with get_session() as session:
        events = session.query(Event).all()
    assert events == []


def test_recurrence_day_clamped_to_last_day_of_short_month(monkeypatch: pytest.MonkeyPatch) -> None:
    """recurrence_day_of_month=31 in a 30-day (or 28/29-day) month
    clamps to that month's actual last day."""
    _freeze(monkeypatch, datetime(2026, 1, 15))  # so Feb 2026 is in the near horizon
    with get_session() as session:
        entry = budget_domain.create_entry(
            session, kind="expense", category="Subscription", amount=10, is_recurring=True, recurrence_day_of_month=31
        )
    with get_session() as session:
        events = (
            session.query(Event)
            .filter(Event.budget_entry_id == entry["id"])
            .order_by(Event.start_at)
            .all()
        )
    feb_2026_events = [e for e in events if e.start_at.year == 2026 and e.start_at.month == 2]
    assert len(feb_2026_events) == 1
    assert feb_2026_events[0].start_at.day == 28  # 2026 is not a leap year


# ---- get_summary ------------------------------------------------------


def test_summary_defaults_to_current_month(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    with get_session() as session:
        budget_domain.log_expense(session, category="Coffee", amount=4.5)
    with get_session() as session:
        summary = budget_domain.get_summary(session)
    assert summary["since"] == "2026-07-01"
    assert summary["until"] == "2026-07-31"
    assert summary["total_expenses"] == 4.5


def test_summary_computes_net_income_minus_expenses(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    with get_session() as session:
        budget_domain.log_income(session, category="Freelance", amount=500.0)
        budget_domain.log_expense(session, category="Coffee", amount=4.5)
    with get_session() as session:
        summary = budget_domain.get_summary(session)
    assert summary["total_income"] == 500.0
    assert summary["total_expenses"] == 4.5
    assert summary["net"] == 495.5


def test_summary_by_category_only_includes_expenses_sorted_descending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    with get_session() as session:
        budget_domain.log_income(session, category="Salary", amount=3000.0)
        budget_domain.log_expense(session, category="Groceries", amount=50.0)
        budget_domain.log_expense(session, category="Coffee", amount=4.5)
    with get_session() as session:
        summary = budget_domain.get_summary(session)
    categories = [c["category"] for c in summary["by_category"]]
    assert categories == ["Groceries", "Coffee"]
    assert "Salary" not in categories


def test_summary_includes_recurring_entry_occurring_this_month(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recurring rent entry set up on the 1st, checked mid-month --
    even though 'today' (the 13th) is past the 1st, the whole month's
    summary still counts it (see get_summary's forward-looking design)."""
    _freeze(monkeypatch, TODAY)  # July 13
    with get_session() as session:
        entry = budget_domain.create_entry(
            session, kind="expense", category="Rent", amount=1200, is_recurring=True, recurrence_day_of_month=1
        )
    _backdate(entry["id"], datetime(2026, 7, 1))
    with get_session() as session:
        summary = budget_domain.get_summary(session)
    assert summary["total_expenses"] == 1200.0


def test_summary_includes_recurring_entry_scheduled_later_this_month(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Salary lands on the 25th; today is the 13th -- the month's
    summary still counts it as expected income for the month."""
    _freeze(monkeypatch, TODAY)  # July 13
    with get_session() as session:
        budget_domain.create_entry(
            session, kind="income", category="Salary", amount=3000, is_recurring=True, recurrence_day_of_month=25
        )
    with get_session() as session:
        summary = budget_domain.get_summary(session)
    assert summary["total_income"] == 3000.0


def test_summary_excludes_recurring_entry_set_up_after_the_period(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recurring bill created today didn't exist last month -- a
    past month's summary must not retroactively show it (would
    misrepresent history, e.g. a brand new job's salary appearing to
    have been received months before the job existed)."""
    _freeze(monkeypatch, TODAY)  # created July 13
    with get_session() as session:
        budget_domain.create_entry(
            session, kind="expense", category="Rent", amount=1200, is_recurring=True, recurrence_day_of_month=1
        )
    with get_session() as session:
        summary = budget_domain.get_summary(session, since="2026-06-01", until="2026-06-30")
    assert summary["total_expenses"] == 0.0


def test_summary_excludes_recurring_entry_on_a_different_day_of_month(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch, datetime(2026, 6, 1))  # created well before the checked period
    with get_session() as session:
        budget_domain.create_entry(
            session, kind="expense", category="Rent", amount=1200, is_recurring=True, recurrence_day_of_month=1
        )
    with get_session() as session:
        # Ask about a period that never includes the 1st.
        summary = budget_domain.get_summary(session, since="2026-07-02", until="2026-07-31")
    assert summary["total_expenses"] == 0.0


def test_summary_custom_date_range(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, datetime(2026, 3, 15))
    with get_session() as session:
        budget_domain.log_expense(session, category="Coffee", amount=4.5)
    with get_session() as session:
        summary = budget_domain.get_summary(session, since="2026-01-01", until="2026-12-31")
    assert summary["total_expenses"] == 4.5


# ---- get_monthly_trend -----------------------------------------------


def test_monthly_trend_returns_requested_number_of_months_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch, TODAY)  # July 2026
    with get_session() as session:
        trend = budget_domain.get_monthly_trend(session, months=3)
    months = [m["month"] for m in trend["months"]]
    assert months == ["2026-05", "2026-06", "2026-07"]


def test_monthly_trend_reflects_recurring_entries_from_creation_onward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recurring entry set up in an earlier month shows up in every
    month's trend from then on -- but not before it existed (see
    test_monthly_trend_excludes_months_before_the_entry_existed)."""
    _freeze(monkeypatch, TODAY)  # July 13
    with get_session() as session:
        entry = budget_domain.create_entry(
            session, kind="income", category="Salary", amount=3000, is_recurring=True, recurrence_day_of_month=25
        )
    _backdate(entry["id"], datetime(2026, 5, 1))  # simulate it having existed since May
    with get_session() as session:
        trend = budget_domain.get_monthly_trend(session, months=3)  # May, June, July
    for month in trend["months"]:
        assert month["income"] == 3000.0


def test_monthly_trend_excludes_months_before_the_entry_existed(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, TODAY)  # created July 13 -- did not exist in May/June
    with get_session() as session:
        budget_domain.create_entry(
            session, kind="income", category="Salary", amount=3000, is_recurring=True, recurrence_day_of_month=25
        )
    with get_session() as session:
        trend = budget_domain.get_monthly_trend(session, months=3)  # May, June, July
    by_month = {m["month"]: m["income"] for m in trend["months"]}
    assert by_month["2026-05"] == 0.0
    assert by_month["2026-06"] == 0.0
    assert by_month["2026-07"] == 3000.0  # July 25 occurrence is after the July 13 creation


# ---- list_upcoming ------------------------------------------------------


def test_list_upcoming_returns_events_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, TODAY)  # July 13
    with get_session() as session:
        budget_domain.create_entry(
            session, kind="income", category="Salary", amount=3000, is_recurring=True, recurrence_day_of_month=25
        )
    with get_session() as session:
        upcoming = budget_domain.list_upcoming(session, days=30)
    assert len(upcoming) >= 1
    assert upcoming[0]["category"] == "Salary"
    assert upcoming[0]["date"] == "2026-07-25"


def test_list_upcoming_excludes_events_beyond_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, TODAY)  # July 13
    with get_session() as session:
        budget_domain.create_entry(
            session, kind="expense", category="Annual fee", amount=50, is_recurring=True, recurrence_day_of_month=1
        )
    with get_session() as session:
        upcoming = budget_domain.list_upcoming(session, days=5)
    # Next occurrence (Aug 1) is more than 5 days out from July 13.
    assert upcoming == []


def test_list_upcoming_sorted_chronologically(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, TODAY)
    with get_session() as session:
        budget_domain.create_entry(
            session, kind="expense", category="Rent", amount=1200, is_recurring=True, recurrence_day_of_month=1
        )
        budget_domain.create_entry(
            session, kind="income", category="Salary", amount=3000, is_recurring=True, recurrence_day_of_month=25
        )
    with get_session() as session:
        upcoming = budget_domain.list_upcoming(session, days=60)
    dates = [u["date"] for u in upcoming]
    assert dates == sorted(dates)
