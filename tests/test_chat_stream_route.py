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


def test_ask_stream_route_forwards_reasoning_content(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    async def fake_stream_completion(**kwargs):
        yield "reasoning", "先分析问题。"
        yield "reasoning", "先分析问题。然后组织答案。"
        yield "partial", "最终回答"
        yield "usage", {
            "text": "最终回答。",
            "reasoning_content": "先分析问题。然后组织答案。",
            "prompt_tokens": 12,
            "completion_tokens": 20,
            "total_tokens": 32,
            "model": "qwen-plus",
            "provider": "bailian",
        }

    monkeypatch.setattr(chat_route.llm_service, "stream_completion", fake_stream_completion)

    response = client.post(
        f"/chat/sessions/{session_id}/ask/stream",
        json={
            "mode": "ask",
            "question": "解释一下",
            "project_name": "demo",
            "active_file": "",
            "chat_files": [],
            "repo_map_text": "",
            "snippets": [],
        },
    )

    assert response.status_code == 200
    payloads = _parse_sse_payloads(response.text)
    reasoning_events = [item for item in payloads if isinstance(item, dict) and item["type"] == "reasoning"]
    assert [item["text"] for item in reasoning_events] == [
        "先分析问题。",
        "先分析问题。然后组织答案。",
    ]
    assert all(item["collapsed"] is True for item in reasoning_events)

    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    assert result["response"] == "最终回答。"
    assert result["reasoning"] == {
        "content": "先分析问题。然后组织答案。",
        "collapsed": True,
    }

    with SessionLocal() as db:
        assistant_row = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))[1]
        assert assistant_row.meta["reasoning"] == {
            "content": "先分析问题。然后组织答案。",
            "collapsed": True,
        }

    app.dependency_overrides.clear()
    client.close()


def test_ask_stream_route_returns_context_request_without_persisting_assistant(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    async def fake_stream_completion(**kwargs):
        raw = """CONTEXT_REQUEST
reason: 需要查看入口和路由文件后才能说明各文件关系。
queries:
- fastapi router include
paths:
- app/main.py
- app/api/routes/chat.py
"""
        yield "usage", {
            "text": raw,
            "prompt_tokens": 18,
            "completion_tokens": 16,
            "total_tokens": 34,
            "model": "deepseek-chat",
            "provider": "openai-compatible",
        }

    monkeypatch.setattr(chat_route.llm_service, "stream_completion", fake_stream_completion)

    response = client.post(
        f"/chat/sessions/{session_id}/ask/stream",
        json={
            "mode": "ask",
            "question": "查看代码中的各文件关系",
            "project_name": "demo",
            "active_file": "",
            "chat_files": [],
            "repo_map_text": "- app/main.py\n- app/api/routes/chat.py",
            "snippets": [],
        },
    )

    assert response.status_code == 200
    payloads = _parse_sse_payloads(response.text)
    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    activities = [item["payload"] for item in payloads if isinstance(item, dict) and item["type"] == "activity"]

    assert result["mode"] == "ask"
    assert result["edit_plan"]["status"] == "needs_context"
    assert result["response"] == "需要查看入口和路由文件后才能说明各文件关系。"
    assert result["edit_plan"]["context_queries"] == ["fastapi router include"]
    assert result["edit_plan"]["context_paths"] == ["app/main.py", "app/api/routes/chat.py"]
    assert [item["type"] for item in result["next_actions"]] == ["add_context", "retry"]
    assert result["message_id"] == ""
    assert result["execution_summary"]["headline"] == "需要补充上下文后继续回答"
    assert any(item["phase"] == "context_request" for item in activities)

    with SessionLocal() as db:
        rows = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))
        assert len(rows) == 1
        assert rows[0].role == "user"

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


