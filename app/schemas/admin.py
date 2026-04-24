from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AdminUserRead(BaseModel):
    id: str
    username: str
    email: str | None
    role: str
    status: str
    monthly_token_limit: int
    used_tokens: int
    adjustment_tokens: int
    remaining_tokens: int
    created_at: datetime
    updated_at: datetime


class SetMonthlyTokenLimitRequest(BaseModel):
    monthly_token_limit: int = Field(ge=0)


class SetRemainingQuotaRequest(BaseModel):
    remaining_tokens: int = Field(ge=0)
    reason: str = Field(default="", max_length=255)


class QuotaAdjustmentCreateRequest(BaseModel):
    delta_tokens: int
    reason: str = Field(default="", max_length=255)


class UserStatusUpdateRequest(BaseModel):
    status: Literal["active", "disabled"]
