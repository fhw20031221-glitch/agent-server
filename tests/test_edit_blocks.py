from app.services.edit_blocks import extract_non_block_text, parse_edit_blocks


def test_parse_edit_blocks_extracts_multiple_blocks():
    raw = """准备修改如下。

src/main.py
<<<<<<< SEARCH
old_value = 1
=======
old_value = 2
>>>>>>> REPLACE

src/utils.py
<<<<<<< SEARCH
return False
=======
return True
>>>>>>> REPLACE
"""
    blocks = parse_edit_blocks(raw)

    assert blocks == [
        {
            "path": "src/main.py",
            "search": "old_value = 1",
            "replace": "old_value = 2",
        },
        {
            "path": "src/utils.py",
            "search": "return False",
            "replace": "return True",
        },
    ]
    assert extract_non_block_text(raw) == "准备修改如下。"


def test_parse_edit_blocks_supports_create_file_with_empty_search():
    raw = """新增文件如下。

docs/usage.md
<<<<<<< SEARCH
=======
# Usage
hello
>>>>>>> REPLACE
"""
    blocks = parse_edit_blocks(raw)

    assert blocks == [
        {
            "path": "docs/usage.md",
            "search": "",
            "replace": "# Usage\nhello",
        }
    ]
