"""Tests for /api/system/restart -- monkeypatches _do_restart so the
test process itself is never actually replaced via os.execv."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from elly_server.api.app import app
from elly_server.api.routers import system as system_router
from elly_server.domain.auth import get_or_create_token
from elly_server.telegram_bot.process_manager import TelegramBotProcessManager

client = TestClient(app)


def _auth_headers() -> dict[str, str]:
    token, _ = get_or_create_token()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _fast_restart_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_router, "RESTART_DELAY_SECONDS", 0.01)


def test_restart_requires_auth() -> None:
    resp = client.post("/api/system/restart")
    assert resp.status_code == 401


def test_restart_responds_immediately_and_schedules_do_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(system_router, "_do_restart", lambda: calls.append(True))

    resp = client.post("/api/system/restart", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"status": "restarting"}

    # _do_restart runs on a background thread shortly after responding
    # -- give it a moment to fire without ever actually calling
    # os.execv (which would replace this test process).
    deadline = time.time() + 2
    while time.time() < deadline and not calls:
        time.sleep(0.02)
    assert calls == [True]


def test_restart_stops_telegram_bot_subprocess_first(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_manager = TelegramBotProcessManager()
    stop_calls: list[bool] = []
    monkeypatch.setattr(fake_manager, "stop", lambda: stop_calls.append(True))

    monkeypatch.setattr(system_router, "_do_restart", lambda: None)

    import elly_server.telegram_bot.process_manager as pm_module

    monkeypatch.setattr(pm_module, "telegram_process_manager", fake_manager)

    resp = client.post("/api/system/restart", headers=_auth_headers())
    assert resp.status_code == 200

    deadline = time.time() + 2
    while time.time() < deadline and not stop_calls:
        time.sleep(0.02)
    assert stop_calls == [True]
