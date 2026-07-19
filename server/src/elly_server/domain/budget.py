"""Income and expense tracking (the Budget page).

Two kinds of BudgetEntry, both sharing one table (see db/models.py's
docstring for the full rationale):
- **One-off**: logged the moment it happens ("$4.50 on coffee just
  now") -- no calendar event, `created_at` is simply when it was
  logged (same convention as HabitLog.logged_at -- no way to log for
  a past/future date, matching that same existing limitation).
- **Recurring**: a monthly amount on a given day-of-month ("$1200 rent
  due on the 1st", "$3000 salary on the 25th") -- generates calendar
  `Event`s the same way a "finance"-labelled `Habit` used to (see
  generate_scheduled_budget_events below), via `_event_for_budget_entry_on_date`.

Amounts are stored as integer cents (`amount_cents`) and always
positive -- direction comes entirely from `kind` ("income" | "expense").
The public functions here all speak in plain float dollars/whatever
the user's chosen currency's major unit is (see AppSettings.currency);
`_to_cents`/`_from_cents` are the only place that conversion happens.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from elly_server.db.models import BudgetEntry, Event
from elly_server.db.serialize import model_to_dict
from elly_server.domain.validation import require_nonblank
from elly_server.timeutil import last_day_of_month, months_ahead, now, parse_datetime

VALID_KINDS = ("income", "expense")


def _to_cents(amount: float) -> int:
    if amount is None or amount <= 0:
        raise ValueError("amount must be greater than 0")
    return round(amount * 100)


def _from_cents(cents: int) -> float:
    return round(cents / 100, 2)


def _entry_dict(entry: BudgetEntry) -> dict[str, Any]:
    data = model_to_dict(entry)
    data["amount"] = _from_cents(data.pop("amount_cents"))
    return data


def _find_entry(session: Session, entry_id: int) -> BudgetEntry:
    entry = session.get(BudgetEntry, entry_id)
    if entry is None:
        raise ValueError(f"Budget entry {entry_id} not found")
    return entry


def _validate_quantity(quantity: int) -> None:
    if quantity < 1:
        raise ValueError("quantity must be at least 1")


def create_entry(
    session: Session,
    kind: str,
    category: str,
    amount: float,
    note: Optional[str] = None,
    color: Optional[str] = None,
    is_recurring: bool = False,
    recurrence_day_of_month: Optional[int] = None,
    auto_event: bool = True,
    quantity: int = 1,
    unit_label: Optional[str] = None,
) -> dict[str, Any]:
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
    if is_recurring:
        if recurrence_day_of_month is None:
            raise ValueError("recurrence_day_of_month is required when is_recurring is True")
        if not (1 <= recurrence_day_of_month <= 31):
            raise ValueError("recurrence_day_of_month must be between 1 and 31")
    _validate_quantity(quantity)

    entry = BudgetEntry(
        kind=kind,
        category=require_nonblank(category, "category"),
        color=color,
        amount_cents=_to_cents(amount),
        note=note,
        is_recurring=is_recurring,
        recurrence_day_of_month=recurrence_day_of_month if is_recurring else None,
        auto_event=auto_event,
        quantity=quantity,
        unit_label=unit_label,
    )
    session.add(entry)
    session.flush()
    if is_recurring and auto_event:
        generate_scheduled_budget_events(session)
    return _entry_dict(entry)


def log_expense(
    session: Session,
    category: str,
    amount: float,
    note: Optional[str] = None,
    color: Optional[str] = None,
) -> dict[str, Any]:
    """Log a one-off expense for right now -- not recurring, no calendar
    event. For a recurring bill, use create_entry(..., is_recurring=True)
    instead."""
    return create_entry(session, kind="expense", category=category, amount=amount, note=note, color=color)


def log_income(
    session: Session,
    category: str,
    amount: float,
    note: Optional[str] = None,
    color: Optional[str] = None,
) -> dict[str, Any]:
    """Log a one-off income entry for right now -- not recurring, no
    calendar event. For recurring income (e.g. salary), use
    create_entry(..., is_recurring=True) instead."""
    return create_entry(session, kind="income", category=category, amount=amount, note=note, color=color)


def update_entry(
    session: Session,
    entry_id: int,
    category: Optional[str] = None,
    amount: Optional[float] = None,
    note: Optional[str] = None,
    color: Optional[str] = None,
    quantity: Optional[int] = None,
    unit_label: Optional[str] = None,
) -> dict[str, Any]:
    entry = _find_entry(session, entry_id)
    if category is not None:
        entry.category = require_nonblank(category, "category")
    if amount is not None:
        entry.amount_cents = _to_cents(amount)
    if note is not None:
        entry.note = note
    if color is not None:
        entry.color = color
    if quantity is not None:
        _validate_quantity(quantity)
        entry.quantity = quantity
    if unit_label is not None:
        entry.unit_label = unit_label
    session.flush()
    return _entry_dict(entry)


def delete_entry(session: Session, entry_id: int) -> bool:
    """Delete a budget entry and any calendar events generated from it."""
    entry = session.get(BudgetEntry, entry_id)
    if entry is None:
        return False
    session.query(Event).filter(Event.budget_entry_id == entry_id).delete()
    session.delete(entry)
    return True


def get_entry(session: Session, entry_id: int) -> dict[str, Any]:
    return _entry_dict(_find_entry(session, entry_id))


def list_entries(
    session: Session,
    kind: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    stmt = select(BudgetEntry).order_by(BudgetEntry.created_at.desc()).limit(limit)
    if kind is not None:
        stmt = stmt.where(BudgetEntry.kind == kind)
    since_dt = parse_datetime(since)
    until_dt = parse_datetime(until)
    if since_dt is not None:
        stmt = stmt.where(BudgetEntry.created_at >= since_dt)
    if until_dt is not None:
        stmt = stmt.where(BudgetEntry.created_at <= until_dt)
    return [_entry_dict(e) for e in session.scalars(stmt).all()]


def list_recent(session: Session, kind: str = "expense", limit: int = 5) -> list[dict[str, Any]]:
    """Most recent *distinct* (category, amount) one-off entries of a
    given kind -- powers the "tap to repeat" quick-log chips in the UI
    (e.g. a daily coffee), deduplicated so ten identical coffee logs
    don't crowd out everything else."""
    stmt = (
        select(BudgetEntry)
        .where(BudgetEntry.kind == kind, BudgetEntry.is_recurring.is_(False))
        .order_by(BudgetEntry.created_at.desc())
        .limit(50)
    )
    seen: set[tuple[str, int]] = set()
    result: list[dict[str, Any]] = []
    for entry in session.scalars(stmt).all():
        key = (entry.category, entry.amount_cents)
        if key in seen:
            continue
        seen.add(key)
        result.append(_entry_dict(entry))
        if len(result) >= limit:
            break
    return result


