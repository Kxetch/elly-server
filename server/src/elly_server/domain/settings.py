"""App-wide settings: LLM provider choice + first-run onboarding state.

Single-row table (see AppSettings), same pattern as NotificationPref.
The local API access token lives in the OS keyring/a locked-down file
(see domain/auth.py), never here. Most of this table holds user-facing
preferences that make sense to change via the Settings UI without
restarting the server (LLM provider, Ollama URL/model, and -- since
this module gained set_openai_api_key()/get_effective_openai_api_key()
-- the OpenAI API key too, all take effect immediately: see
domain/llm_client.py::get_llm_client(), which already reads settings
fresh from the DB on every call rather than caching a client).

The one deliberate exception is `telegram_bot_token`: unlike everything
else above, changing it DOES require a restart (see
api/routers/system.py's self-restart endpoint) to actually
start/replace the managed subprocess (see
telegram_bot/process_manager.py) -- get_settings() below never
includes either secret (Telegram token or OpenAI key) in what it
returns; use get_effective_telegram_bot_token()/set_telegram_bot_token()
or get_effective_openai_api_key()/set_openai_api_key() instead, and
api/routers/telegram.py's /status endpoint for a configured/running
summary that's safe to expose to the frontend for Telegram specifically.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from elly_server.config import get_openai_api_key as _env_openai_api_key
from elly_server.config import get_telegram_bot_token as _env_telegram_bot_token
from elly_server.db.models import AppSettings
from elly_server.db.serialize import model_to_dict

VALID_PROVIDERS = ("openai", "ollama")

# A curated set of common ISO 4217 currency codes -- not exhaustive
# (there are ~180 in circulation), but covers the large majority of
# single-user households this app is built for. One global choice for
# the whole app (see db/models.py's BudgetEntry docstring for why not
# per-entry: no exchange-rate data source exists here, and mixed-
# currency totals would need one). Symbol/display formatting is a
# frontend concern (Intl.NumberFormat handles it given a valid code)
# -- this list is purely for input validation.
VALID_CURRENCIES = (
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY", "INR",
    "BRL", "MXN", "ZAR", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "RON",
    "TRY", "RUB", "KRW", "SGD", "HKD", "THB", "IDR", "PHP", "MYR", "VND",
    "AED", "SAR", "ILS", "EGP", "NGN", "KES", "UAH", "ARS", "CLP", "COP",
)


def _ensure_settings(session: Session) -> AppSettings:
    row = session.scalars(select(AppSettings).limit(1)).first()
    if row is None:
        row = AppSettings()
        session.add(row)
        session.flush()
    return row


def get_settings(session: Session) -> dict[str, Any]:
    row = _ensure_settings(session)
    data = model_to_dict(row)
    # Never leak either raw secret through the generic settings
    # response -- see the module docstring above. The frontend still
    # needs to know *whether* a key is configured (to show "saved" vs.
    # the input form, mirroring /api/telegram/status's bot_configured),
    # so surface that as a plain boolean instead of the value itself --
    # checks the DB value only, not the .env fallback, since Settings
    # UI state should reflect what *it* has configured specifically.
    data.pop("telegram_bot_token", None)
    data["openai_key_configured"] = data.pop("openai_api_key", None) is not None
    return data


def update_settings(
    session: Session,
    llm_provider: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    ollama_model: Optional[str] = None,
    setup_completed: Optional[bool] = None,
    currency: Optional[str] = None,
) -> dict[str, Any]:
    row = _ensure_settings(session)
    if llm_provider is not None:
        if llm_provider not in VALID_PROVIDERS:
            raise ValueError(f"llm_provider must be one of {VALID_PROVIDERS}, got {llm_provider!r}")
        row.llm_provider = llm_provider
    if ollama_base_url is not None:
        row.ollama_base_url = ollama_base_url
    if ollama_model is not None:
        row.ollama_model = ollama_model
    if setup_completed is not None:
        row.setup_completed = setup_completed
    if currency is not None:
        code = currency.strip().upper()
        if code not in VALID_CURRENCIES:
            raise ValueError(f"currency must be one of {VALID_CURRENCIES}, got {currency!r}")
        row.currency = code
    session.flush()
    return get_settings(session)


def set_telegram_bot_token(session: Session, token: Optional[str]) -> None:
    """Set (or, with token=None, clear) the Telegram bot token.

    A separate function/endpoint from update_settings() above,
    deliberately -- this is the one secret this table holds, so it gets
    its own explicit, unambiguous "set" and "clear" (DELETE
    /api/telegram/bot-token) entry points rather than being folded into
    the generic partial-update PUT /api/settings, where a `None` means
    "don't touch" for every other field.
    """
    row = _ensure_settings(session)
    row.telegram_bot_token = token or None
    session.flush()


def get_effective_telegram_bot_token(session: Session) -> Optional[str]:
    """The token actually in effect: the DB value (set via Settings)
    takes precedence, falling back to the ELLY_TELEGRAM_BOT_TOKEN env
    var for existing installs configured the old way. Used by
    telegram_bot/process_manager.py to decide whether/what to spawn,
    and by api/routers/telegram.py's /status endpoint."""
    row = _ensure_settings(session)
    return row.telegram_bot_token or _env_telegram_bot_token()


def set_openai_api_key(session: Session, key: Optional[str]) -> None:
    """Set (or, with key=None, clear) the OpenAI API key.

    Same rationale as set_telegram_bot_token() above -- a separate,
    explicit "set"/"clear" entry point (PUT/DELETE
    /api/settings/openai-key) rather than folding this secret into the
    generic partial-update PUT /api/settings.
    """
    row = _ensure_settings(session)
    row.openai_api_key = key or None
    session.flush()


def get_effective_openai_api_key(session: Session) -> Optional[str]:
    """The key actually in effect: the DB value (set via Settings)
    takes precedence, falling back to the OPENAI_API_KEY env var for
    existing installs configured the old way. Used by
    domain/llm_client.py::get_llm_client()."""
    row = _ensure_settings(session)
    return row.openai_api_key or _env_openai_api_key()
