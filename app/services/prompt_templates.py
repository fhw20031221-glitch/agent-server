#! -*- coding: utf-8 -*-
"""
Ask / Code 提示词拼装。
"""

from __future__ import annotations

from typing import Iterable, List, Sequence


ASK_SYSTEM_PROMPT = """你是集成在桌面 IDE 里的代码助手。

目标：
1. 基于给定的 repo-map、文件上下文和最近对话回答问题。
2. 不要编造未提供的代码事实；证据不足时明确说明。
3. 回答尽量简洁，优先给出文件路径、模块关系和结论。
4. 始终使用简体中文回答。
5. 先回答用户当前问题，不要因为上下文里有 repo-map 就默认复述整个项目结构。

要求：
- 如果引用代码事实，优先点名文件路径。
- 如果问题是结构、上下文、项目索引相关，优先解释当前可见上下文范围。
- 如果用户只是打招呼、确认、追问或闲聊，请直接回应，不要强行展开项目分析。
- 除非用户明确要求，否则不要输出代码示例、伪代码或占位函数。
- 如果问题是在追问当前可见上下文、项目索引或上一轮对话，请直接依据下面提供的 repo-map、recent_messages 和代码片段回答。
- 尽量给出文件路径与模块关系。"""


CODE_SYSTEM_PROMPT = """你是 Aider 风格的代码编辑助手。

你的职责是根据用户需求生成最小编辑块，可以修改已有文件，也可以在明确需要时新建文本文件。

输出规则：
1. 可以先写不超过 3 行的简短说明。
2. 随后输出一个或多个编辑块。
3. 每个编辑块必须使用以下格式：

path/to/file.py
<<<<<<< SEARCH
原始文本
=======
替换后的文本
>>>>>>> REPLACE

如果需要新建文件，使用空 SEARCH：

path/to/new_file.py
<<<<<<< SEARCH
=======
新文件完整内容
>>>>>>> REPLACE

约束：
- SEARCH 必须与文件中的连续原文完全一致。
- 只修改必要的最小片段。
- 新建文件时，必须提供完整文件内容，并且 SEARCH 保持为空。
- 如果不需要修改，输出 NO_CHANGES。
- 不要输出 diff、JSON、XML 或工具调用。
- 始终使用简体中文。"""


def _join_lines(items: Iterable[str]) -> str:
    return "\n".join(str(item) for item in items if str(item).strip())


def format_recent_messages(messages: Sequence[dict], limit: int = 6) -> str:
    selected = list(messages or [])[-limit:]
    if not selected:
        return "暂无历史对话。"
    lines: List[str] = []
    for item in selected:
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("content", "") or "").strip()
        if len(content) > 280:
            content = content[:277] + "..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def build_conversation_state(messages: Sequence[dict]) -> str:
    selected = list(messages or [])
    if not selected:
        return "\n".join([
            "上一条用户消息: 无",
            "上一条助手消息: 无",
            "最近用户消息列表: 无",
            "最近对话轮数: 0",
        ])

    last_user = ""
    last_assistant = ""
    recent_user_messages: List[str] = []
    for item in selected:
        role = str(item.get("role", "") or "").strip()
        content = str(item.get("content", "") or "").strip()
        if not content:
            continue
        if role == "user":
            last_user = content
            recent_user_messages.append(content)
        elif role == "assistant":
            last_assistant = content

    recent_user_messages = recent_user_messages[-4:]
    round_count = sum(1 for item in selected if item.get("role") == "user")
    return "\n".join([
        "上一条用户消息: " + (last_user or "无"),
        "上一条助手消息: " + (last_assistant or "无"),
        "最近用户消息列表: " + (" | ".join(recent_user_messages) if recent_user_messages else "无"),
        f"最近对话轮数: {round_count}",
    ])


def format_snippets_for_prompt(snippets: Sequence[dict]) -> str:
    if not snippets:
        return "暂无代码片段。"
    blocks: List[str] = []
    for item in snippets:
        path = str(item.get("path", "") or "")
        language = str(item.get("language", "") or "plaintext")
        text = str(item.get("content", "") or "").rstrip()
        note = str(item.get("note", "") or "").strip()
        header = f"文件: {path}"
        if note:
            header += f" | 说明: {note}"
        blocks.append(f"{header}\n```{language}\n{text}\n```")
    return "\n\n".join(blocks)


def build_ask_user_prompt(
    *,
    question: str,
    project_name: str,
    project_root: str,
    active_file: str,
    chat_files: Sequence[str],
    selected_files: Sequence[str],
    repo_map_text: str,
    recent_messages: Sequence[dict],
    snippets: Sequence[dict],
) -> str:
    return _join_lines([
        f"用户问题:\n{question.strip()}",
        "",
        f"项目名: {project_name or '未命名项目'}",
        f"工作区根目录: {project_root or '客户端本地工作区'}",
        f"当前激活文件: {active_file or '无'}",
        "当前 in-chat files: " + (", ".join(chat_files) if chat_files else "无"),
        "本轮额外选中的上下文文件: " + (", ".join(selected_files) if selected_files else "无"),
        "",
        "Repo Map / 项目概览:",
        repo_map_text.strip() or "暂无 repo map。",
        "",
        "最近对话:",
        format_recent_messages(recent_messages),
        "",
        "会话状态:",
        build_conversation_state(recent_messages),
        "",
        "可用代码片段:",
        format_snippets_for_prompt(snippets),
        "",
        "回答要求:",
        "- 只基于以上上下文回答。",
        "- 若证据不足，直接说明缺少哪个文件或片段。",
        "- 尽量给出文件路径与模块关系。",
    ])


def build_code_user_prompt(
    *,
    question: str,
    project_name: str,
    project_root: str,
    active_file: str,
    chat_files: Sequence[str],
    selected_files: Sequence[str],
    repo_map_text: str,
    recent_messages: Sequence[dict],
    snippets: Sequence[dict],
) -> str:
    return _join_lines([
        f"代码修改请求:\n{question.strip()}",
        "",
        f"项目名: {project_name or '未命名项目'}",
        f"工作区根目录: {project_root or '客户端本地工作区'}",
        f"当前激活文件: {active_file or '无'}",
        "当前 in-chat files: " + (", ".join(chat_files) if chat_files else "无"),
        "本轮准备用于修改的文件: " + (", ".join(selected_files) if selected_files else "无"),
        "",
        "Repo Map / 项目概览:",
        repo_map_text.strip() or "暂无 repo map。",
        "",
        "最近对话:",
        format_recent_messages(recent_messages),
        "",
        "会话状态:",
        build_conversation_state(recent_messages),
        "",
        "可编辑代码片段:",
        format_snippets_for_prompt(snippets),
        "",
        "输出要求:",
        "- 只输出简短说明和 SEARCH/REPLACE 编辑块。",
        "- 修改已有文件时，必须保持 SEARCH 与原文完全一致。",
        "- 需要新建文件时，使用空 SEARCH，并在 REPLACE 中给出完整文件内容。",
        "- 如果现有上下文不足以安全修改，就输出 NO_CHANGES 并说明原因。",
    ])
