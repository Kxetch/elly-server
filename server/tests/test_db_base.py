"""Tests for db/base.py's SQLite pragma setup.

busy_timeout specifically: unlike journal_mode=WAL (persisted in the
database file itself, so setting it once on the first-ever connection
is enough), busy_timeout is a per-connection setting that resets for
every new connection -- it has to be (re-)applied via a SQLAlchemy
"connect" event listener, not a one-off PRAGMA at engine-creation time.
Added after a real, reproduced bug: without it, a second writer (the
background notification scheduler) showing up while a chat request held
the write lock got an immediate `database is locked` error instead of
waiting."""

from __future__ import annotations

from sqlalchemy import text

from elly_server.db.base import get_engine, get_session


def _busy_timeout_of(conn) -> int:
    return conn.exec_driver_sql("PRAGMA busy_timeout").scalar()


def test_busy_timeout_is_set_on_a_fresh_connection() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        assert _busy_timeout_of(conn) == 30000


def test_busy_timeout_is_set_on_every_new_connection_not_just_the_first() -> None:
    """The actual thing that would break without the connect-event
    listener -- a *second*, later connection must also have it, not
    just whichever connection happened to be open when the engine was
    first created."""
    engine = get_engine()
    with engine.connect() as conn_a:
        assert _busy_timeout_of(conn_a) == 30000
    with engine.connect() as conn_b:
        assert _busy_timeout_of(conn_b) == 30000


def test_journal_mode_is_wal() -> None:
    with get_session() as session:
        mode = session.execute(text("PRAGMA journal_mode")).scalar()
    assert mode == "wal"
