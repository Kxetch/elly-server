"""Tests for domain/reminders.py -- the Sprint 4 reminders/alarms engine.

Deliberately thorough (see PLAN.md section 0.2's Sprint 4 note on this)
given how easy time-based logic is to get subtly wrong: trigger_at
computation, idempotency, cascade-delete, and the habit-specific daily
recompute all get direct coverage.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import Event, Habit, HabitLog, Reminder, Task
from elly_server.domain import calendar, habits, reminders, tasks
from elly_server.timeutil import now


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with get_session() as session:
        session.execute(delete(Reminder))
        session.execute(delete(Event))
        session.execute(delete(HabitLog))
        session.execute(delete(Habit))
        session.execute(delete(Task))


@pytest.fixture(autouse=True)
def _never_send_real_native_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent any test from popping a real macOS notification/sound --
    same isolation rationale as conftest.py's _never_touch_real_os_keyring,
    just for a different real-OS-side-effect risk. No test in this file
    configures a Telegram bot token, so _deliver_telegram already
    naturally short-circuits before ever reaching the network -- this
    only needs to guard the native-notification path."""
    from elly_server.domain import notifications

    monkeypatch.setattr(notifications, "send_native_notification", lambda *a, **k: None)


# ---- _compute_trigger_at / set_reminder --------------------------------


def test_set_reminder_for_task_computes_trigger_at_from_due_date() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="Submit form", due_at="2026-08-01T10:00:00")
    with get_session() as session:
        reminder = reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=-15)
    assert reminder["trigger_at"] == "2026-08-01T09:45:00"


def test_set_reminder_for_task_without_due_date_raises() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="No due date")
    with pytest.raises(ValueError, match="no due date"):
        with get_session() as session:
            reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=0)


def test_set_reminder_for_event_computes_trigger_at_from_start() -> None:
    with get_session() as session:
        event = calendar.create_event(session, title="Dentist", start_at="2026-08-01T15:00:00")
    with get_session() as session:
        reminder = reminders.set_reminder(session, "event", event["id"], "alarm", offset_minutes=-60)
    assert reminder["trigger_at"] == "2026-08-01T14:00:00"
    assert reminder["kind"] == "alarm"


def test_set_reminder_for_habit_computes_trigger_at_from_scheduled_start() -> None:
    with get_session() as session:
        habit = habits.create_habit(session, name="Workout", label="fitness", scheduled_start="09:00")
    with get_session() as session:
        reminder = reminders.set_reminder(session, "habit", habit["id"], "notification", offset_minutes=-10)
    today = now().date()
    expected = f"{today.isoformat()}T08:50:00"
    assert reminder["trigger_at"] == expected


def test_set_reminder_for_habit_without_scheduled_start_raises() -> None:
    with get_session() as session:
        habit = habits.create_habit(session, name="Simple habit")  # no schedule
    with pytest.raises(ValueError, match="no scheduled time block"):
        with get_session() as session:
            reminders.set_reminder(session, "habit", habit["id"], "notification", offset_minutes=0)


def test_set_reminder_rejects_unknown_target_type() -> None:
    with pytest.raises(ValueError, match="target_type"):
        with get_session() as session:
            reminders.set_reminder(session, "budget_entry", 1, "notification", offset_minutes=0)


def test_set_reminder_rejects_unknown_kind() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="X", due_at="2026-08-01T10:00:00")
    with pytest.raises(ValueError, match="kind"):
        with get_session() as session:
            reminders.set_reminder(session, "task", task["id"], "siren", offset_minutes=0)


def test_set_reminder_for_nonexistent_target_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        with get_session() as session:
            reminders.set_reminder(session, "task", 99999, "notification", offset_minutes=0)


