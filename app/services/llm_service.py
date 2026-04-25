from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from app.core.config import settings
from app.services.model_service import RuntimeModel

logger = logging.getLogger("agent.tools")


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


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _argument_fragment_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return _compact_json(value)
    return str(value)


def _tool_debug(message: str, **fields: Any) -> None:
    if not settings.agent_debug_tools:
        return
    safe_fields: dict[str, Any] = {}
    for key, value in fields.items():
        text = str(value)
        safe_fields[key] = text[:500] + "..." if len(text) > 500 else text
    logger.warning("agent-tools %s %s", message, safe_fields)


def _raw_debug_record(payload: dict[str, Any]) -> None:
    if not settings.agent_debug_raw:
        return
    try:
        file_path = Path(settings.agent_debug_log_file or "agent-raw-debug.jsonl")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logger.exception("agent-tools raw debug write failed")


def _tool_call_items(container: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("tool_calls", "toolCalls", "tool_call", "toolCall"):
        raw = container.get(key)
        if isinstance(raw, list):
            items.extend(item for item in raw if isinstance(item, dict))
        elif isinstance(raw, dict):
            items.append(raw)
    return items


def _function_call_item(container: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("function_call", "functionCall"):
        raw = container.get(key)
        if isinstance(raw, dict):
            return raw
    return None


def _normalize_tool_call(raw: dict[str, Any]) -> dict[str, Any]:
    function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
    raw_arguments = function.get("arguments")
    if raw_arguments is None and "arguments_json" in function:
        raw_arguments = function.get("arguments_json")
    arguments_json = _argument_fragment_to_text(raw_arguments)
    arguments: Any
    if isinstance(raw_arguments, (dict, list)):
        arguments = raw_arguments
    else:
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

    _tool_debug(
        "request",
        model=runtime_model.upstream_model,
        message_count=len(messages),
        tool_names=[
            item.get("function", {}).get("name")
            for item in tools
            if isinstance(item.get("function"), dict)
        ],
        tool_choice=tool_choice,
    )

    def merge_tool_call(
        raw_call: dict[str, Any],
        fallback_index: int = 0,
        *,
        replace: bool = False,
        source: str = "unknown",
    ) -> None:
        try:
            index = int(raw_call.get("index") if raw_call.get("index") is not None else fallback_index)
        except (TypeError, ValueError):
            index = fallback_index
        current = tool_calls_by_index.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if raw_call.get("id"):
            current["id"] = str(raw_call.get("id") or "")
        elif raw_call.get("tool_call_id"):
            current["id"] = str(raw_call.get("tool_call_id") or "")
        elif raw_call.get("call_id"):
            current["id"] = str(raw_call.get("call_id") or "")
        if raw_call.get("type"):
            current["type"] = str(raw_call.get("type") or "")

        raw_function = raw_call.get("function")
        if not isinstance(raw_function, dict):
            raw_function = _function_call_item(raw_call)
        if not isinstance(raw_function, dict) and any(
            key in raw_call for key in ("name", "arguments", "arguments_json")
        ):
            raw_function = raw_call
        if not isinstance(raw_function, dict):
            _tool_debug(
                "raw_tool_call_ignored",
                source=source,
                index=index,
                keys=sorted(raw_call.keys()),
            )
            return
        current_function = current.setdefault("function", {"name": "", "arguments": ""})
        argument_value: Any = None
        has_argument = False
        if "arguments" in raw_function:
            argument_value = raw_function.get("arguments")
            has_argument = True
        elif "arguments_json" in raw_function:
            argument_value = raw_function.get("arguments_json")
            has_argument = True
        _tool_debug(
            "raw_tool_call",
            source=source,
            index=index,
            id=current.get("id") or "",
            replace=replace,
            name_piece=raw_function.get("name") or "",
            argument_type=type(argument_value).__name__ if has_argument else "missing",
            argument_length=len(_argument_fragment_to_text(argument_value)) if has_argument else 0,
        )
        if raw_function.get("name") is not None:
            name_piece = str(raw_function.get("name") or "")
            if replace:
                current_function["name"] = name_piece
            elif name_piece:
                current_function["name"] = str(current_function.get("name") or "") + name_piece

        if has_argument:
            argument_piece = _argument_fragment_to_text(argument_value)
            if replace:
                current_function["arguments"] = argument_piece
            elif argument_piece:
                current_function["arguments"] = str(current_function.get("arguments") or "") + argument_piece

    def merge_legacy_function_call(raw_function: dict[str, Any], *, replace: bool = False) -> None:
        merge_tool_call(
            {
                "index": 0,
                "id": "function_call",
                "type": "function",
                "function": raw_function,
            },
            0,
            replace=replace,
            source="legacy_function_call",
        )

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
                    _raw_debug_record(
                        {
                            "event": "chunk",
                            "model": runtime_model.upstream_model,
                            "chunk": chunk,
                        }
                    )
                    usage = chunk.get("usage") or {}
                    prompt_tokens = max(prompt_tokens, int(usage.get("prompt_tokens", 0) or 0))
                    completion_tokens = max(completion_tokens, int(usage.get("completion_tokens", 0) or 0))

                    choices = chunk.get("choices") or []
                    if not choices:
                        _tool_debug(
                            "chunk_no_choices",
                            usage_prompt=usage.get("prompt_tokens", 0) or 0,
                            usage_completion=usage.get("completion_tokens", 0) or 0,
                            keys=sorted(chunk.keys()),
                        )
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                    reasoning_piece = str(delta.get("reasoning_content") or "")
                    content_piece = delta.get("content")
                    piece = "" if content_piece is None else str(content_piece)
                    _tool_debug(
                        "chunk",
                        finish_reason=choice.get("finish_reason") or "",
                        delta_keys=sorted(delta.keys()) if isinstance(delta, dict) else [],
                        message_keys=sorted(message.keys()) if isinstance(message, dict) else [],
                        content_piece_length=len(piece),
                        reasoning_piece_length=len(reasoning_piece),
                        accumulated_content_length=len(accumulated_text) + len(piece),
                        accumulated_reasoning_length=len(accumulated_reasoning) + len(reasoning_piece),
                        delta_tool_call_count=len(_tool_call_items(delta)) if isinstance(delta, dict) else 0,
                        message_tool_call_count=len(_tool_call_items(message)) if isinstance(message, dict) else 0,
                        has_function_call=bool(_function_call_item(delta) or _function_call_item(message)),
                        usage_prompt=usage.get("prompt_tokens", 0) or 0,
                        usage_completion=usage.get("completion_tokens", 0) or 0,
                    )
                    if reasoning_piece:
                        accumulated_reasoning += reasoning_piece
                        yield "reasoning", accumulated_reasoning

                    if piece:
                        accumulated_text += piece
                        yield "partial", sanitize_generated_text(accumulated_text, aggressive=False)

                    for raw_call_index, raw_call in enumerate(_tool_call_items(delta)):
                        merge_tool_call(raw_call, raw_call_index, source="delta.tool_calls")

                    delta_function_call = _function_call_item(delta)
                    if isinstance(delta_function_call, dict):
                        merge_legacy_function_call(delta_function_call)

                    message_content = str(message.get("content") or "")
                    if message_content and not accumulated_text:
                        accumulated_text = message_content
                        yield "partial", sanitize_generated_text(accumulated_text, aggressive=False)

                    for raw_call_index, raw_call in enumerate(_tool_call_items(message)):
                        merge_tool_call(raw_call, raw_call_index, replace=True, source="message.tool_calls")

                    message_function_call = _function_call_item(message)
                    if isinstance(message_function_call, dict):
                        merge_legacy_function_call(message_function_call, replace=True)
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
    _tool_debug(
        "normalized_tool_calls",
        count=len(normalized_tool_calls),
        calls=[
            {
                "id": item.get("id"),
                "name": item.get("function", {}).get("name"),
                "arguments_type": type(item.get("function", {}).get("arguments")).__name__,
                "arguments_json_length": len(str(item.get("function", {}).get("arguments_json") or "")),
            }
            for item in normalized_tool_calls
        ],
        text_length=len(cleaned),
        reasoning_length=len(accumulated_reasoning.strip()),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
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
