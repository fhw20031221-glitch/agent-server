from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import session_scope
from app.schemas.chat import AgentTurnRequest
from app.services import chat_protocol, chat_service, llm_service, model_service, quota_service, usage_service
from app.utils.sse import sse_event

router = APIRouter(prefix="/agent", tags=["agent"])


def _truncate(value: Any, limit: int = 800) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _extract_tool_trace(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for item in messages:
        role = str(item.get("role") or "")
        if role == "assistant":
            for raw_call in item.get("tool_calls") or []:
                if not isinstance(raw_call, dict):
                    continue
                function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
                trace.append(
                    {
                        "type": "tool_call",
                        "id": str(raw_call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": _truncate(function.get("arguments") or ""),
                    }
                )
        elif role == "tool":
            trace.append(
                {
                    "type": "tool_result",
                    "id": str(item.get("tool_call_id") or ""),
                    "content": _truncate(item.get("content") or ""),
                }
            )
    return trace[-20:]


def _message_dump(messages: list[Any]) -> list[dict[str, Any]]:
    dumped: list[dict[str, Any]] = []
    for item in messages:
        payload = item.model_dump(exclude_none=True)
        if payload.get("content") is None and payload.get("role") != "assistant":
            payload["content"] = ""
        dumped.append(payload)
    return dumped


@router.post("/turn/stream")
async def agent_turn_stream(
    payload: AgentTurnRequest,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    request_id = uuid4().hex[:12]

    with session_scope() as db:
        try:
            session = chat_service.get_session_for_user(db, current_user, payload.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        remaining_tokens = quota_service.get_remaining_tokens(db, current_user)
        if remaining_tokens <= 0:
            raise HTTPException(status_code=402, detail="本月 Token 额度已用尽")
        selected_model = model_service.resolve_model(db, payload.model_key)

        if payload.persist_user_message and payload.question.strip():
            chat_service.create_message(
                db,
                session,
                role="user",
                content=payload.question,
                metadata={"mode": payload.mode, "agent": True},
            )

    messages = _message_dump(payload.messages)
    tools = [item.model_dump(exclude_none=True) for item in payload.tools]
    tool_trace = _extract_tool_trace(messages)

    async def event_stream():
        final_text = ""
        reasoning_content = ""
        tool_calls: list[dict[str, Any]] = []
        prompt_tokens = 0
        completion_tokens = 0
        provider = "openai-compatible"
        model = ""
        execution_steps: list[dict[str, str]] = []

        def emit_working(*, phase: str, title: str, detail: str, status: str) -> str:
            step = chat_protocol.record_step(
                execution_steps,
                phase=phase,
                title=title,
                detail=detail,
                status=status,
            )
            return sse_event({"type": "working", "payload": step})

        yield emit_working(
            phase="agent_prompt",
            title="准备 Agent Turn",
            detail=f"已提供 {len(tools)} 个工作区工具",
            status="done",
        )
        yield emit_working(
            phase="model_call",
            title="调用工具模型",
            detail=f"正在调用 {selected_model.display_name}",
            status="running",
        )

        try:
            async for kind, item in llm_service.stream_agent_turn(
                messages=messages,
                tools=tools,
                model_config=selected_model,
                max_tokens=min(4096, selected_model.max_tokens),
                temperature=selected_model.temperature_default,
                tool_choice="auto",
            ):
                if kind == "partial":
                    final_text = str(item)
                    yield sse_event({"type": "partial", "text": final_text})
                elif kind == "reasoning":
                    reasoning_content = str(item)
                    yield sse_event({"type": "reasoning", "text": reasoning_content, "collapsed": True})
                elif kind == "tool_call":
                    tool_call = dict(item)
                    tool_calls.append(tool_call)
                    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                    yield sse_event({"type": "tool_call", "tool_call": tool_call})
                    yield emit_working(
                        phase="tool_call",
                        title=f"请求工具 {function.get('name') or 'unknown'}",
                        detail=_truncate(function.get("arguments_json") or function.get("arguments") or "", 240),
                        status="done",
                    )
                elif kind == "usage":
                    usage = dict(item)
                    final_text = str(usage.get("text") or final_text or "")
                    reasoning_content = str(usage.get("reasoning_content") or reasoning_content or "")
                    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
                    model = str(usage.get("model") or "")
                    provider = str(usage.get("provider") or provider)
                    tool_calls = list(usage.get("tool_calls") or tool_calls)
        except Exception as exc:
            yield emit_working(
                phase="model_call",
                title="调用工具模型",
                detail="远程模型调用失败",
                status="error",
            )
            yield sse_event({"type": "error", "message": str(exc).strip() or "模型请求失败"})
            yield "data: [DONE]\n\n"
            return

        yield emit_working(
            phase="model_call",
            title="调用工具模型",
            detail="远程模型已返回结果",
            status="done",
        )

        stats = {
            "input_tokens": prompt_tokens,
            "generated_tokens": completion_tokens,
            "elapsed": 0,
            "tool_call_count": len(tool_calls),
            "search_count": 0,
            "read_count": 0,
            "snippet_count": 0,
            "cache_hits": 0,
        }
        message_id = ""
        execution_summary = chat_protocol.build_execution_summary(
            mode=payload.mode,
            steps=execution_steps,
            edit_plan={"status": "none" if not tool_calls else "needs_context"},
        )
        remaining = 0

        with session_scope() as db:
            user = db.get(User, current_user.id)
            if user is None:
                yield sse_event({"type": "error", "message": "用户不存在"})
                yield "data: [DONE]\n\n"
                return
            try:
                session = chat_service.get_session_for_user(db, user, payload.session_id)
            except ValueError:
                yield sse_event({"type": "error", "message": "会话不存在"})
                yield "data: [DONE]\n\n"
                return

            if not tool_calls:
                metadata = {
                    "mode": payload.mode,
                    "agent": True,
                    "execution_summary": execution_summary,
                    "next_actions": [],
                    "tool_trace": tool_trace,
                    "stats": stats,
                }
                if reasoning_content.strip():
                    metadata["reasoning"] = {"content": reasoning_content.strip(), "collapsed": True}
                assistant_message = chat_service.create_message(
                    db,
                    session,
                    role="assistant",
                    content=final_text.strip() or "模型未返回内容。",
                    metadata=metadata,
                )
                message_id = assistant_message.id

            remaining = usage_service.record_usage(
                db,
                user=user,
                session_id=session.id,
                request_id=request_id,
                provider=provider,
                model=model or "unknown",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        result_payload: dict[str, Any] = {
            "type": "result",
            "mode": payload.mode,
            "response": final_text.strip(),
            "tool_calls": tool_calls,
            "execution_summary": execution_summary,
            "next_actions": [],
            "stats": stats,
            "quota_remaining_tokens": remaining,
            "session_id": payload.session_id,
            "message_id": message_id,
        }
        if reasoning_content.strip():
            result_payload["reasoning"] = {"content": reasoning_content.strip(), "collapsed": True}
        yield sse_event(result_payload)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
