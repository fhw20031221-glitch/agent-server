from __future__ import annotations
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db, session_scope
from app.schemas.chat import AskRequest, ChatMessageRead, ChatSessionCreateRequest, ChatSessionRead
from app.services import (
    chat_protocol,
    chat_service,
    llm_service,
    model_service,
    prompt_service,
    quota_service,
    usage_service,
)
from app.utils.sse import sse_event

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/sessions", response_model=ChatSessionRead)
def create_session(
    payload: ChatSessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatSessionRead:
    session = chat_service.create_session(db, current_user, payload.title, payload.project_name)
    db.commit()
    db.refresh(session)
    return ChatSessionRead.model_validate(session)


@router.get("/sessions", response_model=list[ChatSessionRead])
def list_sessions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ChatSessionRead]:
    return [ChatSessionRead.model_validate(item) for item in chat_service.list_sessions(db, current_user)]


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageRead])
def list_messages(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ChatMessageRead]:
    try:
        session = chat_service.get_session_for_user(db, current_user, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    rows = chat_service.list_messages(db, session)
    return [
        ChatMessageRead(
            id=row.id,
            session_id=row.session_id,
            role=row.role,
            content=row.content,
            metadata=row.meta,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/sessions/{session_id}/ask/stream")
async def ask_stream(
    session_id: str,
    payload: AskRequest,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    request_id = uuid4().hex[:12]

    with session_scope() as db:
        try:
            session = chat_service.get_session_for_user(db, current_user, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        remaining_tokens = quota_service.get_remaining_tokens(db, current_user)
        if remaining_tokens <= 0:
            raise HTTPException(status_code=402, detail="本月 Token 额度已用尽")
        selected_model = model_service.resolve_model(db, payload.model_key)

        recent_messages = chat_service.list_recent_messages_payload(db, session, limit=8)
        if not (payload.mode == "code" and payload.context_retry):
            chat_service.create_message(
                db,
                session,
                role="user",
                content=payload.question,
                metadata={"mode": payload.mode},
            )

    selected_files = [item.path for item in payload.snippets]
    prompt_kwargs = {
        "question": payload.question,
        "project_name": payload.project_name or "",
        "project_root": "客户端本地工作区",
        "active_file": payload.active_file,
        "chat_files": payload.chat_files,
        "selected_files": selected_files,
        "repo_map_text": payload.repo_map_text,
        "recent_messages": recent_messages,
        "snippets": [item.model_dump() for item in payload.snippets],
    }

    if payload.mode == "code":
        system_prompt = prompt_service.CODE_SYSTEM_PROMPT
        user_prompt = prompt_service.build_code_user_prompt(**prompt_kwargs)
        max_tokens = 2048
    else:
        system_prompt = prompt_service.ASK_SYSTEM_PROMPT
        user_prompt = prompt_service.build_ask_user_prompt(**prompt_kwargs)
        max_tokens = 1024

    async def event_stream():
        final_text = ""
        reasoning_content = ""
        prompt_tokens = 0
        completion_tokens = 0
        provider = "openai-compatible"
        model = ""
        execution_steps: list[dict[str, str]] = []

        def emit_activity(*, phase: str, title: str, detail: str, status: str) -> str:
            payload_step = chat_protocol.record_step(
                execution_steps,
                phase=phase,
                title=title,
                detail=detail,
                status=status,
            )
            return sse_event({"type": "activity", "payload": payload_step})

        yield emit_activity(
            phase="build_prompt",
            title="构建提示词",
            detail="服务端正在组织 repo-map、上下文片段与最近对话",
            status="done",
        )
        yield emit_activity(
            phase="model_call",
            title="调用远程模型",
            detail=f"正在调用 {selected_model.display_name}",
            status="running",
        )

        try:
            async for kind, item in llm_service.stream_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model_config=selected_model,
                max_tokens=min(max_tokens, selected_model.max_tokens),
                temperature=selected_model.temperature_default,
                aggressive_sanitize=payload.mode == "ask",
            ):
                if kind == "partial":
                    final_text = str(item)
                    if payload.mode == "ask":
                        yield sse_event({"type": "partial", "text": final_text})
                elif kind == "reasoning":
                    reasoning_content = str(item)
                    yield sse_event(
                        {
                            "type": "reasoning",
                            "text": reasoning_content,
                            "collapsed": True,
                        }
                    )
                elif kind == "usage":
                    usage = dict(item)
                    final_text = str(usage.get("text") or final_text or "")
                    reasoning_content = str(usage.get("reasoning_content") or reasoning_content or "")
                    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
                    model = str(usage.get("model") or "")
                    provider = str(usage.get("provider") or provider)
        except Exception as exc:
            yield emit_activity(
                phase="model_call",
                title="调用远程模型",
                detail="远程模型调用失败",
                status="error",
            )
            error_message = str(exc).strip() or "模型请求失败，请检查网络、服务端配置或稍后重试。"
            yield sse_event({"type": "error", "message": error_message})
            yield "data: [DONE]\n\n"
            return

        yield emit_activity(
            phase="model_call",
            title="调用远程模型",
            detail="远程模型已返回结果",
            status="done",
        )

        edit_plan = chat_protocol.default_edit_plan()
        if payload.mode == "code":
            yield emit_activity(
                phase="interpret_response",
                title="处理模型输出",
                detail="正在识别普通回复、上下文请求或 SEARCH/REPLACE 编辑块",
                status="running",
            )
            edit_plan = chat_protocol.build_edit_plan(final_text)
            edit_status = str(edit_plan.get("status") or "invalid")
            has_no_changes_marker = chat_protocol.has_no_changes_marker(final_text)
            if edit_status == "ready":
                parse_phase = "parse_edits"
                parse_title = "解析编辑块"
                parse_detail = f"已解析出 {len(edit_plan.get('edits', []))} 个可预览编辑块"
            elif edit_status == "needs_context":
                parse_phase = "context_request"
                parse_title = "请求补充上下文"
                parse_detail = str(edit_plan.get("explanation") or "需要补充更多上下文后继续生成")
            elif edit_status == "none" and not has_no_changes_marker:
                parse_phase = "interpret_response"
                parse_title = "识别普通回复"
                parse_detail = "模型返回普通回复，未生成代码修改"
            elif edit_status == "none":
                parse_phase = "interpret_response"
                parse_title = "无需修改"
                parse_detail = str(edit_plan.get("explanation") or "模型判断不需要修改")
            else:
                parse_phase = "parse_edits"
                parse_title = "解析编辑块"
                parse_detail = str(edit_plan.get("explanation") or "模型输出无法解析为有效编辑块")
            parse_status = "done" if str(edit_plan.get("status") or "invalid") != "invalid" else "error"
            yield emit_activity(
                phase=parse_phase,
                title=parse_title,
                detail=parse_detail,
                status=parse_status,
            )

        response_text = chat_protocol.resolve_response_text(payload.mode, final_text, edit_plan)
        execution_summary = chat_protocol.build_execution_summary(
            mode=payload.mode,
            steps=execution_steps,
            edit_plan=edit_plan,
        )
        next_actions = chat_protocol.build_next_actions(payload.mode, edit_plan)
        message_metadata = chat_protocol.build_message_metadata(
            mode=payload.mode,
            execution_summary=execution_summary,
            next_actions=next_actions,
            edit_plan=edit_plan,
            reasoning_content=reasoning_content,
        )
        stats = {
            "input_tokens": prompt_tokens,
            "generated_tokens": completion_tokens,
            "elapsed": 0,
            "search_count": 0,
            "read_count": len(payload.snippets),
            "snippet_count": len(payload.snippets),
            "cache_hits": 0,
        }

        message_id = ""
        should_persist_assistant = str(edit_plan.get("status") or "") != "needs_context"
        with session_scope() as db:
            user = db.get(User, current_user.id)
            if user is None:
                yield sse_event({"type": "error", "message": "用户不存在"})
                yield "data: [DONE]\n\n"
                return
            try:
                session = chat_service.get_session_for_user(db, user, session_id)
            except ValueError:
                yield sse_event({"type": "error", "message": "会话不存在"})
                yield "data: [DONE]\n\n"
                return

            if should_persist_assistant:
                assistant_message = chat_service.create_message(
                    db,
                    session,
                    role="assistant",
                    content=response_text,
                    metadata=message_metadata,
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

        yield sse_event(
            chat_protocol.build_result_payload(
                mode=payload.mode,
                response=response_text,
                execution_summary=execution_summary,
                next_actions=next_actions,
                edit_plan=edit_plan,
                stats=stats,
                quota_remaining_tokens=remaining,
                session_id=session_id,
                message_id=message_id,
                reasoning_content=reasoning_content,
            )
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
