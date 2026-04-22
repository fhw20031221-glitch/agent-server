from __future__ import annotations

import re

BLOCK_RE = re.compile(
    r"(?ms)(?:^|\n)(?P<path>[^\n`][^\n]*?)\n<<<<<<< SEARCH\n(?P<search>.*?)\n=======\n(?P<replace>.*?)\n>>>>>>> REPLACE"
)


def normalize_display_path(path: str) -> str:
    normalized = str(path or "").strip().strip("`").replace("\\", "/")
    normalized = re.sub(r"^\./", "", normalized)
    return normalized


def strip_code_fences(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```") and raw.endswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return raw


def parse_edit_blocks(text: str) -> list[dict[str, str]]:
    normalized = strip_code_fences(text)
    blocks: list[dict[str, str]] = []
    for match in BLOCK_RE.finditer(normalized):
        path = normalize_display_path(match.group("path"))
        search = match.group("search").replace("\r\n", "\n")
        replace = match.group("replace").replace("\r\n", "\n")
        if not path or not search:
            continue
        blocks.append({
            "path": path,
            "search": search,
            "replace": replace,
        })
    return blocks


def extract_non_block_text(text: str) -> str:
    normalized = strip_code_fences(text)
    cleaned = BLOCK_RE.sub("", normalized)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
