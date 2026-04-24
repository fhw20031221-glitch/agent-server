from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.db.models import Base, ChatSession, LlmModel, User
from app.main import app
from app.services import model_service


@contextmanager
def _override_db(SessionLocal):
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _build_admin_context(*, seed_deepseek_model: bool = True, seed_remote_model: bool = False):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    admin_id = str(uuid4())
    user_id = str(uuid4())

    with SessionLocal() as db:
        db.add(
            User(
                id=admin_id,
                username="admin",
                password_hash="hashed",
                role="admin",
                status="active",
                monthly_token_limit=2_000_000,
            )
        )
        db.add(
            User(
                id=user_id,
                username="alice",
                password_hash="hashed",
                role="user",
                status="active",
                monthly_token_limit=1_000_000,
            )
        )
        db.add(
            ChatSession(
                id=str(uuid4()),
                user_id=user_id,
                title="测试会话",
                project_name="demo",
            )
        )
        if seed_deepseek_model:
            db.add(
                LlmModel(
                    model_key="deepseek-chat",
                    display_name="DeepSeek Chat",
                    provider="openai-compatible",
                    base_url="https://api.deepseek.com/v1",
                    api_key_env="AGENT_SERVER_OPENAI_API_KEY",
                    upstream_model="deepseek-chat",
                    max_tokens=8192,
                    temperature_default=0.2,
                    is_enabled=True,
                    is_default=True,
                    sort_order=10,
                )
            )
        if seed_remote_model:
            db.add(
                LlmModel(
                    model_key="qwen-plus",
                    display_name="qwen-plus",
                    provider="bailian",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                    api_key_env="DASHSCOPE_API_KEY",
                    upstream_model="qwen-plus",
                    max_tokens=8192,
                    temperature_default=0.2,
                    is_enabled=True,
                    is_default=True,
                    sort_order=100,
                )
            )
        db.commit()

    def override_get_db():
        with _override_db(SessionLocal) as db:
            yield db

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(
        id=admin_id,
        username="admin",
        role="admin",
        status="active",
        monthly_token_limit=2_000_000,
    )
    app.dependency_overrides[deps.get_current_admin_user] = lambda: SimpleNamespace(
        id=admin_id,
        username="admin",
        role="admin",
        status="active",
        monthly_token_limit=2_000_000,
    )
    return TestClient(app), user_id


def test_models_route_returns_enabled_models():
    client, _user_id = _build_admin_context()

    response = client.get("/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "model_key": "deepseek-chat",
            "display_name": "DeepSeek Chat",
            "provider": "openai-compatible",
            "max_tokens": 8192,
            "is_default": True,
        }
    ]

    app.dependency_overrides.clear()
    client.close()


def test_models_route_adds_settings_model_when_remote_models_exist(monkeypatch):
    monkeypatch.setattr(model_service.settings, "openai_model", "deepseek-chat")
    monkeypatch.setattr(model_service.settings, "openai_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(model_service.settings, "openai_api_key", "test-deepseek-key")
    client, _user_id = _build_admin_context(seed_deepseek_model=False, seed_remote_model=True)

    response = client.get("/models")

    assert response.status_code == 200
    rows = {item["model_key"]: item for item in response.json()}
    assert rows["qwen-plus"]["is_default"] is True
    assert rows["deepseek-chat"] == {
        "model_key": "deepseek-chat",
        "display_name": "deepseek-chat",
        "provider": "openai-compatible",
        "max_tokens": 8192,
        "is_default": False,
    }

    admin_response = client.get("/admin/models")
    assert admin_response.status_code == 200
    admin_rows = {item["model_key"]: item for item in admin_response.json()}
    assert admin_rows["deepseek-chat"]["base_url"] == "https://api.deepseek.com/v1"
    assert admin_rows["deepseek-chat"]["api_key_env"] == "AGENT_SERVER_OPENAI_API_KEY"
    assert admin_rows["deepseek-chat"]["upstream_model"] == "deepseek-chat"
    assert admin_rows["deepseek-chat"]["is_enabled"] is True

    app.dependency_overrides.clear()
    client.close()


def test_admin_can_sync_openai_compatible_models(monkeypatch):
    client, _user_id = _build_admin_context()
    captured = {}

    def fake_fetch_openai_compatible_models(*, base_url, api_key, timeout_seconds=None):
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        captured["timeout_seconds"] = timeout_seconds
        return [
            model_service.RemoteModel(model_key="qwen-plus", created=1, owned_by="system"),
            model_service.RemoteModel(model_key="qwen-turbo", created=2, owned_by="system"),
        ]

    monkeypatch.setattr(model_service, "fetch_openai_compatible_models", fake_fetch_openai_compatible_models)

    response = client.post(
        "/admin/models/sync",
        json={
            "provider": "bailian",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key_env": "DASHSCOPE_API_KEY",
            "api_key": "test-sync-key",
            "default_model_key": "qwen-plus",
            "max_tokens": 32768,
            "sort_order_start": 50,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "provider": "bailian",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "total_remote": 2,
        "created": 2,
        "updated": 0,
        "disabled": 0,
        "skipped": 0,
        "default_model_key": "qwen-plus",
    }
    assert captured == {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "test-sync-key",
        "timeout_seconds": None,
    }

    models_response = client.get("/admin/models")
    assert models_response.status_code == 200
    rows = {item["model_key"]: item for item in models_response.json()}
    assert rows["qwen-plus"]["provider"] == "bailian"
    assert rows["qwen-plus"]["upstream_model"] == "qwen-plus"
    assert rows["qwen-plus"]["api_key_env"] == "DASHSCOPE_API_KEY"
    assert rows["qwen-plus"]["max_tokens"] == 32768
    assert rows["qwen-plus"]["is_default"] is True
    assert rows["deepseek-chat"]["is_default"] is False

    app.dependency_overrides.clear()
    client.close()


def test_admin_model_sync_requires_api_key(monkeypatch):
    client, _user_id = _build_admin_context()

    def fake_fetch_openai_compatible_models(**kwargs):
        raise AssertionError("fetch should not be called without an API key")

    monkeypatch.setattr(model_service, "fetch_openai_compatible_models", fake_fetch_openai_compatible_models)

    response = client.post(
        "/admin/models/sync",
        json={
            "provider": "bailian",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key_env": "MISSING_MODEL_SYNC_KEY",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "模型同步 API Key 未配置"

    app.dependency_overrides.clear()
    client.close()


def test_admin_can_adjust_remaining_quota():
    client, user_id = _build_admin_context()

    response = client.patch(
        f"/admin/users/{user_id}/remaining-quota",
        json={"remaining_tokens": 888888, "reason": "测试调整"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["monthly_token_limit"] == 1_000_000
    assert payload["used_tokens"] == 0
    assert payload["adjustment_tokens"] == -111112
    assert payload["remaining_tokens"] == 888888

    quota_response = client.get(f"/admin/users/{user_id}")
    assert quota_response.status_code == 200
    quota_payload = quota_response.json()
    assert quota_payload["remaining_tokens"] == 888888

    app.dependency_overrides.clear()
    client.close()
