"""Ollama connectivity + model management for the Settings UI.

Deliberately scoped to what can be done safely through Ollama's own
REST API: testing whether it's reachable, listing models already
pulled, and pulling (downloading) a new one. Installing the Ollama
binary/service itself stays a one-time manual step outside this app --
Elly has no access to the host OS to run installers (and wouldn't want
that access even if it did; see SECURITY.md's threat model), and inside
Docker there's no host to install onto at all. Ollama's own management
API already covers the rest safely, so there's no need for either.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("elly_server.domain.ollama_admin")

_CONNECT_TIMEOUT_SECONDS = 5.0


def _native_root(base_url: str) -> str:
    """Ollama's OpenAI-compatible chat endpoint is `base_url` (e.g.
    http://localhost:11434/v1, what llm_client.py talks to); its native
    management API (tags/pull) lives one level up, without the /v1
    suffix."""
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        return trimmed[: -len("/v1")]
    return trimmed


async def test_connection(base_url: str) -> dict[str, Any]:
    """Check whether an Ollama server is reachable at `base_url`, and
    if so, what models it already has pulled."""
    root = _native_root(base_url)
    try:
        async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{root}/api/tags")
            resp.raise_for_status()
            data = resp.json()
        models = sorted(m["name"] for m in data.get("models", []) if "name" in m)
        return {"reachable": True, "models": models, "error": None}
    except httpx.ConnectError:
        return {
            "reachable": False,
            "models": [],
            "error": "Couldn't reach Ollama at this address. Check it's running "
            "(`ollama serve`) and the URL is correct.",
        }
    except httpx.TimeoutException:
        return {
            "reachable": False,
            "models": [],
            "error": "Timed out reaching Ollama. Check it's running and the URL is correct.",
        }
    except Exception as e:
        logger.warning("Ollama test-connection failed for %s: %s", root, e)
        return {"reachable": False, "models": [], "error": f"Couldn't reach Ollama: {e}"}


async def pull_model(base_url: str, model: str) -> AsyncIterator[dict[str, Any]]:
    """Stream progress while Ollama downloads `model`.

    Yields the same shape Ollama's own streaming NDJSON /api/pull
    already yields (roughly `{"status": "...", "completed": int,
    "total": int}` while downloading, then a final `{"status":
    "success"}`), plus a synthesized `{"status": "error", "error": ...}`
    on any failure so the caller never has to distinguish "Ollama sent
    an error status" from "we couldn't even reach Ollama".
    """
    root = _native_root(base_url)
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", f"{root}/api/pull", json={"name": model, "stream": True}
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    yield {
                        "status": "error",
                        "error": f"Ollama returned {resp.status_code}: "
                        f"{body.decode(errors='replace')[:200]}",
                    }
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
    except httpx.ConnectError:
        yield {"status": "error", "error": "Couldn't reach Ollama -- check it's running."}
    except Exception as e:
        logger.warning("Ollama pull-model failed for %s/%s: %s", root, model, e)
        yield {"status": "error", "error": str(e)}