def test_set_reminder_replaces_any_existing_reminder_for_the_same_target() -> None:
    """Exactly one reminder per target -- confirmed 2026-07-15."""
    with get_session() as session:
        task = tasks.create_task(session, title="X", due_at="2026-08-01T10:00:00")
    with get_session() as session:
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=-15)
    with get_session() as session:
        reminders.set_reminder(session, "task", task["id"], "alarm", offset_minutes=-30)

    with get_session() as session:
        all_reminders = (
            session.query(Reminder)
            .filter(Reminder.target_type == "task", Reminder.target_id == task["id"])
            .all()
        )
    assert len(all_reminders) == 1
    # Reflects the *second* call's values, not the first's -- confirms
    # replacement actually happened rather than the second call being a
    # silent no-op alongside a leftover first row.
    assert all_reminders[0].kind == "alarm"
    assert all_reminders[0].offset_minutes == -30


# ---- get / delete -------------------------------------------------------


def test_get_reminder_for_returns_none_when_absent() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="X", due_at="2026-08-01T10:00:00")
    with get_session() as session:
        assert reminders.get_reminder_for(session, "task", task["id"]) is None


def test_delete_reminder_for_returns_false_when_absent() -> None:
    with get_session() as session:
        assert reminders.delete_reminder_for(session, "task", 12345) is False


def test_delete_reminder_for_removes_it() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="X", due_at="2026-08-01T10:00:00")
    with get_session() as session:
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=0)
    with get_session() as session:
        assert reminders.delete_reminder_for(session, "task", task["id"]) is True
    with get_session() as session:
        assert reminders.get_reminder_for(session, "task", task["id"]) is None


# ---- list_reminders (Settings management view) --------------------------


def test_list_reminders_includes_target_title_ordered_soonest_first() -> None:
    with get_session() as session:
        later = tasks.create_task(session, title="Later one", due_at="2026-09-01T10:00:00")
        sooner = tasks.create_task(session, title="Sooner one", due_at="2026-08-01T10:00:00")
        reminders.set_reminder(session, "task", later["id"], "notification", offset_minutes=0)
        reminders.set_reminder(session, "task", sooner["id"], "notification", offset_minutes=0)

    with get_session() as session:
        listed = reminders.list_reminders(session)

    assert [r["target_title"] for r in listed] == ["Sooner one", "Later one"]


def test_list_reminders_is_empty_when_none_exist() -> None:
    with get_session() as session:
        assert reminders.list_reminders(session) == []


def test_list_reminders_skips_a_stale_entry_whose_target_vanished() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="X", due_at="2026-08-01T10:00:00")
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=0)
        task_id = task["id"]

    with get_session() as session:
        # Bypass cascade-deleting delete_task() to simulate a stale row.
        session.query(Task).filter(Task.id == task_id).delete()

    with get_session() as session:
        assert reminders.list_reminders(session) == []


# ---- cascade-delete on target deletion ----------------------------------


def test_deleting_a_task_cascades_to_its_reminder() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="X", due_at="2026-08-01T10:00:00")
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=0)
    with get_session() as session:
        tasks.delete_task(session, task["id"])
    with get_session() as session:
        assert reminders.get_reminder_for(session, "task", task["id"]) is None


def test_deleting_a_task_with_children_cascades_reminders_for_children_too() -> None:
    with get_session() as session:
        parent = tasks.create_task(session, title="Parent")
        children = tasks.breakdown_task(session, parent["id"], [{"title": "Step 1", "due_at": "2026-08-01T10:00:00"}])
        child = children[0]
        reminders.set_reminder(session, "task", child["id"], "notification", offset_minutes=0)
    with get_session() as session:
        tasks.delete_task(session, parent["id"])
    with get_session() as session:
        assert reminders.get_reminder_for(session, "task", child["id"]) is None


def test_deleting_an_event_cascades_to_its_reminder() -> None:
    with get_session() as session:
        event = calendar.create_event(session, title="X", start_at="2026-08-01T10:00:00")
        reminders.set_reminder(session, "event", event["id"], "notification", offset_minutes=0)
    with get_session() as session:
        calendar.delete_event(session, event["id"])
    with get_session() as session:
        assert reminders.get_reminder_for(session, "event", event["id"]) is None


def test_deleting_a_habit_cascades_to_its_reminder() -> None:
    with get_session() as session:
        habit = habits.create_habit(session, name="Workout", label="fitness", scheduled_start="09:00")
        reminders.set_reminder(session, "habit", habit["id"], "notification", offset_minutes=0)
    with get_session() as session:
        habits.delete_habit(session, habit["id"])
    with get_session() as session:
        assert reminders.get_reminder_for(session, "habit", habit["id"]) is None


