"""SQLAlchemy models.

Single-user app: there's deliberately no `User` table (unlike the
old `ely` project). Every table just belongs to "you".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from elly_server.db.base import Base
from elly_server.db.encrypted_types import EncryptedJSON, EncryptedText
from elly_server.timeutil import now


class Note(Base):
    """A notebook note or a diary/journal entry.

    Notebook and diary are intentionally the same table: a diary entry
    is just a note with type="diary" and (usually) a mood/energy
    rating attached. Keeps the schema -- and the MVP -- small while
    still covering both of the pillars you asked for.

    `title`/`body` are encrypted at rest (see db/encrypted_types.py +
    domain/crypto.py) -- this is the single most sensitive content Elly
    stores. `title` was originally `String(200)`; encrypted ciphertext
    is longer than the plaintext it replaces, so it's now `Text` (see
    migration for the one-time data-encryption + column-type change).
    """

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(16), default="note")  # "note" | "diary"
    title: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    body: Mapped[str] = mapped_column(EncryptedText)
    mood: Mapped[Optional[int]] = mapped_column(nullable=True)  # 1-9
    energy: Mapped[Optional[int]] = mapped_column(nullable=True)  # 1-9
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class Event(Base):
    """A calendar event / time-block."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    start_at: Mapped[datetime]
    end_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    habit_id: Mapped[Optional[int]] = mapped_column(ForeignKey("habits.id"), nullable=True)
    budget_entry_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("budget_entries.id"), nullable=True
    )
    color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Task(Base):
    """A task/to-do, optionally a subtask of another task.

    `parent_task_id` supports AI-assisted breakdown: a vague task gets
    split into small, concrete, time-estimated subtasks (see
    domain.tasks.breakdown_task) to lower activation energy.
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    due_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    estimate_minutes: Mapped[Optional[int]] = mapped_column(nullable=True)
    priority: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # low|medium|high
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|done
    parent_task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)


class Habit(Base):
    """A habit to track, with a deliberately "tiny" version (BJ Fogg)."""

    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    cadence: Mapped[str] = mapped_column(String(16), default="daily")  # daily|weekly|custom
    tiny_version: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # "routine" | "finance"
    scheduled_start: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # "09:00"
    scheduled_end: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # "17:00"
    scheduled_days: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)  # "0,1,2,3,4"
    scheduled_day_of_month: Mapped[Optional[int]] = mapped_column(nullable=True)  # 1-31
    color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    auto_event: Mapped[bool] = mapped_column(default=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class HabitLog(Base):
    """A single completion of a habit.

    `note` (an optional free-text reflection attached to a completion)
    is encrypted at rest -- same reasoning as Note.body: a user could
    write something personal here ("felt anxious today"), and it's
    never SQL-searched anywhere, so encrypting it costs nothing
    functionally.
    """

    __tablename__ = "habit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    habit_id: Mapped[int] = mapped_column(ForeignKey("habits.id"))
    logged_at: Mapped[datetime] = mapped_column(default=now)
    note: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)


class BudgetEntry(Base):
    """One income or expense entry -- either a one-off ("logged $4.50
    on coffee just now") or a recurring monthly one ("$1200 rent due
    on the 1st"), which generates calendar `Event`s the same way a
    "finance"-labelled `Habit` used to (see the migration that moved
    those forward into this table instead).

    `amount_cents` and `category` are deliberately NOT encrypted, even
    though this is sensitive personal data -- both are aggregated
    (SUM/GROUP BY) for the budgeting dashboard, and encrypting them
    would mean doing all of that math in Python instead of SQL (the
    same tradeoff already made for `Note.mood`/`energy`, which power
    `domain/insights.py`'s correlation math the same way). `note` is
    free text that's never aggregated, so it IS encrypted -- same
    reasoning as `HabitLog.note` above.

    Currency is deliberately NOT a column here -- it's one global
    choice for the whole app (`AppSettings.currency`), not tracked per
    entry (see PLAN.md for why: no exchange-rate data source exists in
    this app, and mixed-currency totals would need one).

    `quantity`/`unit_label` (dev note #4, Sprint 6) are purely
    descriptive metadata for display ("3x Coke Zero" instead of three
    indistinguishable flat rows) -- `amount_cents` is ALWAYS the total
    for this entry, never a per-unit price, specifically so
    domain/budget.py::get_summary()'s existing SUM-based totals need
    zero changes to stay correct regardless of quantity. Frontend
    quantity steppers (e.g. RecentChips' repeat-tap flow) are
    responsible for scaling amount_cents themselves before saving, by
    deriving a per-unit baseline from the entry being repeated.
    """

    __tablename__ = "budget_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # "income" | "expense"
    category: Mapped[str] = mapped_column(String(100))
    color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    amount_cents: Mapped[int] = mapped_column()  # always positive, always the TOTAL; kind gives direction
    quantity: Mapped[int] = mapped_column(default=1)
    unit_label: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # e.g. "bottle"
    note: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    is_recurring: Mapped[bool] = mapped_column(default=False)
    recurrence_day_of_month: Mapped[Optional[int]] = mapped_column(nullable=True)  # 1-31
    auto_event: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class NotificationPref(Base):
    """Notification preferences (opt-in, gentle, single row)."""

    __tablename__ = "notification_prefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    morning_time: Mapped[str] = mapped_column(String(5), default="10:00")
    evening_time: Mapped[str] = mapped_column(String(5), default="19:00")
    morning_sent_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    evening_sent_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)


class Memory(Base):
    """A remembered fact/goal/preference about the user.

    Carried forward from the old `ely` project's memory manager, minus
    the vector store -- MVP recall is simple keyword search (see
    domain.memory.recall, which does its content matching in Python
    now rather than a SQL WHERE clause -- see the module docstring
    there). Semantic search can be added later without changing this
    schema.

    `content` is encrypted at rest (see db/encrypted_types.py).
    """

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(EncryptedText)
    type: Mapped[str] = mapped_column(String(16), default="general")  # fact|goal|preference|general
    importance: Mapped[float] = mapped_column(default=0.5)
    created_at: Mapped[datetime] = mapped_column(default=now)
    last_accessed: Mapped[datetime] = mapped_column(default=now)
    access_count: Mapped[int] = mapped_column(default=0)


class DevNote(Base):
    """Internal dev/testing notes — observations while testing the app.

    Standalone table: the LLM / chat tools don't have access to this.
    Just a simple title + body + timestamp for jotting down thoughts
    during development sessions.
    """

    __tablename__ = "dev_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now)


class AppSettings(Base):
    """Single-row app-wide settings (LLM provider choice, onboarding state).

    Mirrors the NotificationPref "single row" pattern above -- there's
    only ever one row, created lazily on first access. Mostly not a
    secrets store: the local API access token still lives in the OS
    keyring/a locked-down file (see domain/auth.py), never here.

    `telegram_bot_token` and `openai_api_key` are the deliberate
    exceptions, added so the Settings UI can configure them without
    hand-editing `.env` (see telegram_bot/process_manager.py for the
    former; domain/llm_client.py for the latter) -- both are encrypted
    at rest exactly like Note.body/Memory.content (see
    db/encrypted_types.py), and domain/settings.py::get_settings()
    never includes either in what it returns (see
    api/routers/telegram.py for the Telegram configured/running status
    instead, which never leaks the raw value -- same rule applies to
    the OpenAI key). Unlike the Telegram token, a new/changed OpenAI key
    takes effect immediately with no restart needed -- see
    domain/llm_client.py::get_llm_client(), which already reads
    settings fresh on every call.
    """

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    setup_completed: Mapped[bool] = mapped_column(default=False)
    llm_provider: Mapped[str] = mapped_column(String(16), default="openai")  # "openai" | "ollama"
    ollama_base_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ollama_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    telegram_bot_token: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    openai_api_key: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")  # ISO 4217 code
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class TelegramLink(Base):
    """Pairing state for the optional Telegram remote-access channel.

    Single-row table (like AppSettings/NotificationPref above) -- Elly
    supports exactly one paired Telegram chat per instance, matching
    the single-user design. `chat_id` is None until pairing completes
    via the in-app 6-digit code flow (never a hardcoded chat ID).
    """

    __tablename__ = "telegram_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    pairing_code: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    pairing_code_expires_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    paired_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class InboundTelegramMessage(Base):
    """Durability/audit log for incoming Telegram messages.

    Every message is persisted here BEFORE being processed through the
    chat tool-calling loop -- if the bot process crashes mid-processing,
    nothing is silently lost. `telegram_update_id` is unique so a
    redelivered update (Telegram's own retry behaviour) is never
    processed twice.

    `text` is encrypted at rest -- it's the raw literal message the
    user sent (the same class of content as ChatMessage.content, if
    not more exposed, since it's the un-processed original).
    """

    __tablename__ = "inbound_telegram_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int]
    telegram_update_id: Mapped[int] = mapped_column(unique=True)
    text: Mapped[str] = mapped_column(EncryptedText)
    received_at: Mapped[datetime] = mapped_column(default=now)
    processed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|processed|error
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ChatMessage(Base):
    """A single message in a chat conversation with the LLM.

    `content` and `tool_arguments` are both encrypted at rest.
    `tool_arguments` matters just as much as `content` -- a diary
    entry's body created via chat ("create a diary entry saying...")
    flows through as a tool call argument, so it would otherwise sit
    in cleartext there even with `content` itself encrypted.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid.uuid4()))
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | tool
    content: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    tool_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tool_arguments: Mapped[Optional[dict]] = mapped_column(EncryptedJSON, nullable=True)
    tool_call_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Reminder(Base):
    """A one-shot reminder/alarm attached to a task, event, or habit.

    `target_type`/`target_id` are a polymorphic reference (no single FK
    makes sense across three different tables) -- domain/reminders.py
    is responsible for keeping this in sync: deleting the target must
    cascade-delete its reminder (see delete_task/delete_event/
    delete_habit), and rescheduling a task's due date or an event's
    start time must recompute `trigger_at` from the stored
    `offset_minutes` rather than leaving it stale.

    Exactly one reminder per target is enforced by domain/reminders.py
    (set_reminder replaces any existing one for that target) -- not by
    a DB constraint, so this table could support more than one per
    target later without a migration, even though v1 only ever creates
    one (see PLAN.md section 0.2, confirmed 2026-07-15).

    Tasks/events are genuinely one-shot: `trigger_at` is computed once,
    and `fired_at` being set means "done forever". Habits are the one
    exception -- a habit recurs daily (or on specific days), so it has
    no single upcoming occurrence to compute a one-time trigger_at
    from. For habit targets, domain/reminders.py recomputes
    `trigger_at` fresh against *today* on every scheduler check, and
    "already fired" means "already fired today" (fired_at.date() ==
    today), not "ever fired" -- see check_and_send_reminders().
    """

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16))  # task | event | habit
    target_id: Mapped[int]
    kind: Mapped[str] = mapped_column(String(16), default="notification")  # notification | alarm
    offset_minutes: Mapped[int] = mapped_column(default=0)  # signed: -15 = 15 min before, 0 = at the time
    trigger_at: Mapped[datetime]
    fired_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
