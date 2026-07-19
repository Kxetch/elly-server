"""Integration tests: the local API token actually gates the REST API.

Uses FastAPI's TestClient (httpx under the hood) against the real app
object, hitting a real (temp) SQLite DB per conftest.py.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from elly_server.api.app import app
from elly_server.domain.auth import get_or_create_token

client = TestClient(app)


def test_health_is_unauthenticated() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_protected_route_rejects_missing_token() -> None:
    resp = client.get("/api/today")
    assert resp.status_code == 401


def test_protected_route_rejects_wrong_token() -> None:
    resp = client.get("/api/today", headers={"Authorization": "Bearer not-the-real-token"})
    assert resp.status_code == 401


def test_protected_route_accepts_valid_token() -> None:
    token, _ = get_or_create_token()
    resp = client.get("/api/today", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_setup_verify_token_is_unauthenticated_and_correct() -> None:
    token, _ = get_or_create_token()

    ok = client.post("/api/setup/verify-token", json={"token": token})
    assert ok.status_code == 200
    assert ok.json() == {"valid": True}

    bad = client.post("/api/setup/verify-token", json={"token": "wrong"})
    assert bad.status_code == 200
    assert bad.json() == {"valid": False}


def test_settings_route_requires_auth() -> None:
    resp = client.get("/api/settings")
    assert resp.status_code == 401

    token, _ = get_or_create_token()
    resp = client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "llm_provider" in resp.json()
