from __future__ import annotations

import json
import re
from typing import AsyncIterator

import httpx

from app.core.config import settings


def sanitize_generated_text(text: str) -> str:
    cleaned = (text or "").strip()
    for marker in [
        "<|endoftext|>",
        "<|im_end|>",
        "<|im_start|>user",
        "<|im_start|>assistant",
        "\nuser\n",
        "\nassistant\n",
        "\nHuman:",
        "\nAssistant:",
        "\nUser:",
        "Human:",
        "Assistant:",
        "User:",
    ]:
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()
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
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> AsyncIterator[tuple[str, dict | str]]:
    if not settings.openai_api_key:
        raise RuntimeError("服务端未配置 OpenAI-compatible API Key")

    url = resolve_chat_completions_url(settings.openai_base_url)
    payload = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max(1, int(max_tokens)),
        "temperature": float(temperature),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    accumulated_text = ""
    prompt_tokens = 0
    completion_tokens = 0

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
                yield "partial", sanitize_generated_text(accumulated_text)

    cleaned = sanitize_generated_text(accumulated_text)
    if completion_tokens <= 0 and cleaned:
        completion_tokens = max(1, len(cleaned) // 2)
    yield "usage", {
        "text": cleaned,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "model": settings.openai_model,
        "provider": "openai-compatible",
    }