def test_archiving_a_habit_also_removes_its_reminder() -> None:
    """Same reasoning as the future-calendar-events cleanup: archiving
    explicitly promises 'it just stops showing up here'."""
    with get_session() as session:
        habit = habits.create_habit(session, name="Workout", label="fitness", scheduled_start="09:00")
        reminders.set_reminder(session, "habit", habit["id"], "notification", offset_minutes=0)
    with get_session() as session:
        habits.set_habit_active(session, habit["id"], False)
    with get_session() as session:
        assert reminders.get_reminder_for(session, "habit", habit["id"]) is None


# ---- reschedule recomputation -------------------------------------------


def test_rescheduling_a_task_due_date_recomputes_its_reminder() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="X", due_at="2026-08-01T10:00:00")
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=-15)
    with get_session() as session:
        tasks.update_task(session, task["id"], due_at="2026-08-05T12:00:00")
    with get_session() as session:
        reminder = reminders.get_reminder_for(session, "task", task["id"])
    assert reminder["trigger_at"] == "2026-08-05T11:45:00"


def test_rescheduling_an_event_recomputes_its_reminder() -> None:
    with get_session() as session:
        event = calendar.create_event(session, title="X", start_at="2026-08-01T10:00:00")
        reminders.set_reminder(session, "event", event["id"], "notification", offset_minutes=-30)
    with get_session() as session:
        calendar.reschedule_event(session, event["id"], start_at="2026-08-10T18:00:00")
    with get_session() as session:
        reminder = reminders.get_reminder_for(session, "event", event["id"])
    assert reminder["trigger_at"] == "2026-08-10T17:30:00"


def test_recompute_clears_fired_at_so_a_rescheduled_reminder_can_fire_again() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="X", due_at="2026-08-01T10:00:00")
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=0)
    with get_session() as session:
        row = session.query(Reminder).filter(Reminder.target_type == "task").first()
        row.fired_at = now()

    with get_session() as session:
        tasks.update_task(session, task["id"], due_at="2026-09-01T10:00:00")
    with get_session() as session:
        reminder = reminders.get_reminder_for(session, "task", task["id"])
    assert reminder["fired_at"] is None


def test_recompute_with_no_existing_reminder_is_a_safe_no_op() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="X", due_at="2026-08-01T10:00:00")
    with get_session() as session:
        result = reminders.recompute_reminder_for_target(session, "task", task["id"])
    assert result is None


# ---- check_and_send_reminders: one-shot task/event behavior -------------


def test_check_and_send_fires_a_due_task_reminder_and_marks_it_fired() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="Overdue thing", due_at="2020-01-01T00:00:00")
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=0)

    with get_session() as session:
        sent = reminders.check_and_send_reminders(session)
    assert sent == 1

    with get_session() as session:
        reminder = reminders.get_reminder_for(session, "task", task["id"])
    assert reminder["fired_at"] is not None


def test_check_and_send_is_idempotent_never_double_fires() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="Overdue thing", due_at="2020-01-01T00:00:00")
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=0)

    with get_session() as session:
        first = reminders.check_and_send_reminders(session)
    with get_session() as session:
        second = reminders.check_and_send_reminders(session)

    assert first == 1
    assert second == 0


def test_check_and_send_does_not_fire_a_future_reminder() -> None:
    with get_session() as session:
        far_future = (now() + timedelta(days=365)).isoformat()
        task = tasks.create_task(session, title="Not yet", due_at=far_future)
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=0)

    with get_session() as session:
        sent = reminders.check_and_send_reminders(session)
    assert sent == 0


def test_check_and_send_skips_delivering_for_an_already_completed_task_but_still_marks_it_handled() -> None:
    with get_session() as session:
        task = tasks.create_task(session, title="Already done", due_at="2020-01-01T00:00:00")
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=0)
        tasks.complete_task(session, task["id"])

    with get_session() as session:
        sent = reminders.check_and_send_reminders(session)
    assert sent == 0  # not delivered -- would be a nag about something already done

    with get_session() as session:
        reminder = reminders.get_reminder_for(session, "task", task["id"])
    assert reminder["fired_at"] is not None  # but still marked handled, not re-checked forever


