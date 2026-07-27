"""Ollama SLM client for CompChat.

Two call shapes:

* :func:`complete_json` — non-streaming, **grammar-constrained**. We
  pass a JSON Schema in Ollama's ``format`` field; Ollama compiles it to
  a GBNF grammar so the model is *forced* to emit exactly that shape.
  This is what makes a small model reliable enough for the Layer-4
  classifier without function-calling.
* :func:`stream_generate` — streaming narration for the answer. Yields
  decoded text tokens.

Both are thin async wrappers over the local Ollama HTTP API; they do not
assume Ollama is reachable — callers handle failure (the classifier
falls back to rules; the narrator surfaces a structured error).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

# Classification must be fast and deterministic.
_CLASSIFY_TIMEOUT = 30.0
_GENERATE_TIMEOUT = 120.0


async def complete_json(
    base_url: str,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    *,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Run one schema-constrained completion and return parsed JSON.

    Raises on transport error or unparseable output; the classifier
    catches and falls back to its rules path.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": schema,
        "options": {"temperature": temperature},
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=_CLASSIFY_TIMEOUT) as client:
        resp = await client.post("/api/generate", json=payload)
        resp.raise_for_status()
        body = resp.json()
    # Ollama returns the model's text in "response"; with a schema it is
    # a JSON document.
    return json.loads(body["response"])


async def stream_generate(
    base_url: str,
    model: str,
    prompt: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 600,
) -> AsyncIterator[str]:
    """Yield decoded text tokens from a streaming Ollama generation."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    async with (
        httpx.AsyncClient(base_url=base_url, timeout=_GENERATE_TIMEOUT) as client,
        client.stream("POST", "/api/generate", json=payload) as resp,
    ):
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            token = data.get("response")
            if token:
                yield token
            if data.get("done"):
                break
