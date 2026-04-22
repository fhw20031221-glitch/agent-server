from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import AuthResponse, LoginRequest, LogoutRequest, RefreshRequest, RegisterRequest, UserRead
from app.services import auth_service, quota_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user = auth_service.create_user(db, payload.username, payload.password, payload.email)
    access_token, refresh_token = auth_service.issue_tokens(db, user)
    db.commit()
    db.refresh(user)
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserRead.model_validate(user),
        quota_remaining_tokens=quota_service.get_remaining_tokens(db, user),
    )


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user = auth_service.authenticate_user(db, payload.username, payload.password)
    access_token, refresh_token = auth_service.issue_tokens(db, user)
    db.commit()
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserRead.model_validate(user),
        quota_remaining_tokens=quota_service.get_remaining_tokens(db, user),
    )


@router.post("/refresh", response_model=AuthResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user, access_token, refresh_token = auth_service.rotate_refresh_token(db, payload.refresh_token)
    db.commit()
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserRead.model_validate(user),
        quota_remaining_tokens=quota_service.get_remaining_tokens(db, user),
    )


@router.post("/logout")
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    auth_service.revoke_refresh_token(db, payload.refresh_token)
    db.commit()
    return {"status": "ok"}


@router.get("/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)
