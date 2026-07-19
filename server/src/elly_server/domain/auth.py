"""Local API access token: generated once, verified on every REST request.

Design rationale: this app binds strictly to 127.0.0.1 and has no
multi-user concept, but an unauthenticated localhost API is still a
real attack surface -- a malicious website open in another browser tab,
or any other local process/script, can otherwise freely read/write/
delete every diary entry, chat conversation, and memory just by
knowing the port number. A single long-lived bearer token, generated
on first boot and never sent anywhere except from this same machine's
browser (after being typed in once), closes that gap without adding
any account/password system to a genuinely single-user app.

Storage: the OS keychain (via `keyring`) when one is available -- macOS
Keychain, Windows Credential Manager, or a Linux Secret Service. Falls
back to a plain file (0600 permissions, outside the repo, alongside the
SQLite DB) when no OS keyring backend exists, which is the normal case
inside a headless Docker container / Raspberry Pi / CI runner. Never
falls back to an environment variable or committed file.
"""

from __future__ import annotations

import hmac
import secrets
from pathlib import Path

import keyring
import keyring.errors

from elly_server.config import get_data_dir

_SERVICE_NAME = "Elly"
_TOKEN_KEY = "elly-api-token"

# The app was previously named "KX" -- get_or_create_token() below
# checks these once, on the first call after upgrading, and copies an
# existing token forward rather than silently generating a new one
# (which would invalidate every browser's saved token and force a
# re-auth for no real reason). The old entries are left in place, not
# deleted, so this is always safe to no-op if already migrated.
_OLD_SERVICE_NAME = "KX"
_OLD_TOKEN_KEY = "kx-api-token"
_OLD_TOKEN_FILENAME = ".kx_token"


def _token_file_path() -> Path:
    return get_data_dir() / ".elly_token"


def _read_token_file() -> str | None:
    path = _token_file_path()
    if not path.exists():
        return None
    value = path.read_text().strip()
    return value or None


def _write_token_file(token: str) -> None:
    path = _token_file_path()
    path.write_text(token)
    path.chmod(0o600)


def get_or_create_token() -> tuple[str, bool]:
    """Return (token, was_just_created).

    Tries the OS keyring first; falls back to a locked-down local file
    if no keyring backend is available (headless/Docker/CI). Only
    generates a new token if neither the new nor a pre-rebrand ("KX")
    store already has one.
    """
    try:
        existing = keyring.get_password(_SERVICE_NAME, _TOKEN_KEY)
        if existing:
            return existing, False

        old = keyring.get_password(_OLD_SERVICE_NAME, _OLD_TOKEN_KEY)
        if old:
            keyring.set_password(_SERVICE_NAME, _TOKEN_KEY, old)
            return old, False

        token = secrets.token_hex(32)
        keyring.set_password(_SERVICE_NAME, _TOKEN_KEY, token)
        return token, True
    except keyring.errors.KeyringError:
        pass  # no usable OS keyring backend -- fall through to file storage

    existing_file = _read_token_file()
    if existing_file:
        return existing_file, False

    old_file = get_data_dir() / _OLD_TOKEN_FILENAME
    if old_file.exists():
        old_value = old_file.read_text().strip()
        if old_value:
            _write_token_file(old_value)
            return old_value, False

    token = secrets.token_hex(32)
    _write_token_file(token)
    return token, True


def verify_token(candidate: str | None) -> bool:
    """Constant-time comparison against the stored token.

    Returns False (never raises) for missing/empty candidates.
    """
    if not candidate:
        return False
    token, _ = get_or_create_token()
    return hmac.compare_digest(candidate, token)
