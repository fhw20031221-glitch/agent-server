from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import month_key
from app.db.models import UsageEvent, User


def get_used_tokens(db: Session, user_id: str, quota_month: str | None = None) -> int:
    target_month = quota_month or month_key()
    total = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.total_tokens), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.quota_month == target_month,
        )
    )
    return int(total or 0)


def get_remaining_tokens(db: Session, user: User) -> int:
    used = get_used_tokens(db, user.id)
    return max(0, int(user.monthly_token_limit) - used)