def list_categories(session: Session, kind: Optional[str] = None) -> list[str]:
    """Distinct category names used so far -- powers autocomplete/quick
    pick in the UI."""
    stmt = select(BudgetEntry.category).distinct()
    if kind is not None:
        stmt = stmt.where(BudgetEntry.kind == kind)
    return sorted({row[0] for row in session.execute(stmt).all()})


def _recurring_occurs_in_period(entry: BudgetEntry, since_dt: datetime, until_dt: datetime) -> bool:
    """Does *entry* (a recurring entry) have an occurrence date falling
    within [since_dt, until_dt]? Computed directly from
    recurrence_day_of_month clamped per month, independent of whether a
    calendar Event was actually generated for it (auto_event=False
    skips event creation, but the entry still logically recurs every
    month) -- see get_summary below.

    Never counts an occurrence before `entry.created_at`'s own date --
    a recurring bill/income set up today didn't actually exist last
    month, so a past-months summary/trend must not retroactively show
    it (that would misrepresent history, e.g. a brand new job's salary
    appearing to have been received for months it wasn't).
    """
    if entry.recurrence_day_of_month is None:
        return False
    cursor = date(since_dt.year, since_dt.month, 1)
    end_date = until_dt.date()
    start_date = max(since_dt.date(), entry.created_at.date())
    # Capped iteration count -- a summary period spanning years' worth
    # of months would be unusual, but never loop unbounded.
    for _ in range(120):
        if cursor > end_date:
            return False
        last_day = last_day_of_month(cursor.year, cursor.month)
        occurrence = cursor.replace(day=min(entry.recurrence_day_of_month, last_day))
        if start_date <= occurrence <= end_date:
            return True
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return False


def get_summary(session: Session, since: Optional[str] = None, until: Optional[str] = None) -> dict[str, Any]:
    """Totals + expense-by-category breakdown for a period -- defaults
    to the current calendar month (the 1st through the last day, not
    just "so far") if since/until aren't given, so upcoming recurring
    bills/income already scheduled later this month count toward it --
    budgeting is inherently forward-looking within the month, not just
    a retrospective log. Powers the Budget dashboard's stat cards +
    category bar chart.

    One-off entries count if logged within the period; recurring
    entries count once per month they land on. See
    _recurring_occurs_in_period.
    """
    today = now().date()
    since_dt = parse_datetime(since) or datetime(today.year, today.month, 1)
    if until is not None:
        until_dt = parse_datetime(until)
    else:
        last_day = last_day_of_month(since_dt.year, since_dt.month)
        until_dt = datetime(since_dt.year, since_dt.month, last_day, 23, 59, 59)

    one_off = session.scalars(
        select(BudgetEntry).where(
            BudgetEntry.is_recurring.is_(False),
            BudgetEntry.created_at >= since_dt,
            BudgetEntry.created_at <= until_dt,
        )
    ).all()

    recurring_candidates = session.scalars(
        select(BudgetEntry).where(BudgetEntry.is_recurring.is_(True))
    ).all()
    recurring = [e for e in recurring_candidates if _recurring_occurs_in_period(e, since_dt, until_dt)]

    all_entries = list(one_off) + recurring
    total_income_cents = sum(e.amount_cents for e in all_entries if e.kind == "income")
    total_expense_cents = sum(e.amount_cents for e in all_entries if e.kind == "expense")

    by_category: dict[str, int] = {}
    for e in all_entries:
        if e.kind != "expense":
            continue
        by_category[e.category] = by_category.get(e.category, 0) + e.amount_cents

    return {
        "since": since_dt.date().isoformat(),
        "until": until_dt.date().isoformat(),
        "total_income": _from_cents(total_income_cents),
        "total_expenses": _from_cents(total_expense_cents),
        "net": _from_cents(total_income_cents - total_expense_cents),
        "by_category": [
            {"category": cat, "amount": _from_cents(cents)}
            for cat, cents in sorted(by_category.items(), key=lambda kv: -kv[1])
        ],
    }


