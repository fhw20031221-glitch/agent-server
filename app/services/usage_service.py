from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.security import month_key
from app.db.models import UsageEvent, User
from app.services import quota_service


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> Decimal:
    total = prompt_tokens + completion_tokens
    return Decimal(total) / Decimal(1_000_000)


def record_usage(
    db: Session,
    *,
    user: User,
    session_id: str | None,
    request_id: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> int:
    event = UsageEvent(
        user_id=user.id,
        session_id=session_id,
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        estimated_cost=estimate_cost(prompt_tokens, completion_tokens),
        request_id=request_id,
        quota_month=month_key(),
    )
    db.add(event)
    db.flush()
    return quota_service.get_remaining_tokens(db, user)
