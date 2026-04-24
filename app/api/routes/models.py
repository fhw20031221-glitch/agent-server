from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import LlmModel, User
from app.db.session import get_db
from app.schemas.models import PublicModelRead
from app.services import model_service

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[PublicModelRead])
def list_models(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PublicModelRead]:
    del current_user
    rows = model_service.list_public_models(db)
    result = []
    for row in rows:
        result.append(
            PublicModelRead(
                model_key=row.model_key,
                display_name=row.display_name,
                provider=row.provider,
                max_tokens=row.max_tokens,
                is_default=bool(row.is_default) if isinstance(row, LlmModel) else True,
            )
        )
    return result
