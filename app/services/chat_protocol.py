from __future__ import annotations

import re
from typing import Any

from app.schemas.chat import ChatMode, EditPlan, ExecutionSummary, NextAction
from app.services.edit_blocks import extract_non_block_text, parse_edit_blocks

NO_CHANGES_RE = re.compile(r"(?im)^\s*\*{0,2}NO_CHANGES\*{0,2}\s*[:：-]?\s*")


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
    cleaned = NO_CHANGES_RE.sub("", cleaned)
    cleaned = re.sub(r"(?im)^\s*CONTEXT_REQUEST\s*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(" \n:")


def has_no_changes_marker(text: str) -> bool:
    return bool(NO_CHANGES_RE.search(str(text or "")))


def _unique_nonempty(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = str(item or "").strip().strip("`")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _parse_context_request(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not re.search(r"(?im)^\s*CONTEXT_REQUEST\s*$", text):
        return None

    reason = ""
    queries: list[str] = []
    paths: list[str] = []
    current_section = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"(?i)CONTEXT_REQUEST", line):
            continue

        lowered = line.lower()
        if lowered.startswith("reason:"):
            reason = line.split(":", 1)[1].strip()
            current_section = ""
            continue
        if lowered in {"queries:", "query:"}:
            current_section = "queries"
            continue
        if lowered in {"paths:", "path:"}:
            current_section = "paths"
            continue

        if line.startswith("-"):
            value = line[1:].strip()
            if current_section == "queries":
                queries.append(value)
            elif current_section == "paths":
                paths.append(value)
            continue

        if current_section == "queries":
            queries.append(line)
        elif current_section == "paths":
            paths.append(line)
        elif not reason:
            reason = line

    queries = _unique_nonempty(queries)
    paths = _unique_nonempty(paths)
    explanation = reason or "需要补充更多上下文后才能安全生成修改。"
    return EditPlan(
        status="needs_context",
        explanation=explanation,
        context_queries=queries,
        context_paths=paths,
    ).model_dump()


def _looks_like_malformed_edit(raw_text: str) -> bool:
    text = str(raw_text or "")
    markers = ["<<<<<<< SEARCH", ">>>>>>> REPLACE", "\n=======", "diff --git", "```diff"]
    return any(marker in text for marker in markers)


def build_edit_plan(raw_text: str) -> dict[str, Any]:
    edits = parse_edit_blocks(raw_text)
    explanation = _normalize_explanation(extract_non_block_text(raw_text))

    if edits:
        return EditPlan(
            status="ready",
            explanation=explanation or "已生成可预览的修改草案。",
            edits=edits,
        ).model_dump()

    context_request = _parse_context_request(raw_text)
    if context_request:
        return context_request

    normalized = str(raw_text or "").strip()
    if has_no_changes_marker(normalized):
        return EditPlan(
            status="none",
            explanation=explanation or "没有需要修改的内容。",
        ).model_dump()

    if not normalized:
        return EditPlan(
            status="invalid",
            explanation="模型未返回任何可解析的修改结果。",
        ).model_dump()

    if not _looks_like_malformed_edit(normalized):
        return EditPlan(
            status="none",
            explanation=explanation or normalized,
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
    if edit_plan.get("status") == "needs_context":
        return explanation or "需要补充更多上下文后才能安全生成修改。"
    if edit_plan.get("status") == "none":
        return explanation or "没有需要修改的内容。"
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
    if status == "needs_context":
        return [
            NextAction(type="add_context", label="补充上下文").model_dump(),
            NextAction(type="retry", label="重试生成").model_dump(),
        ]
    if status == "none":
        return []

    return [
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
        elif status == "needs_context":
            headline = "需要补充上下文后继续生成"
        elif status == "none":
            headline = "未生成代码修改"
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
        "edit_paths": _unique_nonempty([str(item.get("path") or "") for item in edit_plan.get("edits", [])]),
        "context_queries": _unique_nonempty([str(item) for item in edit_plan.get("context_queries", [])]),
        "context_paths": _unique_nonempty([str(item) for item in edit_plan.get("context_paths", [])]),
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
