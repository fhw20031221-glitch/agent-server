from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user
from app.db.models import LlmModel, User
from app.db.session import get_db
from app.schemas.admin import (
    AdminUserRead,
    QuotaAdjustmentCreateRequest,
    SetMonthlyTokenLimitRequest,
    SetRemainingQuotaRequest,
    UserStatusUpdateRequest,
)
from app.schemas.models import (
    LlmModelCreate,
    LlmModelRead,
    LlmModelSyncRequest,
    LlmModelSyncResult,
    LlmModelUpdate,
)
from app.services import model_service, quota_service

router = APIRouter(prefix="/admin", tags=["admin"])


def _get_user_or_404(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return user


def _build_admin_user_read(db: Session, user: User) -> AdminUserRead:
    used_tokens = quota_service.get_used_tokens(db, user.id)
    adjustment_tokens = quota_service.get_adjustment_tokens(db, user.id)
    remaining_tokens = quota_service.get_remaining_tokens(db, user)
    return AdminUserRead(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        status=user.status,
        monthly_token_limit=user.monthly_token_limit,
        used_tokens=used_tokens,
        adjustment_tokens=adjustment_tokens,
        remaining_tokens=remaining_tokens,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("/users", response_model=list[AdminUserRead])
def list_users(
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> list[AdminUserRead]:
    del current_admin
    users = list(db.scalars(select(User).order_by(User.created_at.desc())))
    return [_build_admin_user_read(db, user) for user in users]


@router.get("/users/{user_id}", response_model=AdminUserRead)
def get_user(
    user_id: str,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> AdminUserRead:
    del current_admin
    return _build_admin_user_read(db, _get_user_or_404(db, user_id))


@router.patch("/users/{user_id}/quota-limit", response_model=AdminUserRead)
def set_quota_limit(
    user_id: str,
    payload: SetMonthlyTokenLimitRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> AdminUserRead:
    del current_admin
    user = _get_user_or_404(db, user_id)
    user.monthly_token_limit = payload.monthly_token_limit
    db.add(user)
    db.commit()
    db.refresh(user)
    return _build_admin_user_read(db, user)


@router.patch("/users/{user_id}/remaining-quota", response_model=AdminUserRead)
def set_remaining_quota(
    user_id: str,
    payload: SetRemainingQuotaRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> AdminUserRead:
    user = _get_user_or_404(db, user_id)
    quota_service.set_remaining_tokens(
        db,
        user=user,
        admin_user=current_admin,
        remaining_tokens=payload.remaining_tokens,
        reason=payload.reason,
    )
    db.commit()
    db.refresh(user)
    return _build_admin_user_read(db, user)


@router.post("/users/{user_id}/quota-adjustments", response_model=AdminUserRead)
def create_quota_adjustment(
    user_id: str,
    payload: QuotaAdjustmentCreateRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> AdminUserRead:
    if payload.delta_tokens == 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="调整额度不能为 0")
    user = _get_user_or_404(db, user_id)
    quota_service.create_adjustment(
        db,
        user=user,
        admin_user=current_admin,
        delta_tokens=payload.delta_tokens,
        reason=payload.reason,
    )
    db.commit()
    db.refresh(user)
    return _build_admin_user_read(db, user)


@router.patch("/users/{user_id}/status", response_model=AdminUserRead)
def update_user_status(
    user_id: str,
    payload: UserStatusUpdateRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> AdminUserRead:
    user = _get_user_or_404(db, user_id)
    if user.id == current_admin.id and payload.status != "active":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能停用当前管理员账户")
    user.status = payload.status
    db.add(user)
    db.commit()
    db.refresh(user)
    return _build_admin_user_read(db, user)


@router.get("/models", response_model=list[LlmModelRead])
def list_models(
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> list[LlmModelRead]:
    del current_admin
    return [LlmModelRead.model_validate(row) for row in model_service.list_admin_models(db)]


@router.post("/models", response_model=LlmModelRead)
def create_model(
    payload: LlmModelCreate,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> LlmModelRead:
    del current_admin
    if payload.is_default:
        model_service.unset_other_defaults(db)
    row = LlmModel(**payload.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="模型标识已存在") from exc
    db.refresh(row)
    return LlmModelRead.model_validate(row)


@router.post("/models/sync", response_model=LlmModelSyncResult)
def sync_models(
    payload: LlmModelSyncRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> LlmModelSyncResult:
    del current_admin
    api_key = model_service.resolve_api_key(payload.api_key_env, payload.api_key)
    if not api_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模型同步 API Key 未配置")
    try:
        remote_models = model_service.fetch_openai_compatible_models(
            base_url=payload.base_url,
            api_key=api_key,
        )
        result = model_service.sync_remote_models(
            db,
            provider=payload.provider,
            base_url=payload.base_url,
            api_key_env=payload.api_key_env,
            remote_models=remote_models,
            model_key_prefix=payload.model_key_prefix,
            default_model_key=payload.default_model_key,
            max_tokens=payload.max_tokens,
            temperature_default=payload.temperature_default,
            enable_new_models=payload.enable_new_models,
            enable_existing_models=payload.enable_existing_models,
            disable_missing_models=payload.disable_missing_models,
            sort_order_start=payload.sort_order_start,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return LlmModelSyncResult(**result)


@router.patch("/models/{model_id}", response_model=LlmModelRead)
def update_model(
    model_id: str,
    payload: LlmModelUpdate,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> LlmModelRead:
    del current_admin
    row = model_service.get_model_or_404(db, model_id)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("is_default") is True:
        model_service.unset_other_defaults(db, model_id)
    for key, value in updates.items():
        setattr(row, key, value)
    db.add(row)
    db.commit()
    db.refresh(row)
    return LlmModelRead.model_validate(row)


@router.delete("/models/{model_id}")
def delete_model(
    model_id: str,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    del current_admin
    row = model_service.get_model_or_404(db, model_id)
    db.delete(row)
    db.commit()
    return {"status": "ok"}
