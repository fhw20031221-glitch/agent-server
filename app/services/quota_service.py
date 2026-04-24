from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import month_key
from app.db.models import QuotaAdjustment, UsageEvent, User


def get_used_tokens(db: Session, user_id: str, quota_month: str | None = None) -> int:
    target_month = quota_month or month_key()
    total = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.total_tokens), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.quota_month == target_month,
        )
    )
    return int(total or 0)


def get_adjustment_tokens(db: Session, user_id: str, quota_month: str | None = None) -> int:
    target_month = quota_month or month_key()
    total = db.scalar(
        select(func.coalesce(func.sum(QuotaAdjustment.delta_tokens), 0)).where(
            QuotaAdjustment.user_id == user_id,
            QuotaAdjustment.quota_month == target_month,
        )
    )
    return int(total or 0)


def get_remaining_tokens(db: Session, user: User) -> int:
    return max(0, get_token_balance(db, user))


def get_token_balance(db: Session, user: User) -> int:
    used = get_used_tokens(db, user.id)
    adjusted = get_adjustment_tokens(db, user.id)
    return int(user.monthly_token_limit) + adjusted - used


def create_adjustment(
    db: Session,
    *,
    user: User,
    admin_user: User | None,
    delta_tokens: int,
    reason: str = "",
    quota_month: str | None = None,
) -> QuotaAdjustment:
    adjustment = QuotaAdjustment(
        user_id=user.id,
        admin_user_id=admin_user.id if admin_user else None,
        quota_month=quota_month or month_key(),
        delta_tokens=int(delta_tokens),
        reason=(reason or "").strip()[:255],
    )
    db.add(adjustment)
    db.flush()
    return adjustment


def set_remaining_tokens(
    db: Session,
    *,
    user: User,
    admin_user: User,
    remaining_tokens: int,
    reason: str = "",
    quota_month: str | None = None,
) -> QuotaAdjustment | None:
    current_balance = get_token_balance(db, user)
    delta = int(remaining_tokens) - current_balance
    if delta == 0:
        return None
    return create_adjustment(
        db,
        user=user,
        admin_user=admin_user,
        delta_tokens=delta,
        reason=reason or f"设置剩余额度为 {remaining_tokens}",
        quota_month=quota_month,
    )
