"""Integration tests: the Ollama connectivity/model REST surface
(Settings UI). Mocks domain/ollama_admin.py entirely -- never makes a
real network call."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from elly_server.api.app import app
from elly_server.api.routers import ollama as ollama_router
from elly_server.db.base import get_session
from elly_server.db.models import AppSettings
from elly_server.domain.auth import get_or_create_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_settings() -> None:
    with get_session() as session:
        session.execute(delete(AppSettings))


def _auth_headers() -> dict[str, str]:
    token, _ = get_or_create_token()
    return {"Authorization": f"Bearer {token}"}


def test_test_connection_requires_auth() -> None:
    resp = client.post("/api/ollama/test-connection", json={})
    assert resp.status_code == 401


def test_test_connection_uses_default_base_url_when_none_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_urls = []

    async def fake_test_connection(base_url: str):
        seen_urls.append(base_url)
        return {"reachable": True, "models": ["llama3.1"], "error": None}

    monkeypatch.setattr(ollama_router.ollama_admin, "test_connection", fake_test_connection)

    resp = client.post("/api/ollama/test-connection", json={}, headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"reachable": True, "models": ["llama3.1"], "error": None}
    assert seen_urls == ["http://localhost:11434/v1"]


def test_test_connection_uses_explicit_override_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_urls = []

    async def fake_test_connection(base_url: str):
        seen_urls.append(base_url)
        return {"reachable": False, "models": [], "error": "nope"}

    monkeypatch.setattr(ollama_router.ollama_admin, "test_connection", fake_test_connection)

    resp = client.post(
        "/api/ollama/test-connection",
        json={"base_url": "http://192.168.1.50:11434/v1"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert seen_urls == ["http://192.168.1.50:11434/v1"]


def test_test_connection_uses_saved_settings_url(monkeypatch: pytest.MonkeyPatch) -> None:
    with get_session() as session:
        from elly_server.domain import settings as settings_domain

        settings_domain.update_settings(session, ollama_base_url="http://saved-host:11434/v1")

    seen_urls = []

    async def fake_test_connection(base_url: str):
        seen_urls.append(base_url)
        return {"reachable": True, "models": [], "error": None}

    monkeypatch.setattr(ollama_router.ollama_admin, "test_connection", fake_test_connection)

    resp = client.post("/api/ollama/test-connection", json={}, headers=_auth_headers())
    assert resp.status_code == 200
    assert seen_urls == ["http://saved-host:11434/v1"]


def test_pull_model_streams_sse_events(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_pull_model(base_url: str, model: str):
        assert model == "llama3.1"
        yield {"status": "pulling manifest"}
        yield {"status": "success"}

    monkeypatch.setattr(ollama_router.ollama_admin, "pull_model", fake_pull_model)

    with client.stream(
        "POST",
        "/api/ollama/pull-model",
        json={"model": "llama3.1"},
        headers=_auth_headers(),
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    lines = [line for line in body.split("\n\n") if line.strip()]
    events = [json.loads(line.removeprefix("data: ")) for line in lines]
    assert events == [{"status": "pulling manifest"}, {"status": "success"}]


def test_pull_model_requires_auth() -> None:
    resp = client.post("/api/ollama/pull-model", json={"model": "llama3.1"})
    assert resp.status_code == 401
