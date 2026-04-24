from __future__ import annotations

import json
import re
from typing import AsyncIterator

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


async def stream_completion(
    *,
    system_prompt: str,
    user_prompt: str,
    model_config: RuntimeModel | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    aggressive_sanitize: bool = True,
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
    payload = {
        "model": runtime_model.upstream_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max(1, int(max_tokens or runtime_model.max_tokens)),
        "temperature": float(temperature if temperature is not None else runtime_model.temperature_default),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    headers = {
        "Authorization": f"Bearer {runtime_model.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    accumulated_text = ""
    prompt_tokens = 0
    completion_tokens = 0

    try:
        async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
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
                    piece = str(delta.get("content") or "")
                    if not piece:
                        continue
                    accumulated_text += piece
                    yield "partial", sanitize_generated_text(accumulated_text, aggressive=aggressive_sanitize)
    except httpx.ReadTimeout as exc:
        raise RuntimeError(
            f"调用模型超时（{settings.openai_timeout_seconds} 秒）。请检查当前网络、代理设置，或稍后重试。"
        ) from exc
    except httpx.ConnectError as exc:
        raise RuntimeError("无法连接模型服务，请检查网络或服务端出网能力。") from exc
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.text.strip()
        except Exception:
            detail = ""
        suffix = f": {detail[:300]}" if detail else ""
        raise RuntimeError(f"模型服务返回 HTTP {exc.response.status_code}{suffix}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"模型请求失败: {exc}") from exc

    cleaned = sanitize_generated_text(accumulated_text, aggressive=aggressive_sanitize)
    if completion_tokens <= 0 and cleaned:
        completion_tokens = max(1, len(cleaned) // 2)
    yield "usage", {
        "text": cleaned,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "model": runtime_model.model_key,
        "provider": runtime_model.provider,
    }
