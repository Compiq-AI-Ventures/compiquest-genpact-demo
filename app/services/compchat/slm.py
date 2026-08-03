"""Bedrock client for CompChat.

Two call shapes:

* :func:`complete_json` — non-streaming, prompt-enforced JSON. Bedrock's
  raw ``InvokeModel`` API has no grammar-constrained decoding, so the
  prompt instructs JSON-only output and the caller validates the parsed
  result against a Pydantic schema; the classifier falls back to its
  rules path on any parse/validation failure.
* :func:`stream_generate` — chunked narration for the answer. Runs one
  non-streaming Bedrock completion (boto3 has no async/streaming-friendly
  client suitable here) and yields it back in fixed-size chunks so
  callers can treat it as a token stream.

Callers do not assume Bedrock is reachable — failure handling is theirs
(the classifier falls back to rules; the narrator surfaces a structured
error).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from app.services.bedrock_client import get_bedrock_client

_APP_JSON = "application/json"
_CHUNK_SIZE = 24


def _invoke(settings: Any, prompt: str, max_tokens: int, temperature: float) -> str:
    client = get_bedrock_client(settings)
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    })
    resp = client.invoke_model(
        modelId=settings.bedrock_model_id,
        body=body,
        contentType=_APP_JSON,
        accept=_APP_JSON,
    )
    result = json.loads(resp["body"].read())
    return (result.get("choices") or [{}])[0].get("message", {}).get("content", "")


async def complete_json(
    settings: Any,
    prompt: str,
    schema: dict[str, Any],
    *,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Run one JSON-only completion and return parsed JSON.

    Raises on transport error or unparseable output; the classifier
    catches and falls back to its rules path.
    """
    json_instructions = (
        "Respond with ONLY a single JSON object matching this JSON Schema — "
        "no markdown fences, no commentary, no extra keys:\n"
        f"{json.dumps(schema)}\n\n{prompt}"
    )
    raw = await asyncio.get_running_loop().run_in_executor(
        None, lambda: _invoke(settings, json_instructions, max_tokens=400, temperature=temperature)
    )
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


async def stream_generate(
    settings: Any,
    prompt: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 600,
) -> AsyncIterator[str]:
    """Yield chunked text tokens from a single Bedrock completion."""
    raw = await asyncio.get_running_loop().run_in_executor(
        None, lambda: _invoke(settings, prompt, max_tokens=max_tokens, temperature=temperature)
    )
    for i in range(0, len(raw), _CHUNK_SIZE):
        yield raw[i : i + _CHUNK_SIZE]
