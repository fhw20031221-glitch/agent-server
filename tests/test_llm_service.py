from __future__ import annotations

import json

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
async def test_stream_agent_turn_includes_error_body_for_stream_status_error(monkeypatch):
    seen_payload = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content.decode("utf-8")))
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
        async for _kind, _item in llm_service.stream_agent_turn(
            messages=[{"role": "user", "content": "hello"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "workspace_search",
                        "description": "搜索",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            model_config=_runtime_model(),
        ):
            pass

    assert seen_payload["tools"][0]["function"]["name"] == "workspace_search"
    assert seen_payload["tool_choice"] == "auto"
    message = str(exc_info.value)
    assert "HTTP 400" in message
    assert "vanchin/deepseek-v3" in message
    assert "Model does not support chat completions" in message
    assert "invalid_request" in message


@pytest.mark.asyncio
async def test_stream_agent_turn_parses_streamed_tool_call(monkeypatch):
    async def handler(_request: httpx.Request) -> httpx.Response:
        chunks = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "workspace_read_file", "arguments": '{"path"'},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": ':"app/main.py"}'}}
                            ]
                        }
                    }
                ]
            },
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        body = "\n\n".join(
            [*[f"data: {json.dumps(chunk, ensure_ascii=False)}" for chunk in chunks], "data: [DONE]"]
        )
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def async_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", async_client_factory)

    events = []
    async for kind, item in llm_service.stream_agent_turn(
        messages=[{"role": "user", "content": "读取入口"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "workspace_read_file",
                    "description": "读取文件",
                    "parameters": {"type": "object"},
                },
            }
        ],
        model_config=_runtime_model(),
    ):
        events.append((kind, item))

    tool_call = next(item for kind, item in events if kind == "tool_call")
    assert tool_call["id"] == "call_1"
    assert tool_call["function"]["name"] == "workspace_read_file"
    assert tool_call["function"]["arguments"] == {"path": "app/main.py"}

    usage = next(item for kind, item in events if kind == "usage")
    assert usage["tool_calls"][0]["function"]["arguments_json"] == '{"path":"app/main.py"}'
    assert usage["prompt_tokens"] == 10


@pytest.mark.asyncio
async def test_stream_agent_turn_parses_message_tool_call_with_object_arguments(monkeypatch):
    edit_arguments = {
        "summary": "补充主程序注释",
        "edits": [
            {
                "path": "app/main.py",
                "search": "app = FastAPI()",
                "replace": "# 创建 FastAPI 应用\napp = FastAPI()",
            }
        ],
    }

    async def handler(_request: httpx.Request) -> httpx.Response:
        chunks = [
            {
                "usage": {"prompt_tokens": 18, "completion_tokens": 6},
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_edit",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_propose_edit",
                                        "arguments": edit_arguments,
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        ]
        body = "\n\n".join(
            [*[f"data: {json.dumps(chunk, ensure_ascii=False)}" for chunk in chunks], "data: [DONE]"]
        )
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def async_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", async_client_factory)

    events = []
    async for kind, item in llm_service.stream_agent_turn(
        messages=[{"role": "user", "content": "给主程序入口加上更多注释"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "workspace_propose_edit",
                    "description": "编辑预览",
                    "parameters": {"type": "object"},
                },
            }
        ],
        model_config=_runtime_model(),
    ):
        events.append((kind, item))

    tool_call = next(item for kind, item in events if kind == "tool_call")
    assert tool_call["id"] == "call_edit"
    assert tool_call["function"]["name"] == "workspace_propose_edit"
    assert tool_call["function"]["arguments"] == edit_arguments
    assert tool_call["function"]["arguments_json"] == json.dumps(edit_arguments, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.asyncio
async def test_stream_agent_turn_parses_legacy_function_call_chunks(monkeypatch):
    async def handler(_request: httpx.Request) -> httpx.Response:
        chunks = [
            {"choices": [{"delta": {"function_call": {"name": "workspace_search", "arguments": '{"query"'}}}]},
            {"choices": [{"delta": {"function_call": {"arguments": ':"VALID_ASPECT_RATIOS"}'}}}]},
            {"usage": {"prompt_tokens": 9, "completion_tokens": 3}, "choices": [{"delta": {}}]},
        ]
        body = "\n\n".join(
            [*[f"data: {json.dumps(chunk, ensure_ascii=False)}" for chunk in chunks], "data: [DONE]"]
        )
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def async_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(llm_service.httpx, "AsyncClient", async_client_factory)

    events = []
    async for kind, item in llm_service.stream_agent_turn(
        messages=[{"role": "user", "content": "搜索比例"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "workspace_search",
                    "description": "搜索",
                    "parameters": {"type": "object"},
                },
            }
        ],
        model_config=_runtime_model(),
    ):
        events.append((kind, item))

    tool_call = next(item for kind, item in events if kind == "tool_call")
    assert tool_call["id"] == "function_call"
    assert tool_call["function"]["name"] == "workspace_search"
    assert tool_call["function"]["arguments"] == {"query": "VALID_ASPECT_RATIOS"}
