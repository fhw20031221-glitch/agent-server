from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str | None = None
    role: str
    status: str
    monthly_token_limit: int
    created_at: datetime
    updated_at: datetime


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)
    email: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserRead
    quota_remaining_tokens: int


class QuotaRead(BaseModel):
    monthly_token_limit: int
    used_tokens: int
    adjustment_tokens: int = 0
    remaining_tokens: int
