from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PublicModelRead(BaseModel):
    model_key: str
    display_name: str
    provider: str
    max_tokens: int
    is_default: bool = False


class LlmModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    model_key: str
    display_name: str
    provider: str
    base_url: str
    api_key_env: str
    upstream_model: str
    max_tokens: int
    temperature_default: float
    is_enabled: bool
    is_default: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class LlmModelCreate(BaseModel):
    model_key: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=120)
    provider: str = Field(default="openai-compatible", min_length=1, max_length=50)
    base_url: str = Field(min_length=1, max_length=500)
    api_key_env: str = Field(default="AGENT_SERVER_OPENAI_API_KEY", max_length=120)
    upstream_model: str = Field(min_length=1, max_length=120)
    max_tokens: int = Field(default=2048, ge=1, le=200000)
    temperature_default: float = Field(default=0.2, ge=0, le=2)
    is_enabled: bool = True
    is_default: bool = False
    sort_order: int = Field(default=100, ge=0, le=100000)


class LlmModelUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    provider: str | None = Field(default=None, min_length=1, max_length=50)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    api_key_env: str | None = Field(default=None, max_length=120)
    upstream_model: str | None = Field(default=None, min_length=1, max_length=120)
    max_tokens: int | None = Field(default=None, ge=1, le=200000)
    temperature_default: float | None = Field(default=None, ge=0, le=2)
    is_enabled: bool | None = None
    is_default: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)