def test_check_and_send_does_not_abandon_other_reminders_if_one_fails_unexpectedly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bad reminder must never take down the whole batch -- a bug
    processing reminder A should not prevent reminder B (due in the
    same tick) from still being delivered."""
    with get_session() as session:
        broken = tasks.create_task(session, title="Broken one", due_at="2020-01-01T00:00:00")
        fine = tasks.create_task(session, title="Fine one", due_at="2020-01-01T00:00:00")
        reminders.set_reminder(session, "task", broken["id"], "notification", offset_minutes=0)
        reminders.set_reminder(session, "task", fine["id"], "notification", offset_minutes=0)
        broken_id = broken["id"]

    real_target_info = reminders._target_info

    def _flaky_target_info(session, target_type, target_id, today):
        if target_type == "task" and target_id == broken_id:
            raise RuntimeError("simulated unexpected bug")
        return real_target_info(session, target_type, target_id, today)

    monkeypatch.setattr(reminders, "_target_info", _flaky_target_info)

    with get_session() as session:
        sent = reminders.check_and_send_reminders(session)

    assert sent == 1  # the fine one still got delivered despite the broken one blowing up

    with get_session() as session:
        # The broken one is untouched (never got a chance to mark
        # fired_at) -- it'll be retried on the next tick rather than
        # silently lost.
        broken_reminder = reminders.get_reminder_for(session, "task", broken_id)
    assert broken_reminder["fired_at"] is None


def test_check_and_send_cleans_up_a_stale_reminder_whose_target_vanished() -> None:
    """Defensive: cascade-delete should always remove a reminder before
    this ever happens, but a stray row must never crash the scheduler
    or fire forever -- confirm it self-heals instead."""
    with get_session() as session:
        task = tasks.create_task(session, title="X", due_at="2020-01-01T00:00:00")
        reminders.set_reminder(session, "task", task["id"], "notification", offset_minutes=0)
        task_id = task["id"]

    with get_session() as session:
        # Bypass the normal cascade-deleting delete_task() on purpose,
        # to simulate a reminder whose target is already gone.
        session.query(Task).filter(Task.id == task_id).delete()

    with get_session() as session:
        sent = reminders.check_and_send_reminders(session)
    assert sent == 0

    with get_session() as session:
        assert reminders.get_reminder_for(session, "task", task_id) is None


# ---- check_and_send_reminders: habit daily-recurrence behavior ----------


def test_habit_reminder_fires_relative_to_scheduled_start_when_due() -> None:
    with get_session() as session:
        habit = habits.create_habit(session, name="Workout", label="fitness", scheduled_start="00:01")
        reminders.set_reminder(session, "habit", habit["id"], "notification", offset_minutes=0)

    with get_session() as session:
        sent = reminders.check_and_send_reminders(session)
    assert sent == 1


def test_habit_reminder_does_not_fire_before_its_scheduled_time_today(monkeypatch: pytest.MonkeyPatch) -> None:
    # Frozen, not real-clock-relative: adding hours to the *actual*
    # current time and formatting only HH:MM (no date) used to wrap past
    # midnight whenever this test ran after ~18:00 local time, producing
    # a time that's numerically *earlier* than "now" and making the
    # reminder incorrectly eligible to fire -- a real, deterministically
    # reproducible bug in the test itself, not in check_and_send_reminders
    # (confirmed via `git stash` against the pre-fix code). A fixed,
    # comfortably-mid-day frozen time removes any wall-clock dependency.
    frozen_now = datetime(2026, 1, 1, 9, 0)
    monkeypatch.setattr(reminders, "now", lambda: frozen_now)

    with get_session() as session:
        far_future_time = (frozen_now + timedelta(hours=6)).strftime("%H:%M")
        habit = habits.create_habit(session, name="Workout", label="fitness", scheduled_start=far_future_time)
        reminders.set_reminder(session, "habit", habit["id"], "notification", offset_minutes=0)

    with get_session() as session:
        sent = reminders.check_and_send_reminders(session)
    assert sent == 0


def test_habit_reminder_refires_the_next_day_after_firing_today() -> None:
    """The key difference from task/event reminders: 'fired' means
    'fired today', not 'ever fired'."""
    with get_session() as session:
        habit = habits.create_habit(session, name="Workout", label="fitness", scheduled_start="00:01")
        reminders.set_reminder(session, "habit", habit["id"], "notification", offset_minutes=0)

    with get_session() as session:
        first = reminders.check_and_send_reminders(session)
    assert first == 1

    # Simulate "yesterday" by backdating fired_at.
    with get_session() as session:
        row = session.query(Reminder).filter(Reminder.target_type == "habit").first()
        row.fired_at = now() - timedelta(days=1)

    with get_session() as session:
        second = reminders.check_and_send_reminders(session)
    assert second == 1  # fires again -- yesterday's firing doesn't count for today


def test_habit_reminder_does_not_fire_twice_on_the_same_day() -> None:
    with get_session() as session:
        habit = habits.create_habit(session, name="Workout", label="fitness", scheduled_start="00:01")
        reminders.set_reminder(session, "habit", habit["id"], "notification", offset_minutes=0)

    with get_session() as session:
        first = reminders.check_and_send_reminders(session)
    with get_session() as session:
        second = reminders.check_and_send_reminders(session)

    assert first == 1
    assert second == 0


def test_habit_reminder_skips_delivery_if_already_logged_today() -> None:
    """No-nag principle: a reminder about a habit you've already done
    today would just be annoying."""
    with get_session() as session:
        habit = habits.create_habit(session, name="Workout", label="fitness", scheduled_start="00:01")
        reminders.set_reminder(session, "habit", habit["id"], "notification", offset_minutes=0)
        habits.log_habit(session, habit_id=habit["id"])

    with get_session() as session:
        sent = reminders.check_and_send_reminders(session)
    assert sent == 0


# ---- deliver_reminder: both channels independently best-effort ----------


def test_deliver_reminder_calls_native_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    from elly_server.domain import notifications

    calls = []
    monkeypatch.setattr(notifications, "send_native_notification", lambda *a, **k: calls.append((a, k)))

    with get_session() as session:
        reminders.deliver_reminder(session, "Test title", "alarm")

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert "Test title" in args
    assert kwargs.get("play_sound") is True


def test_deliver_reminder_native_failure_does_not_raise_or_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both delivery channels are best-effort -- a failure in native
    delivery must not propagate and crash the caller (and, in
    check_and_send_reminders' loop, must not abandon every other
    reminder still waiting to be processed in that same tick)."""
    from elly_server.domain import notifications

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("simulated native notification failure")

    monkeypatch.setattr(notifications, "send_native_notification", _boom)

    with get_session() as session:
        # Must not raise, even though native delivery is forced to fail.
        reminders.deliver_reminder(session, "Test title", "notification")


def test_deliver_reminder_telegram_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """_deliver_telegram catches its own exceptions -- a bad token/
    network error must never block native delivery for the same
    reminder."""
    from elly_server.db.models import TelegramLink
    from elly_server.domain import notifications
    from elly_server.domain import settings as settings_domain

    monkeypatch.setattr(notifications, "send_native_notification", lambda *a, **k: None)

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("simulated telegram failure")

    monkeypatch.setattr(reminders, "_send_telegram_async", _boom)

    with get_session() as session:
        settings_domain.set_telegram_bot_token(session, "fake-token-for-test")
        # Needs a real paired chat_id too, or _deliver_telegram would
        # short-circuit before ever reaching _send_telegram_async at
        # all -- that's a *different*, already-covered no-op path, not
        # the failure path this test is actually about.
        link = session.query(TelegramLink).first()
        if link is None:
            link = TelegramLink()
            session.add(link)
        link.chat_id = 123456

    with get_session() as session:
        # Must not raise, even though Telegram delivery is forced to fail.
        reminders.deliver_reminder(session, "Test title", "notification")
