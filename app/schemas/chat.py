from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SnippetInput(BaseModel):
    path: str
    language: str = "plaintext"
    content: str
    note: str = ""


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    project_name: str = ""
    active_file: str = ""
    chat_files: list[str] = Field(default_factory=list)
    repo_map_text: str = ""
    snippets: list[SnippetInput] = Field(default_factory=list)


class ChatSessionCreateRequest(BaseModel):
    title: str = "新对话"
    project_name: str | None = None


class ChatSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    project_name: str | None
    created_at: datetime
    updated_at: datetime


class ChatMessageRead(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    metadata: dict
    created_at: datetime
