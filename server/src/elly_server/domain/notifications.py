"""Opt-in native macOS notifications (gentle, 1-2/day).

Design: no nagging, no guilt trips. These are awareness nudges --
"here's what your day looks like" and "here's how it went" -- never
"you missed X" or "you should do Y". Easy to silence: just call
`update_prefs(enabled=False)` or toggle in the UI at any time.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from elly_server.db.models import NotificationPref
from elly_server.db.serialize import model_to_dict
from elly_server.domain import calendar, habits, tasks
from elly_server.timeutil import now

logger = logging.getLogger("elly_server")


def _ensure_prefs(session: Session) -> NotificationPref:
    prefs = session.scalars(select(NotificationPref).limit(1)).first()
    if prefs is None:
        prefs = NotificationPref()
        session.add(prefs)
        session.flush()
    return prefs


def get_prefs(session: Session) -> dict[str, Any]:
    return model_to_dict(_ensure_prefs(session))


def update_prefs(
    session: Session,
    enabled: Optional[bool] = None,
    morning_time: Optional[str] = None,
    evening_time: Optional[str] = None,
) -> dict[str, Any]:
    prefs = _ensure_prefs(session)
    if enabled is not None:
        prefs.enabled = enabled
    if morning_time is not None:
        prefs.morning_time = morning_time
    if evening_time is not None:
        prefs.evening_time = evening_time
    session.flush()
    return model_to_dict(prefs)


def _send_notification(title: str, subtitle: str, body: str) -> None:
    """Send a real macOS notification via osascript."""
    script = (
        f'display notification "{body}" '
        f'with title "{title}" subtitle "{subtitle}"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        logger.exception("Failed to send macOS notification")


# A built-in macOS system sound -- no bundled audio asset needed, works
# out of the box on any Mac. "Glass" is short, distinct, and not
# alarming/jarring (matches the app's no-shame, never-alarming design
# principle -- see AGENTS.md -- even an "alarm" here should be
# attention-getting, not anxiety-inducing).
_ALARM_SOUND_PATH = "/System/Library/Sounds/Glass.aiff"


def send_native_notification(title: str, subtitle: str, body: str, *, play_sound: bool = False) -> None:
    """Public entry point for anything outside this module that needs a
    native OS notification -- currently domain/reminders.py, alongside
    this module's own morning/evening check-ins. macOS-only for now
    (see PLAN.md section 0.2's Sprint 4 -- Windows/Linux native
    delivery is a documented future follow-up, not built here); a
    non-macOS host silently no-ops rather than raising, since native
    delivery failing must never block a reminder's other delivery
    channel (Telegram).

    `play_sound` is for "alarm"-kind reminders specifically -- a plain
    check-in or "notification"-kind reminder uses the notification
    system's own default sound only.
    """
    import platform

    if platform.system() != "Darwin":
        logger.info("Native notification skipped -- not running on macOS")
        return

    _send_notification(title, subtitle, body)

    if play_sound:
        try:
            subprocess.run(
                ["afplay", _ALARM_SOUND_PATH],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            logger.exception("Failed to play alarm sound")


def _morning_message(session: Session) -> tuple[str, str, str]:
    today_events = calendar.list_today(session)
    pending = tasks.list_pending_tasks(session)
    event_count = len(today_events)
    task_count = len(pending)

    parts = []
    if event_count == 1:
        parts.append("1 event on your calendar today")
    elif event_count > 1:
        parts.append(f"{event_count} events on your calendar today")

    if task_count == 1:
        parts.append("1 pending task")
    elif task_count > 1:
        parts.append(f"{task_count} pending tasks")
    elif task_count == 0 and not parts:
        parts.append("a clear day ahead")

    event_info = ""
    if event_count > 0 and event_count <= 3:
        names = [e["title"] for e in today_events]
        event_info = " — " + ", ".join(names)

    body = ", and ".join(parts) + event_info
    return "Elly", "Good morning", body


def _evening_message(session: Session) -> tuple[str, str, str]:
    habit_statuses = habits.list_all_habit_statuses(session)
    done = sum(1 for h in habit_statuses if h["last_logged"] == str(now().date()))

    total = len(habit_statuses)
    if total == 0:
        body = "No habits to check in on. A quiet day is fine."
    elif done == total:
        body = f"All {total} habit{'s' if total > 1 else ''} logged today. Nice."
    elif done == 0:
        body = "No habits logged today — that's okay. Tomorrow's another day."
    else:
        body = f"{done} of {total} habits logged today. Every bit counts."

    return "Elly", "Evening check-in", body


def check_and_send(session: Session) -> int:
    """Check if a notification should be sent now. Returns how many were sent (0-1).

    Called by the background scheduler every ~60 seconds. Idempotent:
    each notification slot only fires once per date.
    """
    prefs = _ensure_prefs(session)
    if not prefs.enabled:
        return 0

    today_str = str(now().date())
    current_time = now().time()
    sent = 0

    if prefs.morning_sent_date != today_str:
        h, m = prefs.morning_time.split(":")
        scheduled = time(int(h), int(m))
        if current_time >= scheduled:
            title, subtitle, body = _morning_message(session)
            _send_notification(title, subtitle, body)
            prefs.morning_sent_date = today_str
            session.flush()
            sent += 1

    if prefs.evening_sent_date != today_str:
        h, m = prefs.evening_time.split(":")
        scheduled = time(int(h), int(m))
        if current_time >= scheduled:
            title, subtitle, body = _evening_message(session)
            _send_notification(title, subtitle, body)
            prefs.evening_sent_date = today_str
            session.flush()
            sent += 1

    return sent


def send_test(session: Session) -> None:
    _send_notification("Elly", "Test notification", "If you see this, notifications are working.")
