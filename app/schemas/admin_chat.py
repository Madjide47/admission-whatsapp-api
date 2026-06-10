"""Schémas Pydantic du chatbot admin (POST /api/v1/admin/chat)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AdminChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: uuid.UUID | None = None
    confirm_action: bool = False


class PendingAction(BaseModel):
    type: str
    summary: str
    count: int | None = None


class AdminChatResponse(BaseModel):
    session_id: str
    message: str
    data_table: list | None = None
    pending_action: PendingAction | None = None
    tool_used: str | None = None


class AdminChatSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    admin_identifier: str | None = None
    messages: list | None = None
    created_at: datetime
    last_activity_at: datetime
