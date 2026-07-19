"""SQLAlchemy engine/session management.

Single SQLite file, single user, single process. No connection pool
tuning or multi-tenancy needed -- this runs on one Mac for one person.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from elly_server.config import get_database_url


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory = None
_db_initialized = False


def init_db() -> None:
    """Apply pending Alembic migrations to bring the database up to date.

    Falls back to creating all tables directly if no Alembic
    configuration is found (e.g. in a fresh checkout before the
    initial migration was generated).

    Only runs once per process -- subsequent calls are no-ops.
    """
    global _db_initialized
    if _db_initialized:
        return

    from elly_server.db import models  # noqa: F401  (registers models on Base.metadata)

    from_path = Path(__file__).resolve().parents[3] / "alembic.ini"
    if not from_path.exists():
        Base.metadata.create_all(get_engine())
        _db_initialized = True
        return

    import alembic.config
    import alembic.command

    cfg = alembic.config.Config(str(from_path))
    cfg.set_main_option("sqlalchemy.url", get_database_url())
    alembic.command.upgrade(cfg, "head")
    _db_initialized = True


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_database_url(),
            connect_args={"check_same_thread": False},
        )

        # busy_timeout is a per-connection setting that resets for every
        # new connection -- unlike journal_mode below, setting it just
        # once wouldn't help any connection opened later (SQLite's pool
        # can and does open more than one over an app's lifetime). An
        # event listener applies it to every connection as it's created
        # instead. Registered BEFORE the very first connection is ever
        # opened (the WAL-mode setup right below this) -- registering it
        # any later would miss that first connection entirely, since
        # SQLAlchemy's SQLite pool can reuse that same underlying
        # connection for everything that follows rather than opening a
        # fresh one each time (confirmed the hard way: an earlier version
        # of this registered the listener after the WAL-mode connection
        # and every connection, including later ones, silently kept
        # SQLite's own default 5s timeout instead of this one).
        #
        # Why this matters: SQLite allows only one writer at a time --
        # without a busy_timeout, a second writer that shows up while
        # another transaction is still open gets an immediate `database is
        # locked` error instead of waiting. The background scheduler
        # (notifications/reminders, every 60s) hitting this while a chat
        # request was mid-round, waiting on a slow LLM response, was a
        # real, reproduced failure. 30s is generous relative to how long
        # this app's own writes normally take, but short enough that a
        # genuinely stuck writer still fails visibly rather than hanging
        # forever -- domain/chat.py separately fixes the actual root cause
        # (a long-held write lock across a slow LLM call) by committing
        # before each blocking call, so this is defense-in-depth for
        # whatever that doesn't cover, not the primary fix.
        @event.listens_for(_engine, "connect")
        def _set_busy_timeout(dbapi_connection, _connection_record) -> None:
            dbapi_connection.execute("PRAGMA busy_timeout=30000")

        # Enable WAL mode for safe sleep/wake recovery and better
        # concurrent-read performance. journal_mode=WAL is stored in the
        # database file itself (unlike busy_timeout above), so setting it
        # once here, on the very first connection ever opened, is enough
        # -- it applies to every future connection against this file too.
        with _engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=wal")
            conn.commit()
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


@contextmanager
def get_session() -> Iterator[Session]:
    """A session that commits on success, rolls back on error, always closes.

    Every MCP tool wraps its work in one of these -- one request, one
    transaction. Keeps the domain layer (elly_server.domain.*) free of
    any session-lifecycle bookkeeping.

    Lazily initializes the database (tables/migrations) on first call.
    """
    init_db()
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
