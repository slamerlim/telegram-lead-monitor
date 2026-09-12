from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db import Base


class Community(Base):
    __tablename__ = "communities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_ref: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship(back_populates="community", cascade="all, delete-orphan")


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_bot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship(back_populates="author")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("community_id", "telegram_message_id", name="uq_message_community_message"),
        Index("ix_messages_date", "message_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("authors.id", ondelete="SET NULL"), nullable=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer, index=True)
    message_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    text: Mapped[str] = mapped_column(Text)
    is_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    community: Mapped[Community] = relationship(back_populates="messages")
    author: Mapped[Author | None] = relationship(back_populates="messages")
    lead: Mapped["Lead | None"] = relationship(back_populates="message", uselist=False, cascade="all, delete-orphan")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (Index("ix_leads_score", "score"), Index("ix_leads_created", "created_at"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    tier: Mapped[str] = mapped_column(String(16), default="LOW")
    matched_keywords: Mapped[str] = mapped_column(Text, default="[]")
    matched_categories: Mapped[str] = mapped_column(Text, default="[]")
    reasons: Mapped[str] = mapped_column(Text, default="[]")
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    message: Mapped[Message] = relationship(back_populates="lead")


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    community_id: Mapped[int | None] = mapped_column(ForeignKey("communities.id", ondelete="SET NULL"), nullable=True)
    days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    messages_seen: Mapped[int] = mapped_column(Integer, default=0)
    messages_published: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
