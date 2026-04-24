from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

import httpx

from app.core.config import settings
from app.services.model_service import RuntimeModel


def sanitize_generated_text(text: str, *, aggressive: bool = True) -> str:
    cleaned = (text or "").strip()
    basic_markers = [
        "<|endoftext|>",
        "<|im_end|>",
        "<|im_start|>user",
        "<|im_start|>assistant",
    ]
    aggressive_markers = [
        "\nuser\n",
        "\nassistant\n",
        "\nHuman:",
        "\nAssistant:",
        "\nUser:",
        "Human:",
        "Assistant:",
        "User:",
    ]
    markers = basic_markers + aggressive_markers if aggressive else basic_markers
    for marker in markers:
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()
    if aggressive:
        cleaned = re.split(r"(?:\n|^)(?:###\s*)?(?:Human|Assistant|User)\s*:", cleaned, maxsplit=1)[0].strip()
    return cleaned


def resolve_chat_completions_url(base_url: str) -> str:
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        raise ValueError("OpenAI Base URL 不能为空")
    if raw.endswith("/chat/completions"):
        return raw
    return f"{raw}/chat/completions"


def _decode_error_body(raw_body: bytes) -> str:
    text = (raw_body or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            code = str(error.get("code") or "").strip()
            if message and code:
                return f"{message} ({code})"
            if message:
                return message
        message = str(payload.get("message") or payload.get("msg") or "").strip()
        if message:
            return message
    return text


def _normalize_tool_call(raw: dict[str, Any]) -> dict[str, Any]:
    function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
    arguments_json = str(function.get("arguments") or "")
    arguments: Any
    try:
        arguments = json.loads(arguments_json) if arguments_json.strip() else {}
    except json.JSONDecodeError:
        arguments = arguments_json
    return {
        "id": str(raw.get("id") or ""),
        "type": str(raw.get("type") or "function"),
        "function": {
            "name": str(function.get("name") or ""),
            "arguments": arguments,
            "arguments_json": arguments_json,
        },
    }


async def stream_agent_turn(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model_config: RuntimeModel | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    tool_choice: str = "auto",
) -> AsyncIterator[tuple[str, dict | str]]:
    runtime_model = model_config or RuntimeModel(
        model_key=settings.openai_model,
        display_name=settings.openai_model,
        provider="openai-compatible",
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        upstream_model=settings.openai_model,
        max_tokens=max_tokens,
        temperature_default=temperature,
    )
    if not runtime_model.api_key:
        raise RuntimeError("服务端未配置 OpenAI-compatible API Key")

    url = resolve_chat_completions_url(runtime_model.base_url)
    payload: dict[str, Any] = {
        "model": runtime_model.upstream_model,
        "messages": messages,
        "max_tokens": max(1, int(max_tokens or runtime_model.max_tokens)),
        "temperature": float(temperature if temperature is not None else runtime_model.temperature_default),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"

    headers = {
        "Authorization": f"Bearer {runtime_model.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    accumulated_text = ""
    accumulated_reasoning = ""
    prompt_tokens = 0
    completion_tokens = 0
    tool_calls_by_index: dict[int, dict[str, Any]] = {}

    try:
        async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    detail = _decode_error_body(await response.aread())
                    suffix = f": {detail[:800]}" if detail else ""
                    raise RuntimeError(
                        f"模型服务返回 HTTP {response.status_code}（model={runtime_model.upstream_model}）{suffix}"
                    )
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break

                    chunk = json.loads(data_str)
                    usage = chunk.get("usage") or {}
                    prompt_tokens = max(prompt_tokens, int(usage.get("prompt_tokens", 0) or 0))
                    completion_tokens = max(completion_tokens, int(usage.get("completion_tokens", 0) or 0))

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    reasoning_piece = str(delta.get("reasoning_content") or "")
                    if reasoning_piece:
                        accumulated_reasoning += reasoning_piece
                        yield "reasoning", accumulated_reasoning

                    piece = str(delta.get("content") or "")
                    if piece:
                        accumulated_text += piece
                        yield "partial", sanitize_generated_text(accumulated_text, aggressive=False)

                    for raw_call in delta.get("tool_calls") or []:
                        if not isinstance(raw_call, dict):
                            continue
                        index = int(raw_call.get("index", len(tool_calls_by_index)) or 0)
                        current = tool_calls_by_index.setdefault(
                            index,
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if raw_call.get("id"):
                            current["id"] = raw_call.get("id")
                        if raw_call.get("type"):
                            current["type"] = raw_call.get("type")
                        raw_function = raw_call.get("function")
                        if isinstance(raw_function, dict):
                            current_function = current.setdefault("function", {"name": "", "arguments": ""})
                            if raw_function.get("name"):
                                current_function["name"] = str(current_function.get("name") or "") + str(
                                    raw_function.get("name") or ""
                                )
                            if raw_function.get("arguments"):
                                current_function["arguments"] = str(current_function.get("arguments") or "") + str(
                                    raw_function.get("arguments") or ""
                                )
    except httpx.ReadTimeout as exc:
        raise RuntimeError(
            f"调用模型超时（{settings.openai_timeout_seconds} 秒）。请检查当前网络、代理设置，或稍后重试。"
        ) from exc
    except httpx.ConnectError as exc:
        raise RuntimeError("无法连接模型服务，请检查网络或服务端出网能力。") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"模型请求失败: {exc}") from exc

    cleaned = sanitize_generated_text(accumulated_text, aggressive=False)
    normalized_tool_calls = [
        _normalize_tool_call(tool_calls_by_index[index])
        for index in sorted(tool_calls_by_index)
        if str(tool_calls_by_index[index].get("function", {}).get("name") or "").strip()
    ]
    for tool_call in normalized_tool_calls:
        yield "tool_call", tool_call

    if completion_tokens <= 0 and cleaned:
        completion_tokens = max(1, len(cleaned) // 2)
    yield "usage", {
        "text": cleaned,
        "reasoning_content": accumulated_reasoning.strip(),
        "tool_calls": normalized_tool_calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "model": runtime_model.model_key,
        "provider": runtime_model.provider,
    }
