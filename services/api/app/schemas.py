from datetime import datetime

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
    resolve_status: str
    last_error: str | None
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
    lead_type: str
    buyer_type: str
    status: str
    intent_score: float
    technical_score: float
    commercial_score: float
    promotion_score: float
    matched_keywords: list[str]
    matched_categories: list[str]
    reasons: list[str]
    contact_usernames: list[str]
    contact_urls: list[str]
    budget_amount: float | None
    budget_currency: str | None
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
    # Reporting vocabulary: lead rows ≠ opportunities
    lead_rows: int
    distinct_opportunities: int
    duplicate_opportunity_groups: int = 0
    scored_messages: int = 0
    persisted_negatives: int = 0
    persisted_positives: int = 0
    persisted_ambiguous: int = 0
    reviewed_labels: int = 0
    true_lead_labels: int = 0
    false_positive_labels: int = 0


_FP_CLASS_PATTERN = (
    "^(MARKETING_BROADCAST|JOB_VACANCY|SUPPORT_REQUEST|SERVICE_AD|"
    "JOB_SEEKER|NEWS_DIGEST|OFF_DOMAIN|DUPLICATE)$"
)


class LabelCreate(BaseModel):
    message_id: int  # internal messages.id
    label: str = Field(pattern="^(TRUE_LEAD|FALSE_POSITIVE|AMBIGUOUS)$")
    fp_class: str | None = Field(default=None, pattern=_FP_CLASS_PATTERN)
    commercially_actionable: bool | None = None
    language: str | None = Field(default=None, max_length=16)
    notes: str | None = None
    labeled_by: str = Field(default="human", max_length=64)


class LabelOut(BaseModel):
    id: int
    message_id: int
    community_id: int
    opportunity_key: str | None
    label: str
    fp_class: str | None
    commercially_actionable: bool | None
    language: str | None
    notes: str | None
    labeled_by: str
    labeled_at: datetime
    text: str | None = None
    community_username: str | None = None
    author_username: str | None = None
    scorer_tier: str | None = None
    scorer_decision: str | None = None


class LabelQueueItem(BaseModel):
    message_id: int
    community_id: int
    opportunity_key: str | None
    text: str
    community_username: str | None
    community_name: str | None
    author_username: str | None
    author_name: str | None
    message_url: str | None
    message_date: datetime | None
    scorer_score: float | None = None
    scorer_tier: str | None = None
    scorer_decision: str | None = None
    scorer_lead_type: str | None = None
    scorer_buyer_type: str | None = None
    lead_id: int | None = None


class LabelStatsOut(BaseModel):
    reviewed_labels: int
    true_lead: int
    false_positive: int
    ambiguous: int
    by_fp_class: dict[str, int]
    by_language: dict[str, int]
    commercially_actionable_true: int
    commercially_actionable_false: int
    ml_gate_m1_ready: bool  # ≥500 reviewed
    ml_gate_positive_ready: bool  # ≥150 TRUE_LEAD
    # Independent human review layer (label_reviews) — distinct from agent-provisional human_labels
    independent_reviews: int = 0
    independent_true: int = 0
    independent_false: int = 0
    independent_uncertain: int = 0
    agent_provisional_labels: int = 0
    ml_gate_independence_ready: bool = False  # ≥100 independent reviews with ≥30 TRUE


class IndependentReviewQueueItem(BaseModel):
    message_id: int
    community_id: int
    sample_batch_id: str
    # Real stratum only when blind=false; blind responses use "blinded"
    stratum: str
    text: str
    community_username: str | None
    community_name: str | None
    author_username: str | None
    message_url: str | None
    message_date: datetime | None
    # HMAC attestation binding reviewer + message + blind mode; required on POST.
    review_token: str | None = None
    # Only populated when blind=false
    scorer_score: float | None = None
    scorer_tier: str | None = None
    scorer_decision: str | None = None
    scorer_lead_type: str | None = None
    scorer_buyer_type: str | None = None
    prior_agent_label: str | None = None


# Independent-layer vocabulary (preferred) + legacy aliases accepted on write.
_INDEPENDENT_LABEL_PATTERN = (
    "^(HUMAN_REVIEWED_TRUE|HUMAN_REVIEWED_FALSE|HUMAN_REVIEWED_AMBIGUOUS|"
    "TRUE_LEAD|FALSE_POSITIVE|AMBIGUOUS|UNCERTAIN)$"
)


class IndependentReviewCreate(BaseModel):
    message_id: int
    sample_batch_id: str = Field(min_length=1, max_length=64)
    reviewer_id: str = Field(min_length=1, max_length=64)
    label: str = Field(pattern=_INDEPENDENT_LABEL_PATTERN)
    fp_class: str | None = Field(default=None, pattern=_FP_CLASS_PATTERN)
    commercially_actionable: bool | None = None
    # Deprecated client fields — ignored; blind flags come from review_token attestation.
    scorer_shown: bool = True
    prior_label_shown: bool = True
    review_token: str = Field(min_length=1, max_length=512)
    notes: str | None = None


class IndependentReviewOut(BaseModel):
    id: int
    message_id: int
    sample_batch_id: str | None
    reviewer_id: str
    label: str
    fp_class: str | None
    commercially_actionable: bool | None
    scorer_shown: bool
    prior_label_shown: bool
    notes: str | None
    reviewed_at: datetime

