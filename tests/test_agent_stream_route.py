from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.routes import agent as agent_route
from app.db.models import Base, ChatMessage, ChatSession, User
from app.main import app


def _parse_sse_payloads(raw_text: str) -> list[dict | str]:
    payloads: list[dict | str] = []
    for chunk in raw_text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        for line in chunk.splitlines():
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            payloads.append(data if data == "[DONE]" else json.loads(data))
    return payloads


@contextmanager
def _session_scope_factory(SessionLocal):
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _build_test_context(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    user_id = str(uuid4())
    session_id = str(uuid4())
    with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                username="agent-route-tester",
                password_hash="hashed",
                status="active",
                monthly_token_limit=1_000_000,
            )
        )
        db.add(ChatSession(id=session_id, user_id=user_id, title="Agent 测试会话", project_name="demo"))
        db.commit()

    def fake_current_user():
        return SimpleNamespace(id=user_id, username="agent-route-tester", status="active")

    monkeypatch.setattr(agent_route, "session_scope", lambda: _session_scope_factory(SessionLocal))
    monkeypatch.setattr(agent_route.quota_service, "get_remaining_tokens", lambda db, user: 999_999)
    monkeypatch.setattr(agent_route.usage_service, "record_usage", lambda db, **kwargs: 999_000)
    app.dependency_overrides[deps.get_current_user] = fake_current_user

    client = TestClient(app)
    return client, SessionLocal, session_id


def test_agent_turn_stream_returns_tool_call_without_persisting_assistant(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    async def fake_stream_agent_turn(**kwargs):
        assert kwargs["tools"][0]["function"]["name"] == "workspace_read_file"
        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "workspace_read_file",
                "arguments": {"path": "app/main.py"},
                "arguments_json": '{"path":"app/main.py"}',
            },
        }
        yield "tool_call", tool_call
        yield "usage", {
            "text": "",
            "tool_calls": [tool_call],
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "model": "deepseek-chat",
            "provider": "openai-compatible",
        }

    monkeypatch.setattr(agent_route.llm_service, "stream_agent_turn", fake_stream_agent_turn)

    response = client.post(
        "/agent/turn/stream",
        json={
            "session_id": session_id,
            "mode": "ask",
            "question": "认证在哪里做？",
            "persist_user_message": True,
            "messages": [{"role": "user", "content": "认证在哪里做？"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "workspace_read_file",
                        "description": "读取文件",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payloads = _parse_sse_payloads(response.text)
    assert any(item["type"] == "tool_call" for item in payloads if isinstance(item, dict))
    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    assert result["tool_calls"][0]["function"]["arguments"] == {"path": "app/main.py"}
    assert result["message_id"] == ""

    with SessionLocal() as db:
        rows = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))
        assert [row.role for row in rows] == ["user"]
        assert rows[0].meta["agent"] is True

    app.dependency_overrides.clear()
    client.close()


def test_agent_turn_stream_persists_final_answer_with_tool_trace(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    async def fake_stream_agent_turn(**kwargs):
        yield "partial", "认证在 auth_middleware。"
        yield "usage", {
            "text": "认证在 auth_middleware。",
            "tool_calls": [],
            "prompt_tokens": 30,
            "completion_tokens": 8,
            "model": "deepseek-chat",
            "provider": "openai-compatible",
        }

    monkeypatch.setattr(agent_route.llm_service, "stream_agent_turn", fake_stream_agent_turn)

    response = client.post(
        "/agent/turn/stream",
        json={
            "session_id": session_id,
            "mode": "ask",
            "messages": [
                {"role": "user", "content": "认证在哪里做？"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "workspace_read_file", "arguments": '{"path":"app/main.py"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": '{"path":"app/main.py"}'},
            ],
            "tools": [],
        },
    )

    assert response.status_code == 200
    payloads = _parse_sse_payloads(response.text)
    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    assert result["message_id"]
    assert result["response"] == "认证在 auth_middleware。"

    with SessionLocal() as db:
        rows = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))
        assert [row.role for row in rows] == ["assistant"]
        assert rows[0].meta["agent"] is True
        assert [item["type"] for item in rows[0].meta["tool_trace"]] == ["tool_call", "tool_result"]

    app.dependency_overrides.clear()
    client.close()
