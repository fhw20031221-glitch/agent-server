from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    ensure_utc,
    hash_password,
    hash_refresh_token,
    utcnow,
    verify_password,
)
from app.db.models import RefreshToken, User


def create_user(db: Session, username: str, password: str, email: str | None = None) -> User:
    existing = db.scalar(select(User).where(User.username == username))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")

    user = User(
        username=username,
        password_hash=hash_password(password),
        email=email,
        monthly_token_limit=settings.default_monthly_token_limit,
    )
    db.add(user)
    db.flush()
    return user


def authenticate_user(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已停用")
    return user


def issue_tokens(db: Session, user: User) -> tuple[str, str]:
    access_token = create_access_token(user.id, user.username)
    refresh_token = create_refresh_token()
    refresh_record = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(refresh_token),
        expires_at=utcnow() + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(refresh_record)
    db.flush()
    return access_token, refresh_token


def revoke_refresh_token(db: Session, raw_refresh_token: str) -> None:
    hashed = hash_refresh_token(raw_refresh_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hashed))
    if record is None:
        return
    record.revoked_at = utcnow()
    db.add(record)


def rotate_refresh_token(db: Session, raw_refresh_token: str) -> tuple[User, str, str]:
    hashed = hash_refresh_token(raw_refresh_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hashed))
    expires_at = ensure_utc(record.expires_at) if record is not None else None
    revoked_at = ensure_utc(record.revoked_at) if record is not None else None
    if record is None or revoked_at is not None or expires_at is None or expires_at <= utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token 无效或已过期")

    user = db.get(User, record.user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")

    record.revoked_at = utcnow()
    record.last_used_at = utcnow()
    db.add(record)
    access_token, refresh_token = issue_tokens(db, user)
    return user, access_token, refresh_token
