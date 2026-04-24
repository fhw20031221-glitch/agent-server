from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.chat import ChatMessageRead, ChatSessionCreateRequest, ChatSessionRead
from app.services import chat_service

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
