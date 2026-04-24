from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import QuotaRead
from app.services import quota_service

router = APIRouter(prefix="/quota", tags=["quota"])


@router.get("/me", response_model=QuotaRead)
def get_my_quota(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> QuotaRead:
    used_tokens = quota_service.get_used_tokens(db, current_user.id)
    adjustment_tokens = quota_service.get_adjustment_tokens(db, current_user.id)
    remaining_tokens = quota_service.get_remaining_tokens(db, current_user)
    return QuotaRead(
        monthly_token_limit=current_user.monthly_token_limit,
        used_tokens=used_tokens,
        adjustment_tokens=adjustment_tokens,
        remaining_tokens=remaining_tokens,
    )
