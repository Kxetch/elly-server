"""Shared pytest fixtures/setup for the Elly server test suite.

Critically: ELLY_DATA_DIR is redirected to a fresh temp directory at
import time, before any test module imports elly_server.* -- the DB
engine in elly_server.db.base is a lazily-created module-level singleton,
so this must happen before the very first get_engine() call anywhere
in the test process, never inside a per-test fixture.
"""

from __future__ import annotations

import os
import tempfile

import keyring.errors
import pytest

_tmp_dir = tempfile.mkdtemp(prefix="elly-test-")
os.environ["ELLY_DATA_DIR"] = _tmp_dir
os.environ.setdefault("ELLY_API_PORT", "18765")


@pytest.fixture(autouse=True)
def _never_touch_real_os_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every test onto domain/auth.py's file-based fallback path.

    Without this, running the suite on a macOS/Windows/Linux dev
    machine with a real keyring backend would write actual entries to
    that machine's real OS keychain/credential manager on every test
    run -- not just noisy, but a genuine test-isolation problem for
    security-sensitive code. Global + autouse so no test file (present
    or future) can forget this.
    """
    from elly_server.domain import auth

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise keyring.errors.NoKeyringError("no backend available (test)")

    monkeypatch.setattr(auth.keyring, "get_password", _raise)
    monkeypatch.setattr(auth.keyring, "set_password", _raise)