def get_monthly_trend(session: Session, months: int = 6) -> dict[str, Any]:
    """Income/expense/net totals per month for the last *months* months
    (oldest first) -- powers the Budget dashboard's trend chart, same
    "structured numbers, not prose" shape convention as
    domain/insights.py::mood_trend."""
    today = now().date()
    month_starts: list[date] = []
    cursor = date(today.year, today.month, 1)
    for _ in range(months):
        month_starts.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    month_starts.reverse()

    series = []
    for month_start in month_starts:
        last_day = last_day_of_month(month_start.year, month_start.month)
        month_end = datetime(month_start.year, month_start.month, last_day, 23, 59, 59)
        summary = get_summary(
            session,
            since=datetime(month_start.year, month_start.month, 1).isoformat(),
            until=month_end.isoformat(),
        )
        series.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "income": summary["total_income"],
                "expenses": summary["total_expenses"],
                "net": summary["net"],
            }
        )
    return {"months": series}


def list_upcoming(session: Session, days: int = 30) -> list[dict[str, Any]]:
    """Upcoming recurring income/expense calendar events in the next
    *days* days -- powers the Budget dashboard's "coming up" section."""
    today_start = datetime.combine(now().date(), datetime.min.time())
    end = today_start + timedelta(days=days)
    stmt = (
        select(Event, BudgetEntry)
        .join(BudgetEntry, Event.budget_entry_id == BudgetEntry.id)
        .where(Event.start_at >= today_start, Event.start_at < end)
        .order_by(Event.start_at)
    )
    result = []
    for event, entry in session.execute(stmt).all():
        result.append(
            {
                "event_id": event.id,
                "budget_entry_id": entry.id,
                "date": event.start_at.date().isoformat(),
                "kind": entry.kind,
                "category": entry.category,
                "amount": _from_cents(entry.amount_cents),
                "color": entry.color,
            }
        )
    return result


def _event_for_budget_entry_on_date(
    session: Session, entry: BudgetEntry, target_date: date
) -> dict[str, Any] | None:
    """Create a calendar event for *entry* on *target_date*, unless one
    already exists (dedup by budget_entry_id + date). Mirrors
    domain/habits.py's _event_for_habit_on_date -- same pattern, no
    scheduled_start/end here since budget entries aren't given a
    specific clock time, just a day."""
    day_start = datetime(target_date.year, target_date.month, target_date.day)
    day_end = day_start + timedelta(days=1)
    existing = (
        session.execute(
            select(Event).where(
                Event.budget_entry_id == entry.id,
                Event.start_at >= day_start,
                Event.start_at < day_end,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return None

    start_dt = day_start.replace(hour=9, minute=0)
    end_dt = start_dt.replace(hour=10, minute=0)
    kind_label = "Income" if entry.kind == "income" else "Expense"
    event = Event(
        title=entry.category,
        start_at=start_dt,
        end_at=end_dt,
        description=f"{kind_label}: {_from_cents(entry.amount_cents)}",
        budget_entry_id=entry.id,
    )
    session.add(event)
    session.flush()
    return model_to_dict(event)


def generate_scheduled_budget_events(session: Session, days_ahead: Optional[int] = None) -> list[dict[str, Any]]:
    """Create calendar events for recurring budget entries, filling
    through end of next year by default -- mirrors
    domain/habits.py::generate_scheduled_events. Repeated calls only
    fill forward (dedup by budget_entry_id + date), safe to call as
    often as you like. Returns the list of newly created event dicts.
    """
    today = now().date()
    if days_ahead is None:
        end_of_next_year = date(today.year + 1, 12, 31)
        days_ahead = (end_of_next_year - today).days
    months_needed = max(1, (days_ahead // 28) + 2)
    created: list[dict[str, Any]] = []

    stmt = select(BudgetEntry).where(
        BudgetEntry.is_recurring.is_(True), BudgetEntry.auto_event.is_(True)
    )
    for entry in session.scalars(stmt).all():
        if entry.recurrence_day_of_month is None:
            continue
        for first_of_month in months_ahead(today, months_needed):
            last_day = last_day_of_month(first_of_month.year, first_of_month.month)
            target_date = first_of_month.replace(day=min(entry.recurrence_day_of_month, last_day))
            if target_date < today:
                continue
            ev = _event_for_budget_entry_on_date(session, entry, target_date)
            if ev:
                created.append(ev)

    return created
