"""Tests for domain/auth.py -- local API token generation/verification.

The file-based fallback path is forced globally for the whole test
suite (see conftest.py's autouse `_never_touch_real_os_keyring` fixture)
so these never touch the actual macOS Keychain/Windows Credential
Manager/Linux Secret Service on the machine running them.
"""

from __future__ import annotations

import stat

import pytest

from elly_server.domain import auth


def test_get_or_create_token_generates_once(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_data_dir", lambda: tmp_path)

    token1, created1 = auth.get_or_create_token()
    assert created1 is True
    assert len(token1) == 64  # 32 bytes hex-encoded

    token2, created2 = auth.get_or_create_token()
    assert created2 is False
    assert token2 == token1


def test_token_file_has_restrictive_permissions(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_data_dir", lambda: tmp_path)
    auth.get_or_create_token()
    token_file = tmp_path / ".elly_token"
    assert token_file.exists()
    mode = stat.S_IMODE(token_file.stat().st_mode)
    assert mode == 0o600


def test_verify_token_correct_and_incorrect(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_data_dir", lambda: tmp_path)
    token, _ = auth.get_or_create_token()

    assert auth.verify_token(token) is True
    assert auth.verify_token(token + "x") is False
    assert auth.verify_token("completely-wrong") is False


def test_verify_token_rejects_missing_or_empty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_data_dir", lambda: tmp_path)
    assert auth.verify_token(None) is False
    assert auth.verify_token("") is False
