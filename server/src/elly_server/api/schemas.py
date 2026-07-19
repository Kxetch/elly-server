"""Pydantic request models for the REST API.

Only request bodies get typed models -- response bodies are the plain
JSON-ready dicts that `elly_server.domain.*` already returns (see
`db/serialize.py`), same as the MCP tools. Keeps this layer thin and
avoids maintaining two parallel shape definitions for every response.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# Shared constrained types -- these mirror the constraints already
# enforced on the LLM tool-calling schemas in domain/chat.py. Previously
# the REST layer was looser than the chat layer for the same fields
# (e.g. mood/energy had no range check here), which meant a REST client
# could set values the UI/LLM never would. Keeping both in sync is a
# manual convention for now (see PLAN.md); if a third calling
# convention is added later, consider generating both from one source.
Rating = Optional[int]  # 1-9, see Field(ge=1, le=9) below where used
NoteType = Literal["note", "diary"]
TaskPriority = Literal["low", "medium", "high"]
HabitCadence = Literal["daily", "weekly"]
HabitLabel = Literal["routine", "fitness"]
MemoryType = Literal["fact", "goal", "preference", "general"]
ColorName = Literal[
    "blue", "emerald", "amber", "violet", "rose", "cyan",
    "lime", "pink", "indigo", "teal", "orange", "sky",
    "red", "yellow", "green", "purple", "fuchsia", "slate",
]
MAX_BODY_LENGTH = 50_000  # generous for a long diary/journal entry
MAX_TITLE_LENGTH = 200


class NoteCreate(BaseModel):
    body: str = Field(max_length=MAX_BODY_LENGTH)
    type: NoteType = "note"
    title: Optional[str] = Field(default=None, max_length=MAX_TITLE_LENGTH)
    mood: Optional[int] = Field(default=None, ge=1, le=9)
    energy: Optional[int] = Field(default=None, ge=1, le=9)
    tags: Optional[list[str]] = None


class NoteUpdate(BaseModel):
    body: Optional[str] = Field(default=None, max_length=MAX_BODY_LENGTH)
    title: Optional[str] = Field(default=None, max_length=MAX_TITLE_LENGTH)
    mood: Optional[int] = Field(default=None, ge=1, le=9)
    energy: Optional[int] = Field(default=None, ge=1, le=9)
    tags: Optional[list[str]] = None


class EventCreate(BaseModel):
    title: str = Field(max_length=MAX_TITLE_LENGTH)
    start_at: str
    end_at: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=MAX_BODY_LENGTH)
    color: Optional[ColorName] = None


class EventReschedule(BaseModel):
    start_at: str
    end_at: Optional[str] = None


class TaskCreate(BaseModel):
    title: str = Field(max_length=MAX_TITLE_LENGTH)
    due_at: Optional[str] = None
    estimate_minutes: Optional[int] = Field(default=None, ge=0, le=100_000)
    priority: Optional[TaskPriority] = None
    parent_task_id: Optional[int] = None


class TaskBreakdown(BaseModel):
    subtasks: list[dict[str, Any]]


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=MAX_TITLE_LENGTH)
    due_at: Optional[str] = None
    estimate_minutes: Optional[int] = Field(default=None, ge=0, le=100_000)
    priority: Optional[TaskPriority] = None


class HabitUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=MAX_TITLE_LENGTH)
    tiny_version: Optional[str] = Field(default=None, max_length=MAX_BODY_LENGTH)
    cadence: Optional[HabitCadence] = None
    label: Optional[HabitLabel] = None
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    scheduled_days: Optional[str] = None
    auto_event: Optional[bool] = None
    color: Optional[ColorName] = None
    is_active: Optional[bool] = None


class HabitCreate(BaseModel):
    name: str = Field(max_length=MAX_TITLE_LENGTH)
    cadence: HabitCadence = "daily"
    tiny_version: Optional[str] = Field(default=None, max_length=MAX_BODY_LENGTH)
    label: Optional[HabitLabel] = None
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    scheduled_days: Optional[str] = None
    auto_event: bool = True
    color: Optional[ColorName] = None


class HabitLogRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=MAX_BODY_LENGTH)


class MemoryCreate(BaseModel):
    content: str = Field(max_length=MAX_BODY_LENGTH)
    type: MemoryType = "general"
    importance: Optional[float] = Field(default=None, ge=0, le=1)


class NotificationPrefUpdate(BaseModel):
    enabled: Optional[bool] = None
    morning_time: Optional[str] = None
    evening_time: Optional[str] = None


class DuplicateTasksRequest(BaseModel):
    task_ids: Optional[list[int]] = None


class DevNoteCreate(BaseModel):
    body: str = Field(max_length=MAX_BODY_LENGTH)
    title: Optional[str] = Field(default=None, max_length=MAX_TITLE_LENGTH)


class VerifyTokenRequest(BaseModel):
    token: str = Field(max_length=256)  # real tokens are 64 hex chars; generous cap only


class SettingsUpdate(BaseModel):
    llm_provider: Optional[Literal["openai", "ollama"]] = None
    ollama_base_url: Optional[str] = Field(default=None, max_length=255)
    ollama_model: Optional[str] = Field(default=None, max_length=100)
    setup_completed: Optional[bool] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)


class TelegramTokenUpdate(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class OpenAiKeyUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=200)


class OllamaTestConnectionRequest(BaseModel):
    base_url: Optional[str] = Field(default=None, max_length=255)


class OllamaPullModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    base_url: Optional[str] = Field(default=None, max_length=255)


# ---- Budget (income/expense tracking) --------------------------------

BudgetKind = Literal["income", "expense"]


class BudgetEntryCreate(BaseModel):
    kind: BudgetKind
    category: str = Field(min_length=1, max_length=100)
    amount: float = Field(gt=0, le=100_000_000)
    note: Optional[str] = Field(default=None, max_length=MAX_BODY_LENGTH)
    color: Optional[ColorName] = None
    is_recurring: bool = False
    recurrence_day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    auto_event: bool = True
    quantity: int = Field(default=1, ge=1, le=10_000)
    unit_label: Optional[str] = Field(default=None, max_length=32)


class BudgetEntryUpdate(BaseModel):
    category: Optional[str] = Field(default=None, min_length=1, max_length=100)
    amount: Optional[float] = Field(default=None, gt=0, le=100_000_000)
    note: Optional[str] = Field(default=None, max_length=MAX_BODY_LENGTH)
    color: Optional[ColorName] = None
    quantity: Optional[int] = Field(default=None, ge=1, le=10_000)
    unit_label: Optional[str] = Field(default=None, max_length=32)


# ---- Reminders & alarms -------------------------------------------------

ReminderTargetType = Literal["task", "event", "habit"]
ReminderKind = Literal["notification", "alarm"]


class ReminderSet(BaseModel):
    kind: ReminderKind = "notification"
    offset_minutes: int = Field(ge=-10_080, le=10_080)  # +/- 1 week, a generous bound
    message: Optional[str] = Field(default=None, max_length=500)
