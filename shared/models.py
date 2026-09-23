from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
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
    resolve_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    __table_args__ = (
        Index("ix_leads_score", "score"),
        Index("ix_leads_created", "created_at"),
        Index("ix_leads_lead_type", "lead_type"),
        Index("ix_leads_buyer_type", "buyer_type"),
        Index("ix_leads_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    tier: Mapped[str] = mapped_column(String(16), default="LOW")
    lead_type: Mapped[str] = mapped_column(String(64), default="NOISE")
    buyer_type: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(32), default="NEW")
    intent_score: Mapped[float] = mapped_column(Float, default=0.0)
    technical_score: Mapped[float] = mapped_column(Float, default=0.0)
    commercial_score: Mapped[float] = mapped_column(Float, default=0.0)
    promotion_score: Mapped[float] = mapped_column(Float, default=0.0)
    matched_keywords: Mapped[str] = mapped_column(Text, default="[]")
    matched_categories: Mapped[str] = mapped_column(Text, default="[]")
    reasons: Mapped[str] = mapped_column(Text, default="[]")
    contact_usernames: Mapped[str] = mapped_column(Text, default="[]")
    contact_urls: Mapped[str] = mapped_column(Text, default="[]")
    budget_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    opportunity_key: Mapped[str | None] = mapped_column(String(96), unique=True, nullable=True, index=True)
    # Commercial ops provenance (0008): how the lead entered the operator queue.
    source: Mapped[str] = mapped_column(String(24), default="scorer", nullable=False)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    message: Mapped[Message] = relationship(back_populates="lead")
    events: Mapped[list["LeadEvent"]] = relationship(back_populates="lead")


class LeadEvent(Base):
    """Append-only commercial workflow audit (contact / outcome / notes)."""

    __tablename__ = "lead_events"
    __table_args__ = (
        Index("ix_lead_events_lead_id", "lead_id"),
        Index("ix_lead_events_occurred_at", "occurred_at"),
        Index("ix_lead_events_event_type", "event_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="NO ACTION"), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="NO ACTION"), nullable=False)
    opportunity_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)

    lead: Mapped[Lead] = relationship(back_populates="events")


class MessageScore(Base):
    """Persisted scoring decision for every processed message (incl. LOW/negatives)."""

    __tablename__ = "message_scores"
    __table_args__ = (
        Index("ix_message_scores_scored_at", "scored_at"),
        Index("ix_message_scores_decision", "decision"),
        Index("ix_message_scores_tier", "tier"),
        Index("ix_message_scores_community_id", "community_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True, index=True)
    # Index provided via __table_args__ only (avoid duplicate ix_message_scores_community_id).
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id", ondelete="CASCADE"))
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    tier: Mapped[str] = mapped_column(String(16), default="LOW")
    buyer_type: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    lead_type: Mapped[str] = mapped_column(String(64), default="NOISE")
    intent_score: Mapped[float] = mapped_column(Float, default=0.0)
    technical_score: Mapped[float] = mapped_column(Float, default=0.0)
    commercial_score: Mapped[float] = mapped_column(Float, default=0.0)
    promotion_score: Mapped[float] = mapped_column(Float, default=0.0)
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_keywords: Mapped[str] = mapped_column(Text, default="[]")
    matched_categories: Mapped[str] = mapped_column(Text, default="[]")
    reasons: Mapped[str] = mapped_column(Text, default="[]")
    rule_version: Mapped[str] = mapped_column(String(32), default="unknown")
    decision: Mapped[str] = mapped_column(String(16), default="NEGATIVE")  # POSITIVE|NEGATIVE|AMBIGUOUS

    message: Mapped[Message] = relationship()


class HumanLabel(Base):
    """Agent- or human-assigned provisional labels (not independent evaluation ground truth)."""

    __tablename__ = "human_labels"
    __table_args__ = (
        Index("ix_human_labels_label", "label"),
        Index("ix_human_labels_fp_class", "fp_class"),
        Index("ix_human_labels_labeled_at", "labeled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True, index=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id", ondelete="CASCADE"), index=True)
    opportunity_key: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(32), nullable=False)  # TRUE_LEAD|FALSE_POSITIVE|AMBIGUOUS
    fp_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commercially_actionable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    labeled_by: Mapped[str] = mapped_column(String(64), default="human", nullable=False)
    labeled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    message: Mapped[Message] = relationship()


class LabelReviewSample(Base):
    """Stratified evaluation sample membership (methodology-bound)."""

    __tablename__ = "label_review_samples"
    __table_args__ = (
        UniqueConstraint("sample_batch_id", "message_id", name="uq_label_review_samples_batch_msg"),
        Index("ix_label_review_samples_batch", "sample_batch_id"),
        Index("ix_label_review_samples_stratum", "stratum"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sample_batch_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    stratum: Mapped[str] = mapped_column(String(64), nullable=False)
    methodology_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class LabelReview(Base):
    """Independent review row (human or AI) — never overwrites human_labels.

    AI rows (validator_kind='ai') are insert-only (DB trigger); use AI_* labels.
    """

    __tablename__ = "label_reviews"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "reviewer_id",
            "sample_batch_id",
            name="uq_label_reviews_msg_reviewer_batch",
        ),
        Index("ix_label_reviews_reviewer_id", "reviewer_id"),
        Index("ix_label_reviews_label", "label"),
        Index("ix_label_reviews_batch", "sample_batch_id"),
        Index("ix_lr_kind_batch", "validator_kind", "sample_batch_id"),
        Index("ix_lr_run", "validation_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    sample_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    fp_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commercially_actionable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    scorer_shown: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prior_label_shown: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    validator_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    validation_mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validation_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    lead_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale_short: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    peer_outputs_shown: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_attempt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attestation_sig: Mapped[str | None] = mapped_column(String(128), nullable=True)


class AIValidationAttempt(Base):
    """Append-only Cursor validator call log (success and failure)."""

    __tablename__ = "ai_validation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "validation_run_id",
            "message_id",
            "reviewer_id",
            "attempt_no",
            name="uq_ai_val_attempt",
        ),
        Index("ix_ai_val_attempts_status", "status"),
        Index("ix_ai_val_attempts_message", "message_id"),
        Index("ix_ai_val_attempts_run", "validation_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_batch_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_mode: Mapped[str] = mapped_column(String(8), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    model_family: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ValidationConsensus(Base):
    """Append-only consensus snapshot; gate re-derives from signed opinions."""

    __tablename__ = "validation_consensus"
    __table_args__ = (
        UniqueConstraint(
            "sample_batch_id",
            "message_id",
            "policy_version",
            "input_digest",
            name="uq_validation_consensus_digest",
        ),
        Index("ix_validation_consensus_state", "state"),
        Index("ix_validation_consensus_batch_msg", "sample_batch_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sample_batch_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    validation_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale_code: Mapped[str] = mapped_column(String(64), nullable=False)
    n_blind_ok: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    n_true: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    n_false: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    n_uncertain: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    n_insufficient: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    n_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    distinct_providers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    distinct_families: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    modes_present: Mapped[str | None] = mapped_column(String(8), nullable=True)
    adjudicator_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lead_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_review_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    gate_eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


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
