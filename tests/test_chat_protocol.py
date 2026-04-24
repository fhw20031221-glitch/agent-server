from app.services.chat_protocol import build_edit_plan, build_message_metadata, build_next_actions


def test_build_edit_plan_returns_ready_for_valid_blocks():
    raw = """已生成修改草案。

app/api.py
<<<<<<< SEARCH
value = old_call()
=======
value = new_call()
>>>>>>> REPLACE
"""
    plan = build_edit_plan(raw)

    assert plan["status"] == "ready"
    assert plan["explanation"] == "已生成修改草案。"
    assert plan["edits"] == [
        {
            "path": "app/api.py",
            "search": "value = old_call()",
            "replace": "value = new_call()",
        }
    ]
    assert [item["type"] for item in build_next_actions("code", plan)] == ["review_patch", "apply_patch"]


def test_build_edit_plan_returns_none_for_no_changes():
    plan = build_edit_plan("NO_CHANGES\n当前不需要修改代码。")

    assert plan["status"] == "none"
    assert "不需要修改" in plan["explanation"]
    assert build_next_actions("code", plan) == []


def test_build_edit_plan_strips_bold_no_changes_marker():
    plan = build_edit_plan("**NO_CHANGES**：文生图示例已经包含 model 参数，无需修改。")

    assert plan["status"] == "none"
    assert plan["explanation"] == "文生图示例已经包含 model 参数，无需修改。"


def test_build_edit_plan_allows_normal_code_mode_reply():
    plan = build_edit_plan("你好，我在。请告诉我你想修改什么。")

    assert plan["status"] == "none"
    assert plan["explanation"] == "你好，我在。请告诉我你想修改什么。"
    assert build_next_actions("code", plan) == []


def test_build_edit_plan_returns_needs_context_for_context_request():
    plan = build_edit_plan(
        """CONTEXT_REQUEST
reason: 需要查看登录路由和认证服务后才能安全修改。
queries:
- login route auth service
- refresh token
paths:
- app/api/routes/auth.py
- app/services/auth_service.py
"""
    )

    assert plan["status"] == "needs_context"
    assert "登录路由" in plan["explanation"]
    assert plan["context_queries"] == ["login route auth service", "refresh token"]
    assert plan["context_paths"] == ["app/api/routes/auth.py", "app/services/auth_service.py"]
    assert [item["type"] for item in build_next_actions("code", plan)] == ["add_context", "retry"]


def test_message_metadata_omits_edit_content():
    edit_plan = build_edit_plan(
        """app/main.py
<<<<<<< SEARCH
foo()
=======
bar()
>>>>>>> REPLACE
"""
    )
    metadata = build_message_metadata(
        mode="code",
        execution_summary={"headline": "已生成修改草案", "steps": []},
        next_actions=build_next_actions("code", edit_plan),
        edit_plan=edit_plan,
    )

    assert metadata["mode"] == "code"
    assert metadata["edit_plan_status"] == "ready"
    assert metadata["edit_paths"] == ["app/main.py"]
    assert "edits" not in metadata


def test_message_metadata_deduplicates_edit_paths():
    raw = "\n".join(
        [
            "app/main.py",
            "<<<<<<< SEARCH",
            "foo()",
            "=======",
            "bar()",
            ">>>>>>> REPLACE",
            "",
            "app/main.py",
            "<<<<<<< SEARCH",
            "baz()",
            "=======",
            "qux()",
            ">>>>>>> REPLACE",
            "",
        ]
    )
    edit_plan = build_edit_plan(raw)
    metadata = build_message_metadata(
        mode="code",
        execution_summary={"headline": "已生成修改草案", "steps": []},
        next_actions=build_next_actions("code", edit_plan),
        edit_plan=edit_plan,
    )

    assert metadata["edit_paths"] == ["app/main.py"]


def test_build_edit_plan_returns_ready_for_create_file_block():
    raw = """新增测试文档。

docs/usage.md
<<<<<<< SEARCH
=======
# Usage
hello
>>>>>>> REPLACE
"""
    plan = build_edit_plan(raw)

    assert plan["status"] == "ready"
    assert plan["explanation"] == "新增测试文档。"
    assert plan["edits"] == [
        {
            "path": "docs/usage.md",
            "search": "",
            "replace": "# Usage\nhello",
        }
    ]
