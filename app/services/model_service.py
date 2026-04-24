from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import LlmModel


@dataclass(frozen=True)
class RuntimeModel:
    model_key: str
    display_name: str
    provider: str
    base_url: str
    api_key: str
    upstream_model: str
    max_tokens: int
    temperature_default: float


def _settings_model() -> RuntimeModel:
    return RuntimeModel(
        model_key=settings.openai_model,
        display_name=settings.openai_model,
        provider="openai-compatible",
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        upstream_model=settings.openai_model,
        max_tokens=2048,
        temperature_default=0.2,
    )


def _model_order_query(enabled_only: bool = False):
    stmt = select(LlmModel)
    if enabled_only:
        stmt = stmt.where(LlmModel.is_enabled.is_(True))
    return stmt.order_by(LlmModel.is_default.desc(), LlmModel.sort_order.asc(), LlmModel.created_at.asc())


def list_public_models(db: Session) -> list[LlmModel | RuntimeModel]:
    rows = list(db.scalars(_model_order_query(enabled_only=True)))
    if rows:
        return rows
    if settings.openai_model:
        return [_settings_model()]
    return []


def list_admin_models(db: Session) -> list[LlmModel]:
    return list(db.scalars(_model_order_query(enabled_only=False)))


def resolve_model(db: Session, model_key: str | None = None) -> RuntimeModel:
    requested = (model_key or "").strip()
    stmt = select(LlmModel).where(LlmModel.is_enabled.is_(True))
    if requested:
        row = db.scalar(stmt.where(LlmModel.model_key == requested))
    else:
        row = db.scalar(stmt.where(LlmModel.is_default.is_(True)).order_by(LlmModel.sort_order.asc()))
        if row is None:
            row = db.scalar(stmt.order_by(LlmModel.sort_order.asc(), LlmModel.created_at.asc()))

    if row is not None:
        api_key = os.environ.get(row.api_key_env or "") if row.api_key_env else settings.openai_api_key
        if not api_key:
            api_key = settings.openai_api_key
        return RuntimeModel(
            model_key=row.model_key,
            display_name=row.display_name,
            provider=row.provider,
            base_url=row.base_url,
            api_key=api_key,
            upstream_model=row.upstream_model,
            max_tokens=row.max_tokens,
            temperature_default=row.temperature_default,
        )

    fallback = _settings_model()
    if requested and requested != fallback.model_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型不存在或已禁用")
    return fallback


def get_model_or_404(db: Session, model_id: str) -> LlmModel:
    row = db.get(LlmModel, model_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型不存在")
    return row


def unset_other_defaults(db: Session, model_id: str | None = None) -> None:
    stmt = update(LlmModel).values(is_default=False)
    if model_id:
        stmt = stmt.where(LlmModel.id != model_id)
    db.execute(stmt)
