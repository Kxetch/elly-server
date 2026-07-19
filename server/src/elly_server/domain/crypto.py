"""Field-level encryption for Elly's most sensitive stored content.

Design rationale (see SECURITY.md for the full writeup): whole-database
encryption via SQLCipher was considered and rejected -- the standard
Python package (`sqlcipher3-binary`) only ships prebuilt wheels for
x86_64 Linux, meaning it would need native compilation (the SQLCipher C
library + OpenSSL + a compiler toolchain) on macOS dev machines *and*
on Raspberry Pi Docker builds. That would break the "just `uv sync`" /
"just `docker compose up`" simplicity this project is built around.

Instead: field-level encryption via `cryptography`'s Fernet
(authenticated AES-128-CBC + HMAC), which has genuinely universal wheel
support (macOS + Linux, arm64 + x86_64) and adds zero new build
complexity. What's encrypted: `Note.body`/`title` (diary/notebook
content), `Memory.content` (remembered facts/goals/preferences), and
`ChatMessage.content`/`tool_arguments` (conversation history --
`tool_arguments` matters too, since a diary entry's body can flow
through as a tool call argument and would otherwise sit in cleartext
there even with `content` encrypted). Event/Task titles and metadata
(timestamps, IDs, mood/energy numbers) remain plaintext -- see
SECURITY.md for exactly why and what that means.

Key storage mirrors domain/auth.py's pattern exactly: the OS keyring
when available, a locked-down `0600` file fallback otherwise (the
normal case inside a headless Docker container).
"""

from __future__ import annotations

from pathlib import Path

import keyring
import keyring.errors
from cryptography.fernet import Fernet, InvalidToken

from elly_server.config import get_data_dir

_SERVICE_NAME = "Elly"
_KEY_NAME = "elly-db-encryption-key"

# The app was previously named "KX" -- get_or_create_key() below checks
# these once and copies an existing key forward rather than generating
# a brand new one. This is the single most important migration in this
# whole rebrand: generating a *new* key here instead of reusing the
# real one would make every already-encrypted diary entry, memory, and
# chat message permanently undecryptable. The old entries are left in
# place, not deleted.
_OLD_SERVICE_NAME = "KX"
_OLD_KEY_NAME = "kx-db-encryption-key"
_OLD_KEY_FILENAME = ".kx_dbkey"


def _key_file_path() -> Path:
    return get_data_dir() / ".elly_dbkey"


def _read_key_file() -> bytes | None:
    path = _key_file_path()
    if not path.exists():
        return None
    value = path.read_text().strip()
    return value.encode() if value else None


def _write_key_file(key: bytes) -> None:
    path = _key_file_path()
    path.write_text(key.decode())
    path.chmod(0o600)


def get_or_create_key() -> bytes:
    """Return the Fernet key, generating and persisting one on first use.

    Tries the OS keyring first; falls back to a locked-down local file
    if no keyring backend is available (headless/Docker/CI) -- same
    fallback strategy as domain/auth.py's API token. Checks for a
    pre-rebrand ("KX") key before ever generating a new one -- see the
    module-level comment above _OLD_SERVICE_NAME for why that matters.
    """
    try:
        existing = keyring.get_password(_SERVICE_NAME, _KEY_NAME)
        if existing:
            return existing.encode()

        old = keyring.get_password(_OLD_SERVICE_NAME, _OLD_KEY_NAME)
        if old:
            keyring.set_password(_SERVICE_NAME, _KEY_NAME, old)
            return old.encode()

        key = Fernet.generate_key()
        keyring.set_password(_SERVICE_NAME, _KEY_NAME, key.decode())
        return key
    except keyring.errors.KeyringError:
        pass  # no usable OS keyring backend -- fall through to file storage

    existing_file = _read_key_file()
    if existing_file:
        return existing_file

    old_file = get_data_dir() / _OLD_KEY_FILENAME
    if old_file.exists():
        old_value = old_file.read_text().strip()
        if old_value:
            _write_key_file(old_value.encode())
            return old_value.encode()

    key = Fernet.generate_key()
    _write_key_file(key)
    return key


def _get_fernet() -> Fernet:
    # Deliberately not cached at module scope: this keeps test isolation
    # simple (no stale-key cache to reset between tests using different
    # monkeypatched keyrings/data dirs), and Fernet construction from an
    # already-generated key is cheap -- not a meaningful hot path cost
    # for a single-user, personal-scale app.
    return Fernet(get_or_create_key())


def encrypt_text(value: str) -> str:
    """Encrypt a plaintext string, returning a Fernet token (str)."""
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_text(value: str) -> str:
    """Decrypt a Fernet token back to the original plaintext string.

    Raises ValueError (not the low-level InvalidToken) if the value
    can't be decrypted -- e.g. the encryption key was lost/rotated, or
    the value predates encryption being enabled and was never migrated.
    """
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise ValueError(
            "Could not decrypt stored content -- the encryption key may be "
            "missing, rotated, or this data predates encryption and was "
            "never migrated."
        ) from e
