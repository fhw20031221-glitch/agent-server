from __future__ import annotations

from typing import Any

from app.schemas.chat import ChatMode, ExecutionSummary


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


def build_execution_summary(
    *,
    mode: ChatMode,
    steps: list[dict[str, str]],
    edit_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_calls_pending = bool((edit_plan or {}).get("status") == "needs_context")
    if tool_calls_pending:
        headline = "等待本地工具执行"
    elif mode == "code":
        headline = "已完成 Agent 代码任务"
    else:
        headline = "已完成 Agent 回答"

    return ExecutionSummary(headline=headline, steps=list(steps)).model_dump()
