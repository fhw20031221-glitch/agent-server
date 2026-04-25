from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_SERVER_", env_file=".env", extra="ignore")

    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = "mysql+pymysql://agent:agent@localhost:3306/agent?charset=utf8mb4"
    jwt_secret: str = "change-me"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    default_monthly_token_limit: int = 1_000_000
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_timeout_seconds: int = 120
    agent_debug_tools: bool = False
    agent_debug_raw: bool = False
    agent_debug_log_file: str = ""
    admin_username: str = ""
    admin_password: str = ""
    admin_email: str = ""
    cors_origins_raw: str = Field(default="", alias="CORS_ORIGINS")

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]


settings = Settings()
