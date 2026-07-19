"""Tests for domain/ollama_admin.py -- mocks httpx.AsyncClient entirely
so these never make a real network call to a real Ollama server."""

from __future__ import annotations

import json

import httpx
import pytest

from elly_server.domain import ollama_admin


def test_native_root_strips_v1_suffix() -> None:
    assert ollama_admin._native_root("http://localhost:11434/v1") == "http://localhost:11434"


def test_native_root_leaves_bare_url_alone() -> None:
    assert ollama_admin._native_root("http://localhost:11434") == "http://localhost:11434"


def test_native_root_strips_trailing_slash() -> None:
    assert ollama_admin._native_root("http://localhost:11434/v1/") == "http://localhost:11434"


class _FakeResponse:
    def __init__(self, status_code: int, json_data=None) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)  # type: ignore[arg-type]

    def json(self):
        return self._json


class _FakeAsyncClient:
    """Fake for `async with httpx.AsyncClient(...) as client: await client.get(...)`."""

    def __init__(self, *, get_result=None, get_error=None, **_kwargs) -> None:
        self._get_result = get_result
        self._get_error = get_error

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get(self, url: str):
        if self._get_error is not None:
            raise self._get_error
        return self._get_result


@pytest.mark.asyncio
async def test_connection_reachable_lists_models(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeAsyncClient(
        get_result=_FakeResponse(200, {"models": [{"name": "llama3.1"}, {"name": "qwen2.5"}]})
    )
    monkeypatch.setattr(ollama_admin.httpx, "AsyncClient", lambda **kw: fake)

    result = await ollama_admin.test_connection("http://localhost:11434/v1")
    assert result["reachable"] is True
    assert result["models"] == ["llama3.1", "qwen2.5"]
    assert result["error"] is None


@pytest.mark.asyncio
async def test_connection_unreachable_reports_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeAsyncClient(get_error=httpx.ConnectError("refused"))
    monkeypatch.setattr(ollama_admin.httpx, "AsyncClient", lambda **kw: fake)

    result = await ollama_admin.test_connection("http://localhost:11434/v1")
    assert result["reachable"] is False
    assert result["models"] == []
    assert "running" in result["error"].lower()


@pytest.mark.asyncio
async def test_connection_timeout_reports_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeAsyncClient(get_error=httpx.TimeoutException("timed out"))
    monkeypatch.setattr(ollama_admin.httpx, "AsyncClient", lambda **kw: fake)

    result = await ollama_admin.test_connection("http://localhost:11434/v1")
    assert result["reachable"] is False
    assert "timed out" in result["error"].lower()


class _FakeStreamResponse:
    def __init__(self, status_code: int, lines: list[str]) -> None:
        self.status_code = status_code
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return b"error body"


class _FakeStreamCtx:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *exc) -> None:
        return None


class _FakeStreamingClient:
    def __init__(self, *, response: _FakeStreamResponse, **_kwargs) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeStreamingClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def stream(self, method: str, url: str, json=None):  # noqa: A002
        return _FakeStreamCtx(self._response)


@pytest.mark.asyncio
async def test_pull_model_streams_progress_events(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        json.dumps({"status": "pulling manifest"}),
        json.dumps({"status": "downloading", "completed": 50, "total": 100}),
        json.dumps({"status": "success"}),
    ]
    fake_response = _FakeStreamResponse(200, lines)
    monkeypatch.setattr(
        ollama_admin.httpx, "AsyncClient", lambda **kw: _FakeStreamingClient(response=fake_response)
    )

    events = [e async for e in ollama_admin.pull_model("http://localhost:11434/v1", "llama3.1")]
    assert events == [
        {"status": "pulling manifest"},
        {"status": "downloading", "completed": 50, "total": 100},
        {"status": "success"},
    ]


@pytest.mark.asyncio
async def test_pull_model_reports_non_200_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = _FakeStreamResponse(404, [])
    monkeypatch.setattr(
        ollama_admin.httpx, "AsyncClient", lambda **kw: _FakeStreamingClient(response=fake_response)
    )

    events = [e async for e in ollama_admin.pull_model("http://localhost:11434/v1", "nonexistent-model")]
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert "404" in events[0]["error"]


@pytest.mark.asyncio
async def test_pull_model_connect_error_yields_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RaisingClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self) -> "_RaisingClient":
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        def stream(self, method: str, url: str, json=None):  # noqa: A002
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(ollama_admin.httpx, "AsyncClient", lambda **kw: _RaisingClient())

    events = [e async for e in ollama_admin.pull_model("http://localhost:11434/v1", "llama3.1")]
    assert len(events) == 1
    assert events[0]["status"] == "error"
