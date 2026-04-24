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


def _build_admin_context():
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
