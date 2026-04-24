from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import User


def ensure_initial_admin(db: Session) -> User | None:
    username = settings.admin_username.strip()
    password = settings.admin_password
    if not username or not password:
        return None

    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(
            username=username,
            password_hash=hash_password(password),
            email=settings.admin_email.strip() or None,
            role="admin",
            status="active",
            monthly_token_limit=settings.default_monthly_token_limit,
        )
        db.add(user)
        db.flush()
        return user

    user.role = "admin"
    user.status = "active"
    if settings.admin_email.strip():
        user.email = settings.admin_email.strip()
    db.add(user)
    db.flush()
    return user
