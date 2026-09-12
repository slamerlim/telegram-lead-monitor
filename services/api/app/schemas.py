from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CommunityCreate(BaseModel):
    telegram_ref: str = Field(min_length=1, max_length=255)
    enabled: bool = True


class CommunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    telegram_ref: str
    telegram_chat_id: int | None
    name: str | None
    username: str | None
    url: str | None
    kind: str | None
    enabled: bool
    last_scanned_at: datetime | None


class ScanOut(BaseModel):
    run_id: int
    status: str
    communities: int
    days: int


class LeadOut(BaseModel):
    id: int
    score: float
    tier: str
    matched_keywords: list[str]
    matched_categories: list[str]
    reasons: list[str]
    semantic_score: float | None
    message_id: int
    message_url: str | None
    message_date: datetime
    text: str
    community_name: str | None
    community_username: str | None
    community_url: str | None
    author_id: int | None
    author_username: str | None
    author_url: str | None
    author_name: str | None
    author_bio: str | None


class HealthOut(BaseModel):
    status: str
    postgres: str
    redis: str


class StatsOut(BaseModel):
    communities: int
    messages: int
    leads: int
    high_leads: int