def test_code_stream_route_allows_non_edit_reply(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    async def fake_stream_completion(**kwargs):
        assert kwargs["aggressive_sanitize"] is False
        yield "usage", {
            "text": "你好，我在。请告诉我你想修改什么。",
            "prompt_tokens": 16,
            "completion_tokens": 8,
            "total_tokens": 24,
            "model": "deepseek-chat",
            "provider": "openai-compatible",
        }

    monkeypatch.setattr(chat_route.llm_service, "stream_completion", fake_stream_completion)

    response = client.post(
        f"/chat/sessions/{session_id}/ask/stream",
        json={
            "mode": "code",
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
    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    activities = [item["payload"] for item in payloads if isinstance(item, dict) and item["type"] == "activity"]
    assert result["mode"] == "code"
    assert result["response"] == "你好，我在。请告诉我你想修改什么。"
    assert result["edit_plan"]["status"] == "none"
    assert result["next_actions"] == []
    assert any(item["title"] == "识别普通回复" for item in activities)
    assert not any(item["title"] == "解析编辑块" for item in activities if item["status"] == "done")

    with SessionLocal() as db:
        rows = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))
        assert len(rows) == 2
        assert rows[0].role == "user"
        assert rows[1].role == "assistant"
        assert rows[1].content == "你好，我在。请告诉我你想修改什么。"
        assert rows[1].meta["edit_plan_status"] == "none"

    app.dependency_overrides.clear()
    client.close()


def test_code_stream_route_returns_context_request_without_persisting_assistant(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    async def fake_stream_completion(**kwargs):
        raw = """CONTEXT_REQUEST
reason: 需要查看路由和服务实现后才能安全修改。
queries:
- auth route service
paths:
- app/api/routes/auth.py
- app/services/auth_service.py
"""
        yield "usage", {
            "text": raw,
            "prompt_tokens": 18,
            "completion_tokens": 16,
            "total_tokens": 34,
            "model": "deepseek-chat",
            "provider": "openai-compatible",
        }

    monkeypatch.setattr(chat_route.llm_service, "stream_completion", fake_stream_completion)

    response = client.post(
        f"/chat/sessions/{session_id}/ask/stream",
        json={
            "mode": "code",
            "question": "修复登录失败的问题",
            "project_name": "demo",
            "active_file": "README.md",
            "chat_files": [],
            "repo_map_text": "- app/api/routes/auth.py\n- app/services/auth_service.py",
            "snippets": [],
        },
    )

    assert response.status_code == 200
    payloads = _parse_sse_payloads(response.text)
    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    assert result["edit_plan"]["status"] == "needs_context"
    assert result["edit_plan"]["context_queries"] == ["auth route service"]
    assert result["edit_plan"]["context_paths"] == ["app/api/routes/auth.py", "app/services/auth_service.py"]
    assert [item["type"] for item in result["next_actions"]] == ["add_context", "retry"]
    assert result["message_id"] == ""

    with SessionLocal() as db:
        rows = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))
        assert len(rows) == 1
        assert rows[0].role == "user"

    app.dependency_overrides.clear()
    client.close()


def test_code_stream_context_retry_does_not_duplicate_user_message(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    with SessionLocal() as db:
        db.add(ChatMessage(session_id=session_id, role="user", content="修复登录失败的问题", meta={"mode": "code"}))
        db.commit()

    async def fake_stream_completion(**kwargs):
        raw = "\n".join(
            [
                "已生成修改草案。",
                "",
                "app/api/routes/auth.py",
                "<<<<<<< SEARCH",
                "return False",
                "=======",
                "return True",
                ">>>>>>> REPLACE",
                "",
            ]
        )
        yield "usage", {
            "text": raw,
            "prompt_tokens": 24,
            "completion_tokens": 18,
            "total_tokens": 42,
            "model": "deepseek-chat",
            "provider": "openai-compatible",
        }

    monkeypatch.setattr(chat_route.llm_service, "stream_completion", fake_stream_completion)

    response = client.post(
        f"/chat/sessions/{session_id}/ask/stream",
        json={
            "mode": "code",
            "context_retry": True,
            "question": "修复登录失败的问题",
            "project_name": "demo",
            "active_file": "app/api/routes/auth.py",
            "chat_files": [],
            "repo_map_text": "- app/api/routes/auth.py",
            "snippets": [
                {
                    "path": "app/api/routes/auth.py",
                    "language": "python",
                    "content": "return False\n",
                    "note": "",
                }
            ],
        },
    )

    assert response.status_code == 200
    payloads = _parse_sse_payloads(response.text)
    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    assert result["edit_plan"]["status"] == "ready"

    with SessionLocal() as db:
        rows = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))
        assert [row.role for row in rows] == ["user", "assistant"]
        assert rows[0].content == "修复登录失败的问题"
        assert rows[1].meta["edit_plan_status"] == "ready"

    app.dependency_overrides.clear()
    client.close()


def test_ask_stream_context_retry_does_not_duplicate_user_message(monkeypatch):
    client, SessionLocal, session_id = _build_test_context(monkeypatch)

    with SessionLocal() as db:
        db.add(ChatMessage(session_id=session_id, role="user", content="查看代码中的各文件关系", meta={"mode": "ask"}))
        db.commit()

    async def fake_stream_completion(**kwargs):
        yield "usage", {
            "text": "app/main.py 负责创建 FastAPI 应用并注册 chat 路由。",
            "prompt_tokens": 24,
            "completion_tokens": 18,
            "total_tokens": 42,
            "model": "deepseek-chat",
            "provider": "openai-compatible",
        }

    monkeypatch.setattr(chat_route.llm_service, "stream_completion", fake_stream_completion)

    response = client.post(
        f"/chat/sessions/{session_id}/ask/stream",
        json={
            "mode": "ask",
            "context_retry": True,
            "question": "查看代码中的各文件关系",
            "project_name": "demo",
            "active_file": "app/main.py",
            "chat_files": [],
            "repo_map_text": "- app/main.py",
            "snippets": [
                {
                    "path": "app/main.py",
                    "language": "python",
                    "content": "app.include_router(chat.router)\n",
                    "note": "",
                }
            ],
        },
    )

    assert response.status_code == 200
    payloads = _parse_sse_payloads(response.text)
    result = next(item for item in payloads if isinstance(item, dict) and item["type"] == "result")
    assert result["mode"] == "ask"
    assert result["edit_plan"]["status"] == "none"

    with SessionLocal() as db:
        rows = list(db.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())))
        assert [row.role for row in rows] == ["user", "assistant"]
        assert rows[0].content == "查看代码中的各文件关系"
        assert rows[1].content == "app/main.py 负责创建 FastAPI 应用并注册 chat 路由。"

    app.dependency_overrides.clear()
    client.close()
