from __future__ import annotations

import httpx
import pytest

from app.services import llm_service
from app.services.model_service import RuntimeModel


def _runtime_model() -> RuntimeModel:
    return RuntimeModel(
        model_key="vanchin/deepseek-v3",
        display_name="vanchin/deepseek-v3",
        provider="openai-compatible",
        base_url="https://example.com/v1",
        api_key="test-key",
        upstream_model="vanchin/deepseek-v3",
        max_tokens=1024,
        temperature_default=0.2,
    )


@pytest.mark.asyncio
async def test_stream_completion_includes_error_body_for_stream_status_error(monkeypatch):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "Model does not support chat completions", "code": "invalid_request"}},
        )

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def async_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", async_client_factory)

    with pytest.raises(RuntimeError) as exc_info:
        async for _kind, _item in llm_service.stream_completion(
            system_prompt="system",
            user_prompt="hello",
            model_config=_runtime_model(),
        ):
            pass

    message = str(exc_info.value)
    assert "HTTP 400" in message
    assert "vanchin/deepseek-v3" in message
    assert "Model does not support chat completions" in message
    assert "invalid_request" in message
