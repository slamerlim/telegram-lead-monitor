from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MessageEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    community_id: int
    telegram_chat_id: int
    community_name: str
    community_username: str | None = None
    community_url: str | None = None
    message_id: int
    message_url: str | None = None
    message_date: datetime
    author_id: int | None = None
    author_username: str | None = None
    author_name: str | None = None
    author_bio: str | None = None
    author_is_bot: bool | None = None
    message_text: str = Field(min_length=1)
    is_reply: bool = False
    raw: dict[str, Any] | None = None


class ScanRequest(BaseModel):
    community_id: int | None = None
    days: int = Field(default=30, ge=1, le=365)
    force: bool = False
