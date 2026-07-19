"""Insights: turning logged data into the "visualize your life" payoff.

These functions return structured numbers, not prose -- the LLM (via
the MCP tool docstrings) is asked to turn them into a warm, descriptive
narrative. Keeping the business logic deterministic and the writing
conversational is a deliberate separation of concerns.
"""

from __future__ import annotations

from datetime import timedelta
from statistics import fmean
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from elly_server.db.models import HabitLog, Note, Task
from elly_server.domain.habits import list_all_habit_statuses
from elly_server.timeutil import now

_VALID_METRICS = {"mood", "energy", "habit_completions"}


def mood_trend(session: Session, days: int = 14) -> dict[str, Any]:
    since = now() - timedelta(days=days)
    stmt = (
        select(Note)
        .where(Note.type == "diary", Note.created_at >= since, Note.mood.is_not(None))
        .order_by(Note.created_at)
    )
    by_day: dict[str, list[int]] = {}
    energy_by_day: dict[str, list[int]] = {}
    for entry in session.scalars(stmt).all():
        day = entry.created_at.date().isoformat()
        by_day.setdefault(day, []).append(entry.mood)  # type: ignore[arg-type]
        if entry.energy is not None:
            energy_by_day.setdefault(day, []).append(entry.energy)

    trend = [
        {
            "date": day,
            "avg_mood": round(fmean(moods), 2),
            "avg_energy": round(fmean(energy_by_day[day]), 2) if day in energy_by_day else None,
            "entries": len(moods),
        }
        for day, moods in sorted(by_day.items())
    ]
    all_moods = [m for values in by_day.values() for m in values]
    return {
        "days_requested": days,
        "trend": trend,
        "overall_avg_mood": round(fmean(all_moods), 2) if all_moods else None,
    }


def _daily_series(session: Session, metric: str, since) -> dict[str, float]:
    if metric in ("mood", "energy"):
        column = Note.mood if metric == "mood" else Note.energy
        stmt = select(Note.created_at, column).where(
            Note.type == "diary", Note.created_at >= since, column.is_not(None)
        )
        by_day: dict[str, list[float]] = {}
        for created_at, value in session.execute(stmt).all():
            by_day.setdefault(created_at.date().isoformat(), []).append(float(value))
        return {day: fmean(values) for day, values in by_day.items()}

    if metric == "habit_completions":
        stmt = select(HabitLog.logged_at).where(HabitLog.logged_at >= since)
        counts: dict[str, int] = {}
        for (logged_at,) in session.execute(stmt).all():
            day = logged_at.date().isoformat()
            counts[day] = counts.get(day, 0) + 1
        return {day: float(count) for day, count in counts.items()}

    raise ValueError(f"Unknown metric {metric!r}. Use one of {sorted(_VALID_METRICS)}.")


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mean_x, mean_y = fmean(xs), fmean(ys)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = (var_x * var_y) ** 0.5
    return covariance / denom if denom else None


def correlate(session: Session, metric_a: str, metric_b: str, days: int = 30) -> dict[str, Any]:
    """Correlate two daily metrics ('mood', 'energy', or 'habit_completions')."""
    since = now() - timedelta(days=days)
    series_a = _daily_series(session, metric_a, since)
    series_b = _daily_series(session, metric_b, since)
    common_days = sorted(set(series_a) & set(series_b))

    if len(common_days) < 3:
        return {
            "metric_a": metric_a,
            "metric_b": metric_b,
            "days_available": len(common_days),
            "message": "Not enough overlapping data yet to correlate reliably.",
        }

    xs = [series_a[d] for d in common_days]
    ys = [series_b[d] for d in common_days]
    r = _pearson(xs, ys)
    return {
        "metric_a": metric_a,
        "metric_b": metric_b,
        "days_available": len(common_days),
        "correlation": round(r, 3) if r is not None else None,
        "series": [
            {"date": d, metric_a: series_a[d], metric_b: series_b[d]} for d in common_days
        ],
    }


def weekly_review(session: Session) -> dict[str, Any]:
    """Structured data for a weekly reflection -- numbers only; the
    model turns this into a warm narrative, never a scorecard."""
    end = now()
    start = end - timedelta(days=7)

    notes = session.scalars(select(Note).where(Note.created_at >= start)).all()
    diary_entries = [n for n in notes if n.type == "diary"]
    plain_notes = [n for n in notes if n.type != "diary"]
    mood_values = [n.mood for n in diary_entries if n.mood is not None]

    tasks_done = session.scalars(
        select(Task).where(Task.status == "done", Task.completed_at >= start)
    ).all()
    tasks_pending = session.scalars(select(Task).where(Task.status == "open")).all()

    return {
        "period": {"from": start.date().isoformat(), "to": end.date().isoformat()},
        # Deliberately excludes diary entries -- they're already their
        # own stat right below, and double-counting them here made the
        # two side-by-side numbers silently overlap (e.g. "5 notes
        # written, 2 diary entries" implied 7 total when it was really
        # 5, 2 of which were diary entries).
        "notes_written": len(plain_notes),
        "diary_entries": len(diary_entries),
        "avg_mood": round(fmean(mood_values), 2) if mood_values else None,
        "tasks_completed": len(tasks_done),
        "tasks_still_pending": len(tasks_pending),
        "habits": list_all_habit_statuses(session),
    }
