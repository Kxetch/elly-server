"""Tests for db/base.py's SQLite pragma setup and file permissions.

busy_timeout/secure_delete/foreign_keys are all per-connection settings
that reset for every new connection (unlike journal_mode=WAL, which is
persisted in the database file itself) -- they have to be (re-)applied
via a SQLAlchemy "connect" event listener, not a one-off PRAGMA at
engine-creation time. busy_timeout was added after a real, reproduced
bug (the background scheduler hitting `database is locked` while a chat
request held the write lock across a slow LLM call); secure_delete and
foreign_keys came out of the fable-assessment security polishing pass
(deleted diary content was surviving on disk in freed pages, and
SQLite's FK enforcement defaults to OFF)."""

from __future__ import annotations

import os
import stat

import pytest
from sqlalchemy import text

from elly_server.config import get_db_path
from elly_server.db.base import get_engine, get_session


def _pragma(conn, name: str):
    return conn.exec_driver_sql(f"PRAGMA {name}").scalar()


def test_busy_timeout_is_set_on_a_fresh_connection() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        assert _pragma(conn, "busy_timeout") == 30000


def test_busy_timeout_is_set_on_every_new_connection_not_just_the_first() -> None:
    """The actual thing that would break without the connect-event
    listener -- a *second*, later connection must also have it, not
    just whichever connection happened to be open when the engine was
    first created."""
    engine = get_engine()
    with engine.connect() as conn_a:
        assert _pragma(conn_a, "busy_timeout") == 30000
    with engine.connect() as conn_b:
        assert _pragma(conn_b, "busy_timeout") == 30000


def test_secure_delete_is_enabled_on_every_connection() -> None:
    """Without this, SQLite leaves the bytes of deleted rows physically
    present in freed pages -- "deleting" a diary entry left its
    ciphertext (and for unencrypted fields, plaintext) recoverable from
    the raw file until the page happened to be reused."""
    engine = get_engine()
    with engine.connect() as conn_a:
        assert _pragma(conn_a, "secure_delete") == 1
    with engine.connect() as conn_b:
        assert _pragma(conn_b, "secure_delete") == 1


def test_foreign_keys_enforcement_is_enabled() -> None:
    """SQLite defaults this OFF per-connection. Domain code deletes
    dependent rows explicitly today, so this is a guard against future
    code paths forgetting a cascade, not a fix for a current bug -- but
    it must actually be ON for that guard to exist."""
    engine = get_engine()
    with engine.connect() as conn:
        assert _pragma(conn, "foreign_keys") == 1


def test_journal_mode_is_wal() -> None:
    with get_session() as session:
        mode = session.execute(text("PRAGMA journal_mode")).scalar()
    assert mode == "wal"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits don't map onto Windows ACLs; the chmod is deliberately skipped there")
def test_database_file_is_not_world_readable() -> None:
    """The token/encryption-key files were always 0600, but the database
    they protect was being created with the process umask (typically
    644, world-readable) -- and event/task titles, timestamps, and
    mood/energy values are deliberately plaintext inside it."""
    with get_session() as session:
        session.execute(text("SELECT 1"))  # force the file to exist
    db_path = get_db_path()
    mode = stat.S_IMODE(os.stat(db_path).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_secure_delete_actually_scrubs_deleted_content_from_disk() -> None:
    """The end-to-end proof, not just the pragma value: write a
    recognizable plaintext marker into a row, delete it, checkpoint the
    WAL, and confirm the marker no longer exists anywhere in the raw
    database file bytes. Uses an Event title (deliberately unencrypted
    at rest) so the marker goes into the file as-is -- an encrypted
    field would never contain the literal marker regardless of
    secure_delete, which would make the test pass vacuously."""
    from elly_server.domain import calendar

    marker = "XSECUREDELETEPROOFX7734"
    with get_session() as session:
        created = calendar.create_event(
            session, title=marker, start_at="2030-01-01T10:00:00", end_at="2030-01-01T11:00:00"
        )
        event_id = created["id"]

    db_path = get_db_path()

    with get_session() as session:
        session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    assert marker.encode() in db_path.read_bytes(), "marker should be on disk before deletion (sanity check)"

    with get_session() as session:
        calendar.delete_event(session, event_id=event_id)
    # Checkpoint in its own session, AFTER the delete's transaction has
    # committed -- a wal_checkpoint issued while a write transaction is
    # still open silently does nothing (returns busy), which would leave
    # the pre-delete page image in the main file and fail this test for
    # the wrong reason (caught the hard way: the first version of this
    # test checkpointed inside the delete's own session).
    with get_session() as session:
        session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))

    raw = db_path.read_bytes()
    wal = db_path.with_name(db_path.name + "-wal")
    if wal.exists():
        raw += wal.read_bytes()
    assert marker.encode() not in raw, "deleted content must be scrubbed from the file, not just marked free"
