from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.db.models import ChatMessage, ChatSession, User


def create_session(db: Session, user: User, title: str, project_name: str | None = None) -> ChatSession:
    session = ChatSession(user_id=user.id, title=title.strip() or "新对话", project_name=project_name or None)
    db.add(session)
    db.flush()
    return session


def list_sessions(db: Session, user: User) -> list[ChatSession]:
    return list(
        db.scalars(
            select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc())
        )
    )


def get_session_for_user(db: Session, user: User, session_id: str) -> ChatSession:
    session = db.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user.id))
    if session is None:
        raise ValueError("会话不存在")
    return session


def create_message(
    db: Session,
    session: ChatSession,
    *,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> ChatMessage:
    message = ChatMessage(
        session_id=session.id,
        role=role,
        content=content,
        meta=dict(metadata or {}),
    )
    db.add(message)
    session.updated_at = utcnow()
    db.add(session)
    db.flush()
    return message


def list_messages(db: Session, session: ChatSession) -> list[ChatMessage]:
    return list(
        db.scalars(select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at.asc()))
    )


def list_recent_messages_payload(db: Session, session: ChatSession, limit: int = 8) -> list[dict]:
    rows = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    return [{"role": row.role, "content": row.content} for row in rows]
