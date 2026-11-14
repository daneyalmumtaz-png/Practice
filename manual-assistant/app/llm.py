"""Chat completions over an OpenAI-compatible API.

Configured by environment variables only, so the same code runs against Ollama
at http://ollama:11434/v1 or a cloud provider, with no code change:
LLM_BASE_URL, LLM_API_KEY, LLM_MODEL.

Every call has a timeout. A model that stops answering must not hold a request
open forever, which is what left the old prototype waiting.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Sequence

import httpx

from .config import Settings, get_settings
from .logsetup import get_logger

log = get_logger("llm")

Message = dict[str, str]


class LLMError(RuntimeError):
    """The model could not be reached or refused the request."""


def _headers(settings: Settings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    return headers


def _payload(
    messages: Sequence[Message],
    settings: Settings,
    *,
    stream: bool,
    temperature: float | None,
    max_tokens: int | None,
) -> dict[str, object]:
    return {
        "model": settings.llm_model,
        "messages": list(messages),
        "temperature": (
            settings.llm_temperature if temperature is None else temperature
        ),
        "max_tokens": settings.llm_max_tokens if max_tokens is None else max_tokens,
        "stream": stream,
    }


async def complete(
    messages: Sequence[Message],
    *,
    settings: Settings | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> str:
    """One short answer, not streamed. Used for the rewriting steps."""
    s = settings or get_settings()
    url = s.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=timeout or s.llm_timeout_s) as client:
            response = await client.post(
                url,
                headers=_headers(s),
                json=_payload(
                    messages, s, stream=False, temperature=temperature,
                    max_tokens=max_tokens,
                ),
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:                       # noqa: BLE001 - one error type out
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc

    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"unexpected response shape: {data!r}") from exc


async def stream(
    messages: Sequence[Message],
    *,
    settings: Settings | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """Text fragments as the model produces them."""
    s = settings or get_settings()
    url = s.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=s.llm_timeout_s) as client:
            async with client.stream(
                "POST",
                url,
                headers=_headers(s),
                json=_payload(
                    messages, s, stream=True, temperature=temperature,
                    max_tokens=max_tokens,
                ),
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:500]
                    raise LLMError(f"HTTP {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload in ("", "[DONE]"):
                        if payload == "[DONE]":
                            return
                        continue
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices") or []:
                        piece = (choice.get("delta") or {}).get("content")
                        if piece:
                            yield piece
    except LLMError:
        raise
    except Exception as exc:                       # noqa: BLE001 - one error type out
        raise LLMError(f"{type(exc).__name__}: {exc}") from exc


async def service_status(settings: Settings | None = None) -> tuple[bool, str]:
    """Used by /healthz. Never raises."""
    s = settings or get_settings()
    try:
        async with httpx.AsyncClient(timeout=min(10.0, s.llm_timeout_s)) as client:
            response = await client.get(
                s.llm_base_url.rstrip("/") + "/models", headers=_headers(s)
            )
            if response.status_code >= 400:
                return False, f"HTTP {response.status_code} from {s.llm_base_url}"
        return True, f"{s.llm_model} reachable"
    except Exception as exc:                       # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
