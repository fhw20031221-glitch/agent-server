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
    plan = build_edit_plan("NO_CHANGES\n当前上下文不足以安全修改。")

    assert plan["status"] == "none"
    assert "上下文不足" in plan["explanation"]
    assert build_next_actions("code", plan)[0]["type"] == "add_context"


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
