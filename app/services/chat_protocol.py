from __future__ import annotations

import re
from typing import Any

from app.schemas.chat import ChatMode, EditPlan, ExecutionSummary, NextAction
from app.services.edit_blocks import extract_non_block_text, parse_edit_blocks


def record_step(
    steps: list[dict[str, str]],
    *,
    phase: str,
    title: str,
    detail: str,
    status: str,
) -> dict[str, str]:
    step = {
        "phase": phase,
        "title": title,
        "detail": detail,
        "status": status,
    }
    steps.append(step)
    return step


def default_edit_plan() -> dict[str, Any]:
    return EditPlan().model_dump()


def _normalize_explanation(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"(?im)^\s*NO_CHANGES\s*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(" \n:")


def build_edit_plan(raw_text: str) -> dict[str, Any]:
    edits = parse_edit_blocks(raw_text)
    explanation = _normalize_explanation(extract_non_block_text(raw_text))

    if edits:
        return EditPlan(
            status="ready",
            explanation=explanation or "已生成可预览的修改草案。",
            edits=edits,
        ).model_dump()

    normalized = str(raw_text or "").strip()
    if re.search(r"(?im)^\s*NO_CHANGES\s*$", normalized):
        return EditPlan(
            status="none",
            explanation=explanation or "当前上下文不足以安全修改，请补充相关文件或更具体的修改目标。",
        ).model_dump()

    if not normalized:
        return EditPlan(
            status="invalid",
            explanation="模型未返回任何可解析的修改结果。",
        ).model_dump()

    return EditPlan(
        status="invalid",
        explanation=explanation or "模型未返回可解析的 SEARCH/REPLACE 编辑块。",
    ).model_dump()


def resolve_response_text(mode: ChatMode, raw_text: str, edit_plan: dict[str, Any]) -> str:
    if mode == "ask":
        return str(raw_text or "").strip() or "模型未返回内容。"
    explanation = str(edit_plan.get("explanation") or "").strip()
    if explanation:
        return explanation
    if edit_plan.get("status") == "ready":
        return "已生成可预览的修改草案。"
    if edit_plan.get("status") == "none":
        return "当前上下文不足以安全修改。"
    return "模型未返回可解析的编辑结果。"


def build_next_actions(mode: ChatMode, edit_plan: dict[str, Any]) -> list[dict[str, str]]:
    if mode == "ask":
        return []

    status = str(edit_plan.get("status") or "none")
    if status == "ready":
        return [
            NextAction(type="review_patch", label="预览补丁").model_dump(),
            NextAction(type="apply_patch", label="应用修改").model_dump(),
        ]

    return [
        NextAction(type="add_context", label="补充上下文").model_dump(),
        NextAction(type="retry", label="重试生成").model_dump(),
    ]


def build_execution_summary(
    *,
    mode: ChatMode,
    steps: list[dict[str, str]],
    edit_plan: dict[str, Any],
) -> dict[str, Any]:
    if mode == "ask":
        headline = "已基于当前上下文生成回答"
    else:
        status = str(edit_plan.get("status") or "none")
        if status == "ready":
            headline = "已生成可预览的修改草案"
        elif status == "none":
            headline = "当前上下文不足以安全生成修改"
        else:
            headline = "模型输出未能解析为可应用修改"

    return ExecutionSummary(headline=headline, steps=list(steps)).model_dump()


def build_message_metadata(
    *,
    mode: ChatMode,
    execution_summary: dict[str, Any],
    next_actions: list[dict[str, Any]],
    edit_plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "mode": mode,
        "execution_summary": execution_summary,
        "next_actions": next_actions,
        "edit_plan_status": str(edit_plan.get("status") or "none"),
        "edit_paths": [str(item.get("path") or "") for item in edit_plan.get("edits", []) if item.get("path")],
    }


def build_result_payload(
    *,
    mode: ChatMode,
    response: str,
    execution_summary: dict[str, Any],
    next_actions: list[dict[str, Any]],
    edit_plan: dict[str, Any],
    stats: dict[str, int],
    quota_remaining_tokens: int,
    session_id: str,
    message_id: str,
) -> dict[str, Any]:
    return {
        "type": "result",
        "mode": mode,
        "response": response,
        "execution_summary": execution_summary,
        "next_actions": next_actions,
        "edit_plan": edit_plan,
        "stats": stats,
        "quota_remaining_tokens": quota_remaining_tokens,
        "session_id": session_id,
        "message_id": message_id,
    }
