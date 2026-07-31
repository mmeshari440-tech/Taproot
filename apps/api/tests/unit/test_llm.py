from __future__ import annotations

import httpx

from taproot.core.models import LLMMessage
from taproot.integrations.llm import FakeLLM, LLMClient


async def test_fake_llm_records_messages_and_counts_tokens() -> None:
    fake = FakeLLM(responses=['{"severity": "HIGH"}'])
    result = await fake.complete([LLMMessage(role="user", content="analyze this error")])
    assert result.content == '{"severity": "HIGH"}'
    assert result.usage.total_tokens > 0
    # Received messages are retained so redaction can be asserted (T-22).
    assert fake.received[0][0].content == "analyze this error"


async def test_fake_llm_defaults_to_empty_json() -> None:
    fake = FakeLLM()
    result = await fake.complete([LLMMessage(role="user", content="hi")])
    assert result.content == "{}"


async def test_llm_client_parses_completion_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = request.read().decode()
        assert "Qwen-test" in body
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "root cause: partner timeout"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://vllm.test")
    client = LLMClient("https://vllm.test/v1", "Qwen-test", http_client=http)

    result = await client.complete([LLMMessage(role="user", content="why did it fail?")])
    assert result.content == "root cause: partner timeout"
    assert result.usage.total_tokens == 14


async def test_llm_client_streams_deltas() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        stream = (
            'data: {"choices":[{"delta":{"content":"partner"}}]}\n'
            'data: {"choices":[{"delta":{"content":" timeout"}}]}\n'
            "data: [DONE]\n"
        )
        return httpx.Response(200, text=stream)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://vllm.test")
    client = LLMClient("https://vllm.test/v1", "Qwen-test", http_client=http)

    chunks = [c async for c in client.stream([LLMMessage(role="user", content="x")])]
    assert "".join(chunks) == "partner timeout"
