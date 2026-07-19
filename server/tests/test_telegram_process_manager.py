"""Tests for telegram_bot/process_manager.py using a fake Popen -- never
spawns the real elly-telegram subprocess (which would try to hit
Telegram's real API with whatever fake token a test uses).
"""

from __future__ import annotations

import time
from typing import Optional

import pytest

from elly_server.telegram_bot import process_manager as pm


class FakePopen:
    """Stands in for subprocess.Popen: starts "running" (poll() is
    None) until .terminate()/.kill() is called, or a test flips
    `exit_immediately` to simulate a crash."""

    instances: list["FakePopen"] = []

    def __init__(self, cmd, env=None, **_kwargs) -> None:  # noqa: ANN001
        self.cmd = cmd
        self.env = env
        self._returncode: Optional[int] = None
        self.terminated = False
        self.killed = False
        FakePopen.instances.append(self)

    def poll(self) -> Optional[int]:
        return self._returncode

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = -15

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode if self._returncode is not None else 0

    def simulate_crash(self, code: int = 1) -> None:
        self._returncode = code


@pytest.fixture(autouse=True)
def _fake_popen(monkeypatch: pytest.MonkeyPatch):
    FakePopen.instances = []
    monkeypatch.setattr(pm.subprocess, "Popen", FakePopen)
    # Fast, deterministic monitor-loop timing for tests.
    monkeypatch.setattr(pm, "MONITOR_POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(pm, "RESTART_BACKOFF_SECONDS", 0.02)
    yield FakePopen


def test_start_spawns_subprocess_with_token_in_env() -> None:
    manager = pm.TelegramBotProcessManager()
    manager.start("test-token-123")
    assert manager.is_running() is True
    assert len(FakePopen.instances) == 1
    assert FakePopen.instances[0].env["ELLY_TELEGRAM_BOT_TOKEN"] == "test-token-123"
    manager.stop()


def test_start_is_idempotent_for_same_token() -> None:
    manager = pm.TelegramBotProcessManager()
    manager.start("same-token")
    manager.start("same-token")
    assert len(FakePopen.instances) == 1
    manager.stop()


def test_start_with_new_token_restarts_subprocess() -> None:
    manager = pm.TelegramBotProcessManager()
    manager.start("token-a")
    first = FakePopen.instances[0]
    manager.start("token-b")
    assert first.terminated is True
    assert len(FakePopen.instances) == 2
    assert FakePopen.instances[1].env["ELLY_TELEGRAM_BOT_TOKEN"] == "token-b"
    manager.stop()


def test_stop_terminates_subprocess() -> None:
    manager = pm.TelegramBotProcessManager()
    manager.start("test-token")
    proc = FakePopen.instances[0]
    manager.stop()
    assert proc.terminated is True
    assert manager.is_running() is False


def test_stop_when_never_started_is_safe() -> None:
    manager = pm.TelegramBotProcessManager()
    manager.stop()  # should not raise
    assert manager.is_running() is False


def test_is_running_false_before_start() -> None:
    manager = pm.TelegramBotProcessManager()
    assert manager.is_running() is False


def test_crash_triggers_automatic_restart() -> None:
    manager = pm.TelegramBotProcessManager()
    manager.start("test-token")
    first = FakePopen.instances[0]
    first.simulate_crash()

    # Give the monitor thread a moment to notice and restart (intervals
    # are patched down to ~20ms above, so this is fast and deterministic
    # in practice while still leaving headroom).
    deadline = time.time() + 2
    while time.time() < deadline and len(FakePopen.instances) < 2:
        time.sleep(0.02)

    assert len(FakePopen.instances) == 2
    assert manager.is_running() is True
    manager.stop()


def test_stop_prevents_restart_after_crash() -> None:
    manager = pm.TelegramBotProcessManager()
    manager.start("test-token")
    first = FakePopen.instances[0]
    manager.stop()
    first.simulate_crash()

    time.sleep(0.1)
    # stop() already set _stop_requested -- the monitor thread should
    # have exited already and must not spawn a replacement.
    assert len(FakePopen.instances) == 1
