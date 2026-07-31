"""LLM client (T-16) — OpenAI-compatible, pointed at vLLM (or Ollama in dev).

Exposes a narrow `LLM` protocol so the agent can depend on an interface and tests
can inject `FakeLLM`. The same client works against Ollama's OpenAI-compatible
endpoint with no code change (only `TAPROOT_LLM_BASE_URL` differs).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

import httpx

from taproot.core.logging import get_logger
from taproot.core.models import LLMMessage, LLMResponse, LLMUsage

_log = get_logger(__name__)


@runtime_checkable
class LLM(Protocol):
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse: ...

    async def stream(
        self, messages: list[LLMMessage], *, temperature: float = 0.0
    ) -> AsyncIterator[str]: ...


class LLMClient:
    """OpenAI-compatible chat-completions client."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def _payload(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        if response_format:
            payload["response_format"] = response_format
        return payload

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        start = time.perf_counter()
        resp = await self._http.post(
            f"{self._base}/chat/completions",
            json=self._payload(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                response_format=response_format,
                stream=False,
            ),
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"].get("content") or ""
        usage_raw = data.get("usage", {})
        usage = LLMUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
            completion_tokens=int(usage_raw.get("completion_tokens", 0)),
            total_tokens=int(usage_raw.get("total_tokens", 0)),
        )
        _log.info(
            "llm_call",
            provider="llm",
            model=self._model,
            tokens=usage.total_tokens,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
        return LLMResponse(content=content, usage=usage)

    async def stream(
        self, messages: list[LLMMessage], *, temperature: float = 0.0
    ) -> AsyncIterator[str]:
        import json

        payload = self._payload(
            messages,
            temperature=temperature,
            max_tokens=None,
            tools=None,
            response_format=None,
            stream=True,
        )
        async with self._http.stream(
            "POST", f"{self._base}/chat/completions", json=payload, headers=self._headers
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line.removeprefix("data: ").strip()
                if chunk == "[DONE]":
                    break
                delta = json.loads(chunk)["choices"][0].get("delta", {}).get("content")
                if delta:
                    yield delta


class FakeLLM:
    """Scripted test double. Records received messages so redaction can be
    asserted (T-22): no raw username/token/secret ever reaches the LLM."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or [])
        self.received: list[list[LLMMessage]] = []

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.received.append(messages)
        content = self._responses.pop(0) if self._responses else "{}"
        prompt_tokens = sum(len(m.content.split()) for m in messages)
        completion_tokens = len(content.split())
        return LLMResponse(
            content=content,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    async def stream(
        self, messages: list[LLMMessage], *, temperature: float = 0.0
    ) -> AsyncIterator[str]:
        self.received.append(messages)
        content = self._responses.pop(0) if self._responses else ""
        for token in content.split():
            yield token + " "
