"""Tests for domain/crypto.py -- field-level encryption key management.

Keyring is forced onto the file-based fallback path for the whole test
suite (conftest.py's autouse `_never_touch_real_os_keyring` fixture),
so these never touch the real OS keychain either.
"""

from __future__ import annotations

import stat

import pytest

from elly_server.domain import crypto


def test_get_or_create_key_generates_once(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "get_data_dir", lambda: tmp_path)

    key1 = crypto.get_or_create_key()
    key2 = crypto.get_or_create_key()
    assert key1 == key2  # persisted, not regenerated each call


def test_key_file_has_restrictive_permissions(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "get_data_dir", lambda: tmp_path)
    crypto.get_or_create_key()
    key_file = tmp_path / ".elly_dbkey"
    assert key_file.exists()
    mode = stat.S_IMODE(key_file.stat().st_mode)
    assert mode == 0o600


def test_key_is_distinct_from_the_api_token_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "get_data_dir", lambda: tmp_path)
    crypto.get_or_create_key()
    assert (tmp_path / ".elly_dbkey").exists()
    assert not (tmp_path / ".elly_token").exists()  # domain/auth.py's file, untouched


def test_encrypt_decrypt_round_trip(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "get_data_dir", lambda: tmp_path)
    plaintext = "Something genuinely personal about today."
    ciphertext = crypto.encrypt_text(plaintext)
    assert ciphertext != plaintext
    assert crypto.decrypt_text(ciphertext) == plaintext


def test_ciphertext_does_not_contain_the_plaintext(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "get_data_dir", lambda: tmp_path)
    plaintext = "a very specific and searchable secret string"
    ciphertext = crypto.encrypt_text(plaintext)
    assert plaintext not in ciphertext


def test_decrypt_with_wrong_key_raises_value_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    monkeypatch.setattr(crypto, "get_data_dir", lambda: dir_a)
    ciphertext = crypto.encrypt_text("secret")

    monkeypatch.setattr(crypto, "get_data_dir", lambda: dir_b)  # different key
    with pytest.raises(ValueError, match="Could not decrypt"):
        crypto.decrypt_text(ciphertext)


def test_decrypt_garbage_raises_value_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "get_data_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="Could not decrypt"):
        crypto.decrypt_text("not-a-real-fernet-token")
