from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChatMode = Literal["ask", "code"]
NextActionType = Literal["review_patch", "apply_patch"]


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Any = None


class AgentToolFunction(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgentTool(BaseModel):
    type: Literal["function"] = "function"
    function: AgentToolFunction


class AgentTurnRequest(BaseModel):
    session_id: str = Field(min_length=1)
    question: str = ""
    mode: ChatMode = "ask"
    model_key: str | None = Field(default=None, max_length=120)
    messages: list[AgentMessage] = Field(default_factory=list)
    tools: list[AgentTool] = Field(default_factory=list)
    persist_user_message: bool = False


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

