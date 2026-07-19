"""Tests for domain/settings.py -- LLM provider choice + onboarding state."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import AppSettings
from elly_server.domain import settings as settings_domain
from elly_server.domain.crypto import decrypt_text


@pytest.fixture(autouse=True)
def _clean_settings_row() -> None:
    with get_session() as session:
        session.execute(delete(AppSettings))


def test_get_settings_creates_default_row() -> None:
    with get_session() as session:
        result = settings_domain.get_settings(session)
    assert result["llm_provider"] == "openai"
    assert result["setup_completed"] is False
    assert result["ollama_base_url"] is None


def test_update_settings_switches_provider() -> None:
    with get_session() as session:
        settings_domain.update_settings(
            session,
            llm_provider="ollama",
            ollama_base_url="http://localhost:11434/v1",
            ollama_model="llama3.1",
            setup_completed=True,
        )
    with get_session() as session:
        result = settings_domain.get_settings(session)
    assert result["llm_provider"] == "ollama"
    assert result["ollama_base_url"] == "http://localhost:11434/v1"
    assert result["ollama_model"] == "llama3.1"
    assert result["setup_completed"] is True


def test_update_settings_rejects_invalid_provider() -> None:
    with pytest.raises(ValueError):
        with get_session() as session:
            settings_domain.update_settings(session, llm_provider="not-a-real-provider")


def test_update_settings_partial_update_preserves_other_fields() -> None:
    with get_session() as session:
        settings_domain.update_settings(session, llm_provider="ollama", ollama_model="llama3.1")
    with get_session() as session:
        settings_domain.update_settings(session, setup_completed=True)
    with get_session() as session:
        result = settings_domain.get_settings(session)
    assert result["llm_provider"] == "ollama"
    assert result["ollama_model"] == "llama3.1"
    assert result["setup_completed"] is True


# ---- Telegram bot token (the one deliberate secret in this table) ----------


def test_get_settings_never_includes_telegram_bot_token() -> None:
    with get_session() as session:
        settings_domain.set_telegram_bot_token(session, "super-secret-token")
    with get_session() as session:
        result = settings_domain.get_settings(session)
    assert "telegram_bot_token" not in result


def test_set_and_get_effective_telegram_bot_token() -> None:
    with get_session() as session:
        settings_domain.set_telegram_bot_token(session, "abc123:real-token")
    with get_session() as session:
        assert settings_domain.get_effective_telegram_bot_token(session) == "abc123:real-token"


def test_clear_telegram_bot_token() -> None:
    with get_session() as session:
        settings_domain.set_telegram_bot_token(session, "abc123:real-token")
    with get_session() as session:
        settings_domain.set_telegram_bot_token(session, None)
    with get_session() as session:
        row = session.get(AppSettings, 1)
        assert row is not None
        assert row.telegram_bot_token is None


def test_telegram_bot_token_encrypted_at_rest() -> None:
    """The raw column value in the DB should never be the plaintext
    token -- same guarantee as Note.body/Memory.content etc (see
    db/encrypted_types.py)."""
    with get_session() as session:
        settings_domain.set_telegram_bot_token(session, "plaintext-token-value")

    from sqlalchemy import text

    with get_session() as session:
        raw = session.execute(
            text("SELECT telegram_bot_token FROM app_settings LIMIT 1")
        ).scalar_one()
    assert raw != "plaintext-token-value"
    assert decrypt_text(raw) == "plaintext-token-value"


def test_get_effective_telegram_bot_token_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB value takes precedence when set; falls back to the
    ELLY_TELEGRAM_BOT_TOKEN env var (legacy .env-based installs) when
    the DB has none."""
    monkeypatch.setenv("ELLY_TELEGRAM_BOT_TOKEN", "env-token-value")
    with get_session() as session:
        assert settings_domain.get_effective_telegram_bot_token(session) == "env-token-value"

    with get_session() as session:
        settings_domain.set_telegram_bot_token(session, "db-token-value")
    with get_session() as session:
        # DB value wins once set, even though the env var is still there.
        assert settings_domain.get_effective_telegram_bot_token(session) == "db-token-value"


# ---- OpenAI API key (same secret-storage pattern as the Telegram token) ----


def test_get_settings_never_includes_openai_api_key() -> None:
    with get_session() as session:
        settings_domain.set_openai_api_key(session, "sk-super-secret-key")
    with get_session() as session:
        result = settings_domain.get_settings(session)
    assert "openai_api_key" not in result


def test_get_settings_exposes_openai_key_configured_as_a_boolean() -> None:
    """The raw key is never in the response, but the frontend still
    needs to know *whether* one is set (to show "saved" vs. the input
    form) -- mirrors /api/telegram/status's bot_configured field."""
    with get_session() as session:
        assert settings_domain.get_settings(session)["openai_key_configured"] is False

    with get_session() as session:
        settings_domain.set_openai_api_key(session, "sk-super-secret-key")
    with get_session() as session:
        assert settings_domain.get_settings(session)["openai_key_configured"] is True


def test_set_and_get_effective_openai_api_key() -> None:
    with get_session() as session:
        settings_domain.set_openai_api_key(session, "sk-real-key-value")
    with get_session() as session:
        assert settings_domain.get_effective_openai_api_key(session) == "sk-real-key-value"


def test_clear_openai_api_key() -> None:
    with get_session() as session:
        settings_domain.set_openai_api_key(session, "sk-real-key-value")
    with get_session() as session:
        settings_domain.set_openai_api_key(session, None)
    with get_session() as session:
        row = session.get(AppSettings, 1)
        assert row is not None
        assert row.openai_api_key is None


def test_openai_api_key_encrypted_at_rest() -> None:
    """Same guarantee as the Telegram bot token -- the raw column value
    in the DB should never be the plaintext key."""
    with get_session() as session:
        settings_domain.set_openai_api_key(session, "sk-plaintext-key-value")

    from sqlalchemy import text

    with get_session() as session:
        raw = session.execute(
            text("SELECT openai_api_key FROM app_settings LIMIT 1")
        ).scalar_one()
    assert raw != "sk-plaintext-key-value"
    assert decrypt_text(raw) == "sk-plaintext-key-value"


def test_get_effective_openai_api_key_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB value takes precedence when set; falls back to the
    OPENAI_API_KEY env var (legacy .env-based installs) when the DB has
    none."""
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-value")
    with get_session() as session:
        assert settings_domain.get_effective_openai_api_key(session) == "env-key-value"

    with get_session() as session:
        settings_domain.set_openai_api_key(session, "db-key-value")
    with get_session() as session:
        # DB value wins once set, even though the env var is still there.
        assert settings_domain.get_effective_openai_api_key(session) == "db-key-value"
