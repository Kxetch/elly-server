"""Tests the actual Alembic migration (e36c1349e461) that adds
quantity/unit_label to budget_entries -- specifically that it correctly
backfills quantity=1 for rows that already existed before this column
was added (a NOT NULL column added to a non-empty table needs a
server-side default, not just the ORM-level one -- see the migration's
own docstring). Same scratch-DB pattern as test_budget_migration.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import alembic.command
import alembic.config
import pytest

_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_BEFORE_REVISION = "4db7156fbeca"  # add_reminders_table -- right before this one


def _upgrade(db_path: Path, revision: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELLY_DB_PATH", str(db_path))
    cfg = alembic.config.Config(str(_ALEMBIC_INI))
    alembic.command.upgrade(cfg, revision)


@pytest.fixture
def pre_migration_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "quantity-migration-test.db"
    _upgrade(db_path, _BEFORE_REVISION, monkeypatch)
    return db_path


def test_existing_entry_gets_backfilled_with_quantity_one(
    pre_migration_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = sqlite3.connect(pre_migration_db)
    conn.execute(
        "INSERT INTO budget_entries (id, kind, category, amount_cents, is_recurring, "
        "auto_event, created_at) VALUES (1, 'expense', 'Coffee', 450, 0, 1, '2026-07-01 09:00:00')"
    )
    conn.commit()
    conn.close()

    _upgrade(pre_migration_db, "head", monkeypatch)

    conn = sqlite3.connect(pre_migration_db)
    quantity, unit_label = conn.execute(
        "SELECT quantity, unit_label FROM budget_entries WHERE id = 1"
    ).fetchone()
    conn.close()

    assert quantity == 1
    assert unit_label is None


def test_migration_is_safe_with_no_existing_entries(pre_migration_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _upgrade(pre_migration_db, "head", monkeypatch)

    conn = sqlite3.connect(pre_migration_db)
    count = conn.execute("SELECT COUNT(*) FROM budget_entries").fetchone()[0]
    conn.close()

    assert count == 0


def test_new_columns_accept_real_values_after_migration(pre_migration_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _upgrade(pre_migration_db, "head", monkeypatch)

    conn = sqlite3.connect(pre_migration_db)
    conn.execute(
        "INSERT INTO budget_entries (id, kind, category, amount_cents, quantity, unit_label, "
        "is_recurring, auto_event, created_at) VALUES "
        "(1, 'expense', 'Coke Zero', 450, 3, 'bottle', 0, 1, '2026-07-01 09:00:00')"
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(pre_migration_db)
    quantity, unit_label = conn.execute(
        "SELECT quantity, unit_label FROM budget_entries WHERE id = 1"
    ).fetchone()
    conn.close()

    assert quantity == 3
    assert unit_label == "bottle"
