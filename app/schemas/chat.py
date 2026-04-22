from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChatMode = Literal["ask", "code"]
NextActionType = Literal["review_patch", "apply_patch", "add_context", "retry"]
EditPlanStatus = Literal["none", "ready", "invalid"]


class SnippetInput(BaseModel):
    path: str
    language: str = "plaintext"
    content: str
    note: str = ""


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    mode: ChatMode = "ask"
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
    metadata: dict[str, Any]
    created_at: datetime


class ExecutionSummaryStep(BaseModel):
    phase: str
    title: str
    detail: str
    status: str


class ExecutionSummary(BaseModel):
    headline: str = ""
    steps: list[ExecutionSummaryStep] = Field(default_factory=list)


class NextAction(BaseModel):
    type: NextActionType
    label: str
    path: str = ""


class EditPlanEdit(BaseModel):
    path: str
    search: str
    replace: str


class EditPlan(BaseModel):
    status: EditPlanStatus = "none"
    explanation: str = ""
    edits: list[EditPlanEdit] = Field(default_factory=list)
