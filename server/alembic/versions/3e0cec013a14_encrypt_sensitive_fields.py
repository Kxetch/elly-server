"""encrypt_sensitive_fields

Revision ID: 3e0cec013a14
Revises: 473ce36ea4ce
Create Date: 2026-07-11 15:44:04.096366

Encrypts the most sensitive free-text content at rest: notes.title/
body, memories.content, habit_logs.note, inbound_telegram_messages.text,
chat_messages.content/tool_arguments. See domain/crypto.py for the
"why field-level, not whole-database (SQLCipher)" rationale, and
SECURITY.md for the full, honest picture of what is/isn't covered.

This is a genuine one-time DATA migration, not just a schema change --
any row that already existed with plaintext content needs that content
encrypted in place, or every future read through the ORM (which always
attempts to decrypt these columns via EncryptedText/EncryptedJSON) will
raise ValueError on data that was never actually encrypted. Uses a
direct connection + raw SQL (not the ORM) throughout, specifically to
avoid the new TypeDecorators attempting to decrypt data that's still
plaintext at the time this migration runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from elly_server.domain.crypto import decrypt_text, encrypt_text

# revision identifiers, used by Alembic.
revision: str = '3e0cec013a14'
down_revision: Union[str, Sequence[str], None] = '473ce36ea4ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # notes.title was VARCHAR(200) -- encrypted ciphertext is longer
    # than the plaintext it replaces, so widen it to TEXT. SQLite
    # doesn't enforce VARCHAR(n) length limits in practice, but the
    # column's declared type should still be honest about what it
    # actually holds now.
    with op.batch_alter_table('notes') as batch_op:
        batch_op.alter_column('title', existing_type=sa.String(length=200), type_=sa.Text())

    conn = op.get_bind()

    notes = conn.execute(sa.text("SELECT id, title, body FROM notes")).fetchall()
    for note_id, title, body in notes:
        conn.execute(
            sa.text("UPDATE notes SET title = :title, body = :body WHERE id = :id"),
            {
                "title": encrypt_text(title) if title is not None else None,
                "body": encrypt_text(body) if body is not None else None,
                "id": note_id,
            },
        )

    memories = conn.execute(sa.text("SELECT id, content FROM memories")).fetchall()
    for mem_id, content in memories:
        conn.execute(
            sa.text("UPDATE memories SET content = :content WHERE id = :id"),
            {"content": encrypt_text(content) if content is not None else None, "id": mem_id},
        )

    habit_logs = conn.execute(sa.text("SELECT id, note FROM habit_logs")).fetchall()
    for log_id, note in habit_logs:
        if note is None:
            continue
        conn.execute(
            sa.text("UPDATE habit_logs SET note = :note WHERE id = :id"),
            {"note": encrypt_text(note), "id": log_id},
        )

    inbound = conn.execute(
        sa.text("SELECT id, text FROM inbound_telegram_messages")
    ).fetchall()
    for msg_id, text in inbound:
        conn.execute(
            sa.text("UPDATE inbound_telegram_messages SET text = :text WHERE id = :id"),
            {"text": encrypt_text(text), "id": msg_id},
        )

    # chat_messages.tool_arguments is a JSON column -- SQLAlchemy's JSON
    # type stores it as already-serialized JSON text under the hood, so
    # reading it via raw SQL gives back a valid JSON string directly;
    # no need to json.loads()/json.dumps() round-trip it here, just
    # encrypt the string as-is (EncryptedJSON's process_result_value
    # will json.loads() it after decrypting on the way back out).
    chat_messages = conn.execute(
        sa.text("SELECT id, content, tool_arguments FROM chat_messages")
    ).fetchall()
    for msg_id, content, tool_arguments in chat_messages:
        conn.execute(
            sa.text(
                "UPDATE chat_messages SET content = :content, "
                "tool_arguments = :tool_arguments WHERE id = :id"
            ),
            {
                "content": encrypt_text(content) if content is not None else None,
                "tool_arguments": encrypt_text(tool_arguments) if tool_arguments is not None else None,
                "id": msg_id,
            },
        )


def downgrade() -> None:
    """Downgrade schema -- decrypts everything back to plaintext."""
    conn = op.get_bind()

    notes = conn.execute(sa.text("SELECT id, title, body FROM notes")).fetchall()
    for note_id, title, body in notes:
        conn.execute(
            sa.text("UPDATE notes SET title = :title, body = :body WHERE id = :id"),
            {
                "title": decrypt_text(title) if title is not None else None,
                "body": decrypt_text(body) if body is not None else None,
                "id": note_id,
            },
        )

    memories = conn.execute(sa.text("SELECT id, content FROM memories")).fetchall()
    for mem_id, content in memories:
        conn.execute(
            sa.text("UPDATE memories SET content = :content WHERE id = :id"),
            {"content": decrypt_text(content) if content is not None else None, "id": mem_id},
        )

    habit_logs = conn.execute(sa.text("SELECT id, note FROM habit_logs")).fetchall()
    for log_id, note in habit_logs:
        if note is None:
            continue
        conn.execute(
            sa.text("UPDATE habit_logs SET note = :note WHERE id = :id"),
            {"note": decrypt_text(note), "id": log_id},
        )

    inbound = conn.execute(
        sa.text("SELECT id, text FROM inbound_telegram_messages")
    ).fetchall()
    for msg_id, text in inbound:
        conn.execute(
            sa.text("UPDATE inbound_telegram_messages SET text = :text WHERE id = :id"),
            {"text": decrypt_text(text), "id": msg_id},
        )

    chat_messages = conn.execute(
        sa.text("SELECT id, content, tool_arguments FROM chat_messages")
    ).fetchall()
    for msg_id, content, tool_arguments in chat_messages:
        conn.execute(
            sa.text(
                "UPDATE chat_messages SET content = :content, "
                "tool_arguments = :tool_arguments WHERE id = :id"
            ),
            {
                "content": decrypt_text(content) if content is not None else None,
                "tool_arguments": decrypt_text(tool_arguments) if tool_arguments is not None else None,
                "id": msg_id,
            },
        )

    with op.batch_alter_table('notes') as batch_op:
        batch_op.alter_column('title', existing_type=sa.Text(), type_=sa.String(length=200))
