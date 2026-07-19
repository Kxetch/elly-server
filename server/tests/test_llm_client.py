"""Tests for domain/llm_client.py -- provider selection (OpenAI vs Ollama)."""

from __future__ import annotations

import openai
import pytest
from sqlalchemy import delete

from elly_server.db.base import get_session
from elly_server.db.models import AppSettings
from elly_server.domain import settings as settings_domain
from elly_server.domain.llm_client import LlmNotConfiguredError, describe_llm_error, get_llm_client


@pytest.fixture(autouse=True)
def _clean_settings_row() -> None:
    with get_session() as session:
        session.execute(delete(AppSettings))


def test_openai_provider_without_key_raises_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with get_session() as session:
        with pytest.raises(LlmNotConfiguredError) as exc_info:
            get_llm_client(session)
    assert "Ollama" in str(exc_info.value)  # mentions the local alternative


def test_openai_provider_with_key_returns_configured_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    with get_session() as session:
        client, model, provider = get_llm_client(session)
    assert model == "gpt-4o-mini"
    assert provider == "openai"
    assert client.api_key == "sk-test-fake-key"


def test_llm_client_has_a_bounded_timeout_not_the_sdk_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unbounded (SDK default: 10 minute) timeout means one hung
    request can tie up a FastAPI sync-route worker thread indefinitely
    -- enough of those and the app stops responding to any request, not
    just chat ones. Applies to both providers."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    with get_session() as session:
        client, _model, _provider = get_llm_client(session)
    assert client.timeout == 120.0

    with get_session() as session:
        settings_domain.update_settings(session, llm_provider="ollama")
    with get_session() as session:
        client, _model, _provider = get_llm_client(session)
    assert client.timeout == 120.0


def test_openai_provider_prefers_key_set_in_settings_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key set via the Settings UI takes effect immediately -- no
    restart needed, since this reads settings fresh on every call."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-fallback-key")
    with get_session() as session:
        settings_domain.set_openai_api_key(session, "sk-from-settings-ui")
    with get_session() as session:
        client, _model, _provider = get_llm_client(session)
    assert client.api_key == "sk-from-settings-ui"


def test_ollama_provider_uses_local_base_url_and_model() -> None:
    with get_session() as session:
        settings_domain.update_settings(session, llm_provider="ollama", ollama_model="llama3.1")
    with get_session() as session:
        client, model, provider = get_llm_client(session)
    assert model == "llama3.1"
    assert provider == "ollama"
    assert str(client.base_url).rstrip("/") == "http://localhost:11434/v1"


def test_ollama_provider_never_requires_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with get_session() as session:
        settings_domain.update_settings(session, llm_provider="ollama")
        client, _model, _provider = get_llm_client(session)  # should not raise
    assert client is not None


def test_ollama_custom_base_url_override() -> None:
    with get_session() as session:
        settings_domain.update_settings(
            session, llm_provider="ollama", ollama_base_url="http://192.168.1.50:11434/v1"
        )
    with get_session() as session:
        client, _model, _provider = get_llm_client(session)
    assert str(client.base_url).rstrip("/") == "http://192.168.1.50:11434/v1"


def _fake_request():
    import httpx
    return httpx.Request("POST", "http://example.test/v1/chat/completions")


def _fake_response(status: int):
    import httpx
    return httpx.Response(status, request=_fake_request())


class TestDescribeLlmError:
    """Real failures (Ollama not running, bad API key, etc.) used to
    surface as raw OpenAI-SDK exception text -- confusing for someone
    who just forgot to start Ollama. describe_llm_error() translates
    the SDK's exception hierarchy into plain, actionable language."""

    def test_connection_error_mentions_ollama_when_that_is_the_provider(self) -> None:
        err = openai.APIConnectionError(request=_fake_request())
        msg = describe_llm_error(err, "ollama", "llama3.1")
        assert "Ollama" in msg
        assert "running" in msg

    def test_connection_error_mentions_openai_and_internet_for_that_provider(self) -> None:
        err = openai.APIConnectionError(request=_fake_request())
        msg = describe_llm_error(err, "openai", "gpt-4o-mini")
        assert "OpenAI" in msg
        assert "internet" in msg

    def test_authentication_error_points_at_the_env_var_for_openai(self) -> None:
        err = openai.AuthenticationError("bad key", response=_fake_response(401), body=None)
        msg = describe_llm_error(err, "openai", "gpt-4o-mini")
        assert "OPENAI_API_KEY" in msg

    def test_not_found_error_names_the_missing_model(self) -> None:
        err = openai.NotFoundError("no such model", response=_fake_response(404), body=None)
        msg = describe_llm_error(err, "ollama", "totally-made-up-model")
        assert "totally-made-up-model" in msg

    def test_rate_limit_error_is_distinguishable_from_a_connection_error(self) -> None:
        err = openai.RateLimitError("slow down", response=_fake_response(429), body=None)
        msg = describe_llm_error(err, "openai", "gpt-4o-mini")
        assert "rate-limiting" in msg

    def test_rate_limit_with_insufficient_quota_code_gets_a_billing_specific_message(self) -> None:
        """OpenAI's SDK raises the identical RateLimitError (HTTP 429) for
        both transient throttling and quota/billing exhaustion -- found via
        a real beta tester whose fresh, unfunded OpenAI account hit this on
        every single request while an established account with billing set
        up didn't. "Wait a moment and try again" is actively wrong advice
        for a billing problem; this must be distinguished via the error
        body's `code` field instead."""
        err = openai.RateLimitError(
            "You exceeded your current quota",
            response=_fake_response(429),
            body={"message": "You exceeded your current quota", "type": "insufficient_quota", "code": "insufficient_quota"},
        )
        msg = describe_llm_error(err, "openai", "gpt-4o-mini")
        assert "quota" in msg
        assert "billing" in msg.lower()
        assert "isn't something wrong with Elly" in msg
        assert "wait a moment" not in msg.lower()

    def test_rate_limit_with_a_different_code_still_gets_the_generic_transient_message(self) -> None:
        """A genuine transient rate limit (not a quota/billing issue)
        should NOT get the billing-specific message -- only the specific
        `insufficient_quota` code should trigger it."""
        err = openai.RateLimitError(
            "Rate limit reached",
            response=_fake_response(429),
            body={"message": "Rate limit reached", "type": "requests", "code": "rate_limit_exceeded"},
        )
        msg = describe_llm_error(err, "openai", "gpt-4o-mini")
        assert "rate-limiting" in msg
        assert "billing" not in msg.lower()

    def test_rate_limit_with_insufficient_quota_code_on_ollama_still_gets_generic_message(self) -> None:
        """The quota/billing message is OpenAI-account-specific language
        (platform.openai.com billing) -- must never show up for the local
        Ollama provider even if a body happened to carry that code."""
        err = openai.RateLimitError(
            "slow down", response=_fake_response(429), body={"code": "insufficient_quota"}
        )
        msg = describe_llm_error(err, "ollama", "llama3.1")
        assert "Ollama" in msg
        assert "billing" not in msg.lower()

    def test_unknown_exception_falls_back_gracefully_without_raising(self) -> None:
        msg = describe_llm_error(ValueError("something odd"), "openai", "gpt-4o-mini")
        assert "something odd" in msg
