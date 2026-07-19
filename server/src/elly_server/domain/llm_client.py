"""LLM client factory: one place that decides which provider/model the
in-app chat talks to, based on the user's Settings choice.

Two providers today:
- "openai" (default): direct OpenAI API, requires OPENAI_API_KEY.
- "ollama": a local Ollama server's OpenAI-compatible endpoint
  (http://localhost:11434/v1 by default) -- zero data leaves the
  machine. Requires a tool/function-calling-capable model (Llama 3.1+,
  Qwen2.5, Mistral, etc.) to be pulled and running locally.

Both are used through the same `openai` SDK client class -- Ollama's
OpenAI-compatible endpoint means no separate SDK/dependency is needed,
just a different `base_url` and a placeholder API key (Ollama ignores
it, but the SDK requires a non-empty string).

Kept separate from `domain/chat.py` so the same factory can be reused
by the future Telegram bot (Sprint 2) without duplicating provider
selection logic.
"""

from __future__ import annotations

from typing import Union

import openai
from openai import AsyncOpenAI, OpenAI
from sqlalchemy.orm import Session

from elly_server.config import get_ollama_base_url, get_ollama_model, get_openai_model
from elly_server.domain import settings as settings_domain


class LlmNotConfiguredError(Exception):
    """Raised when the currently-selected provider isn't usable yet
    (e.g. cloud provider chosen but no API key set). Callers should
    catch this and show the message directly to the user -- it's
    already written in plain, friendly language."""


# A per-request ceiling on how long a single LLM call is allowed to take,
# for both providers. The OpenAI SDK's own default (10 minutes) is really a
# "don't wait forever" fallback, not a real bound -- a single hung request
# left unbounded can tie up one of FastAPI's limited sync-route worker
# threads indefinitely; enough of those and the whole app stops responding
# to *any* request, not just chat ones. domain/chat.py separately fixes the
# related-but-distinct problem of a slow call holding a SQLite write lock
# (by committing before every blocking call, not by bounding the call
# itself), so this timeout is about worker-thread/resource exhaustion, not
# database locking. 120s is generous relative to real measurements: a
# narrowed-tool-set Ollama request on modest hardware completed in 2-15s in
# testing; a genuinely stuck request (reproduced once on RAM-starved
# hardware) never completed even after 10+ minutes, so 120s fails that
# case in reasonable time without being so tight it might cut off a real,
# if slow, local-model response.
_LLM_REQUEST_TIMEOUT_SECONDS = 120.0


def get_llm_client(
    session: Session, *, async_mode: bool = False
) -> tuple[Union[OpenAI, AsyncOpenAI], str, str]:
    """Return (client, model_name, provider) for whichever provider is
    configured.

    Raises LlmNotConfiguredError with a user-facing message if the
    configured provider can't be used right now.
    """
    prefs = settings_domain.get_settings(session)
    provider = prefs.get("llm_provider") or "openai"
    cls = AsyncOpenAI if async_mode else OpenAI

    if provider == "ollama":
        base_url = prefs.get("ollama_base_url") or get_ollama_base_url()
        model = prefs.get("ollama_model") or get_ollama_model()
        # Ollama's OpenAI-compatible endpoint doesn't check the API key,
        # but the SDK requires a non-empty string.
        client = cls(api_key="ollama", base_url=base_url, timeout=_LLM_REQUEST_TIMEOUT_SECONDS)
        return client, model, "ollama"

    # Default: direct OpenAI. get_effective_openai_api_key() prefers a
    # key set in Settings, falling back to OPENAI_API_KEY in .env for
    # installs still configured the old way.
    api_key = settings_domain.get_effective_openai_api_key(session)
    if not api_key:
        raise LlmNotConfiguredError(
            "No LLM provider is configured yet. Set an OpenAI API key in "
            "Settings (or OPENAI_API_KEY in server/.env), or switch to a "
            "local Ollama model in Settings for a fully local/private setup."
        )
    model = get_openai_model()
    client = cls(api_key=api_key, timeout=_LLM_REQUEST_TIMEOUT_SECONDS)
    return client, model, "openai"


def describe_llm_error(e: Exception, provider: str, model: str) -> str:
    """Translate a raw OpenAI-SDK exception into plain, actionable
    language -- previously any failure past the "no API key" case fell
    straight through to `str(e)`, which meant a user who forgot to
    start Ollama saw an OpenAI-SDK-flavored connection error instead of
    "Ollama doesn't seem to be running." The SDK's own exception
    hierarchy (openai.APIConnectionError etc.) is the same for both
    providers since Ollama is talked to through the same OpenAI-
    compatible client class, so this one mapping covers both.
    """
    where = "Ollama" if provider == "ollama" else "OpenAI"

    if isinstance(e, openai.APIConnectionError):
        if provider == "ollama":
            return (
                "Couldn't reach Ollama. Check it's running (`ollama serve`) "
                "and that the model is pulled -- see Settings for the URL "
                "Elly is trying to use."
            )
        return "Couldn't reach OpenAI. Check your internet connection and try again."

    if isinstance(e, openai.AuthenticationError):
        if provider == "ollama":
            return "Ollama rejected the request. Check it's running and reachable."
        return "OpenAI rejected the API key. Double-check it in Settings (or OPENAI_API_KEY in server/.env)."

    if isinstance(e, openai.NotFoundError):
        return f"{where} couldn't find the model \"{model}\". Check the model name in Settings."

    if isinstance(e, openai.RateLimitError):
        # OpenAI's SDK raises the exact same RateLimitError (HTTP 429) for
        # genuine transient throttling AND for quota/billing exhaustion
        # (no payment method on file, expired free trial credits, a $0
        # usage cap) -- two very different problems with very different
        # fixes. The response body's `code` field distinguishes them
        # (`"insufficient_quota"` for the billing case); "wait a moment
        # and try again" is actively misleading for that one, since
        # waiting does nothing -- found via a real beta tester whose
        # fresh OpenAI account (no billing configured yet) hit this on
        # every request while an established, funded account didn't.
        if provider == "openai" and getattr(e, "code", None) == "insufficient_quota":
            return (
                "This OpenAI account has no usage quota available -- no "
                "payment method on file, expired free trial credits, or a "
                "$0 spending limit set on the account this key belongs to. "
                "This isn't something wrong with Elly or the key itself; "
                "check platform.openai.com's billing/usage-limits page for "
                "that account, or switch to a fully local model via Ollama "
                "in Settings instead."
            )
        return f"{where} is rate-limiting requests right now. Wait a moment and try again."

    if isinstance(e, openai.APITimeoutError):
        return f"The request to {where} timed out. Try again."

    if isinstance(e, openai.APIError):
        return f"{where} returned an error: {e}"

    return f"Something went wrong talking to {where}: {e}"
