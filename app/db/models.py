from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.security import utcnow


def generate_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="user")
    status: Mapped[str] = mapped_column(String(20), default="active")
    monthly_token_limit: Mapped[int] = mapped_column(Integer, default=1_000_000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    sessions: Mapped[list["ChatSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    quota_adjustments: Mapped[list["QuotaAdjustment"]] = relationship(
        foreign_keys="QuotaAdjustment.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(120))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    request_id: Mapped[str] = mapped_column(String(80))
    quota_month: Mapped[str] = mapped_column(String(7), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QuotaAdjustment(Base):
    __tablename__ = "quota_adjustments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    admin_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    quota_month: Mapped[str] = mapped_column(String(7), index=True)
    delta_tokens: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(foreign_keys=[user_id], back_populates="quota_adjustments")
    admin_user: Mapped[User | None] = relationship(foreign_keys=[admin_user_id])


class LlmModel(Base):
    __tablename__ = "llm_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    model_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(50), default="openai-compatible")
    base_url: Mapped[str] = mapped_column(String(500))
    api_key_env: Mapped[str] = mapped_column(String(120), default="AGENT_SERVER_OPENAI_API_KEY")
    upstream_model: Mapped[str] = mapped_column(String(120))
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)
    temperature_default: Mapped[float] = mapped_column(Float, default=0.2)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
