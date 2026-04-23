from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.routes import chat as chat_route
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
            if data == "[DONE]":
                payloads.append(data)
            else:
                payloads.append(json.loads(data))
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
                username="route-tester",
                password_hash="hashed",
                status="active",
                monthly_token_limit=1_000_000,
            )
        )
        db.add(
            ChatSession(
                id=session_id,
                user_id=user_id,
                title="测试会话",
                project_name="demo",
            )
        )
        db.commit()

    def fake_current_user():
        return SimpleNamespace(id=user_id, username="route-tester", status="active")

    monkeypatch.setattr(chat_route, "session_scope", lambda: _session_scope_factory(SessionLocal))
    monkeypatch.setattr(chat_route.quota_service, "get_remaining_tokens", lambda db, user: 999_999)
    monkeypatch.setattr(chat_route.usage_service, "record_usage", lambda db, **kwargs: 999_000)
    app.dependency_overrides[deps.get_current_user] = fake_current_user

    client = TestClient(app)
    return client, SessionLocal, session_id


def test_ask_stream_route_returns_partial_and_summary(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    async def fake_stream_completion(**kwargs):
        assert kwargs["aggressive_sanitize"] is True
        yield "partial", "你好"
        yield "usage", {
            "text": "你好，联调成功。",
            "prompt_tokens": 12,
            "completion_tokens": 5,
            "total_tokens": 17,
            "model": "deepseek-chat",
            "provider": "openai-compatible",
        }

    monkeypatch.setattr(chat_route.llm_service, "stream_completion", fake_stream_completion)

    response = client.post(
        f"/chat/sessions/{session_id}/ask/stream",
        json={
            "mode": "ask",
            "question": "你好",
            "project_name": "demo",
            "active_file": "sample.py",
            "chat_files": ["sample.py"],
            "repo_map_text": "- sample.py",
            "snippets": [
                {
                    "path": "sample.py",
                    "language": "python",
                    "content": "print('hello')\n",
                    "note": "",
                }
            ],
        },
    )

    assert response.status_code == 200
    payloads = _parse_sse_payloads(response.text)
    event_types = [item["type"] for item in payloads if isinstance(item, dict)]
    assert "activity" in event_types
    assert "partial" in event_types
    assert "result" in event_types

    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    assert result["mode"] == "ask"
    assert result["response"] == "你好，联调成功。"
    assert result["edit_plan"]["status"] == "none"
    assert result["next_actions"] == []
    assert result["execution_summary"]["headline"] == "已基于当前上下文生成回答"

    with SessionLocal() as db:
        rows = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))
        assert len(rows) == 2
        assert rows[0].role == "user"
        assert rows[0].meta["mode"] == "ask"
        assert rows[1].role == "assistant"
        assert rows[1].meta["mode"] == "ask"
        assert rows[1].meta["edit_plan_status"] == "none"
        assert rows[1].meta["next_actions"] == []

    app.dependency_overrides.clear()
    client.close()


def test_code_stream_route_returns_edit_plan_without_persisting_edit_blocks(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    async def fake_stream_completion(**kwargs):
        assert kwargs["aggressive_sanitize"] is False
        raw = """已生成修改草案。

sample.py
<<<<<<< SEARCH
def check_flag():
    return False
=======
def check_flag():
    return True
>>>>>>> REPLACE
"""
        yield "partial", raw[:20]
        yield "usage", {
            "text": raw,
            "prompt_tokens": 20,
            "completion_tokens": 14,
            "total_tokens": 34,
            "model": "deepseek-chat",
            "provider": "openai-compatible",
        }

    monkeypatch.setattr(chat_route.llm_service, "stream_completion", fake_stream_completion)

    response = client.post(
        f"/chat/sessions/{session_id}/ask/stream",
        json={
            "mode": "code",
            "question": "把返回值改成 True",
            "project_name": "demo",
            "active_file": "sample.py",
            "chat_files": ["sample.py"],
            "repo_map_text": "- sample.py",
            "snippets": [
                {
                    "path": "sample.py",
                    "language": "python",
                    "content": "def check_flag():\n    return False\n",
                    "note": "",
                }
            ],
        },
    )

    assert response.status_code == 200
    payloads = _parse_sse_payloads(response.text)
    event_types = [item["type"] for item in payloads if isinstance(item, dict)]
    assert "activity" in event_types
    assert "result" in event_types
    assert "partial" not in event_types

    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    assert result["mode"] == "code"
    assert result["edit_plan"]["status"] == "ready"
    assert result["edit_plan"]["edits"] == [
        {
            "path": "sample.py",
            "search": "def check_flag():\n    return False",
            "replace": "def check_flag():\n    return True",
        }
    ]
    assert [item["type"] for item in result["next_actions"]] == ["review_patch", "apply_patch"]

    with SessionLocal() as db:
        rows = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))
        assert len(rows) == 2
        assistant_row = rows[1]
        assert assistant_row.meta["mode"] == "code"
        assert assistant_row.meta["edit_plan_status"] == "ready"
        assert assistant_row.meta["edit_paths"] == ["sample.py"]
        assert "edits" not in assistant_row.meta

    app.dependency_overrides.clear()
    client.close()


def test_code_stream_route_supports_create_file_edit_plan(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    async def fake_stream_completion(**kwargs):
        raw = """新增使用示例文档。

docs/usage.md
<<<<<<< SEARCH
=======
# Usage
hello
>>>>>>> REPLACE
"""
        yield "usage", {
            "text": raw,
            "prompt_tokens": 18,
            "completion_tokens": 9,
            "total_tokens": 27,
            "model": "deepseek-chat",
            "provider": "openai-compatible",
        }

    monkeypatch.setattr(chat_route.llm_service, "stream_completion", fake_stream_completion)

    response = client.post(
        f"/chat/sessions/{session_id}/ask/stream",
        json={
            "mode": "code",
            "question": "新增 docs/usage.md 示例文档",
            "project_name": "demo",
            "active_file": "README.md",
            "chat_files": [],
            "repo_map_text": "- README.md",
            "snippets": [],
        },
    )

    assert response.status_code == 200
    payloads = _parse_sse_payloads(response.text)
    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    assert result["edit_plan"]["status"] == "ready"
    assert result["edit_plan"]["edits"] == [
        {
            "path": "docs/usage.md",
            "search": "",
            "replace": "# Usage\nhello",
        }
    ]

    with SessionLocal() as db:
        assistant_row = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))[1]
        assert assistant_row.meta["edit_paths"] == ["docs/usage.md"]

    app.dependency_overrides.clear()
    client.close()
