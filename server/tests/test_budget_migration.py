"""Tests the actual Alembic data migration (f3efbc673773) that moves
existing "finance"-labelled habits forward into BudgetEntry rows.

Deliberately does NOT use the shared test DB from conftest.py (that
one is always upgraded straight to `head` via db/base.py::init_db(),
so there's no way to pause it mid-migration to seed pre-migration
data) -- instead builds its own throwaway SQLite file, upgrading in
two steps: schema-only first (so raw SQL can seed a "finance" habit
the old-fashioned way), then the rest of the way to `head` to trigger
the data migration.

Note: alembic/env.py always resolves its DB URL via
elly_server.config.get_database_url() (so a real `uv run alembic ...`
invocation stays consistent with ELLY_DATA_DIR/ELLY_DB_PATH), which
means pointing this at a scratch file requires overriding
ELLY_DB_PATH via monkeypatch rather than passing a URL directly to
Alembic's Config -- the latter gets silently overwritten by env.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import alembic.command
import alembic.config
import pytest

from elly_server.domain.crypto import decrypt_text

_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_SCHEMA_REVISION = "f99c14eb9d34"  # add_budget_entries -- schema only, right before the data migration


def _upgrade(db_path: Path, revision: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELLY_DB_PATH", str(db_path))
    cfg = alembic.config.Config(str(_ALEMBIC_INI))
    alembic.command.upgrade(cfg, revision)


@pytest.fixture
def migration_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "migration-test.db"
    _upgrade(db_path, _SCHEMA_REVISION, monkeypatch)
    return db_path


def _run_data_migration(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _upgrade(db_path, "head", monkeypatch)


def test_finance_habit_becomes_a_budget_entry(migration_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(migration_db)
    conn.execute(
        "INSERT INTO habits (id, name, cadence, label, scheduled_day_of_month, auto_event, "
        "is_active, created_at) VALUES (1, 'Rent', 'daily', 'finance', 1, 1, 1, '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    _run_data_migration(migration_db, monkeypatch)

    conn = sqlite3.connect(migration_db)
    rows = conn.execute(
        "SELECT kind, category, amount_cents, is_recurring, recurrence_day_of_month, auto_event, note "
        "FROM budget_entries"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    kind, category, amount_cents, is_recurring, recurrence_day_of_month, auto_event, note = rows[0]
    assert kind == "expense"
    assert category == "Rent"
    assert amount_cents == 0
    assert is_recurring == 1
    assert recurrence_day_of_month == 1
    assert auto_event == 1
    assert decrypt_text(note) == "Migrated from a habit -- set a real amount."


def test_migration_removes_the_old_habit_and_its_events(migration_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(migration_db)
    conn.execute(
        "INSERT INTO habits (id, name, cadence, label, scheduled_day_of_month, auto_event, "
        "is_active, created_at) VALUES (1, 'Rent', 'daily', 'finance', 1, 1, 1, '2026-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO events (id, title, start_at, habit_id, created_at) "
        "VALUES (1, 'Rent', '2026-08-01 09:00:00', 1, '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    _run_data_migration(migration_db, monkeypatch)

    conn = sqlite3.connect(migration_db)
    habit_count = conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0]
    event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    assert habit_count == 0
    assert event_count == 0


def test_migration_leaves_non_finance_habits_alone(migration_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(migration_db)
    conn.execute(
        "INSERT INTO habits (id, name, cadence, label, auto_event, is_active, created_at) "
        "VALUES (1, 'Drink water', 'daily', 'routine', 1, 1, '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    _run_data_migration(migration_db, monkeypatch)

    conn = sqlite3.connect(migration_db)
    habit_count = conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0]
    budget_entry_count = conn.execute("SELECT COUNT(*) FROM budget_entries").fetchone()[0]
    conn.close()

    assert habit_count == 1
    assert budget_entry_count == 0


def test_migration_is_a_no_op_with_no_finance_habits(migration_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _run_data_migration(migration_db, monkeypatch)

    conn = sqlite3.connect(migration_db)
    budget_entry_count = conn.execute("SELECT COUNT(*) FROM budget_entries").fetchone()[0]
    conn.close()

    assert budget_entry_count == 0


def test_migration_handles_multiple_finance_habits(migration_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(migration_db)
    conn.execute(
        "INSERT INTO habits (id, name, cadence, label, scheduled_day_of_month, auto_event, "
        "is_active, created_at) VALUES (1, 'Rent', 'daily', 'finance', 1, 1, 1, '2026-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO habits (id, name, cadence, label, scheduled_day_of_month, auto_event, "
        "is_active, created_at) VALUES (2, 'Salary', 'daily', 'finance', 25, 1, 1, '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    _run_data_migration(migration_db, monkeypatch)

    conn = sqlite3.connect(migration_db)
    categories = sorted(row[0] for row in conn.execute("SELECT category FROM budget_entries").fetchall())
    conn.close()

    assert categories == ["Rent", "Salary"]
