import csv
import io
import json
import random

from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import engine, get_session
from shared.logging import configure_logging
from shared.models import (
    Author,
    Community,
    HumanLabel,
    LabelReview,
    LabelReviewSample,
    Lead,
    Message,
    MessageScore,
    ScanRun,
)

# Normalize legacy aliases into independent-review vocabulary on write.
_INDEPENDENT_LABEL_NORMALIZE = {
    "TRUE_LEAD": "HUMAN_REVIEWED_TRUE",
    "FALSE_POSITIVE": "HUMAN_REVIEWED_FALSE",
    "AMBIGUOUS": "HUMAN_REVIEWED_AMBIGUOUS",
    "UNCERTAIN": "HUMAN_REVIEWED_AMBIGUOUS",
    "HUMAN_REVIEWED_TRUE": "HUMAN_REVIEWED_TRUE",
    "HUMAN_REVIEWED_FALSE": "HUMAN_REVIEWED_FALSE",
    "HUMAN_REVIEWED_AMBIGUOUS": "HUMAN_REVIEWED_AMBIGUOUS",
}
_INDEPENDENT_TRUE = ("HUMAN_REVIEWED_TRUE", "TRUE_LEAD")
_INDEPENDENT_FALSE = ("HUMAN_REVIEWED_FALSE", "FALSE_POSITIVE")
_INDEPENDENT_UNCERTAIN = ("HUMAN_REVIEWED_AMBIGUOUS", "AMBIGUOUS", "UNCERTAIN")
from shared.opportunity import opportunity_key as make_opportunity_key
from shared.redis_bus import RedisBus, SCAN_STREAM
from shared.settings import get_settings
from services.api.app.schemas import (
    CommunityCreate,
    CommunityOut,
    HealthOut,
    IndependentReviewCreate,
    IndependentReviewOut,
    IndependentReviewQueueItem,
    LabelCreate,
    LabelOut,
    LabelQueueItem,
    LabelStatsOut,
    LeadOut,
    ScanOut,
    StatsOut,
)

settings = get_settings()
configure_logging(settings.log_level)
app = FastAPI(title="Telegram Lead Monitor API", version="0.2.0")


@app.on_event("shutdown")
async def shutdown() -> None:
    await engine.dispose()


@app.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    postgres = "ok"
    redis_status = "ok"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        postgres = "error"
    bus = RedisBus(settings.redis_url)
    try:
        await bus.redis.ping()
    except Exception:
        redis_status = "error"
    finally:
        await bus.close()
    status = "ok" if postgres == redis_status == "ok" else "degraded"
    return HealthOut(status=status, postgres=postgres, redis=redis_status)


@app.post("/communities", response_model=CommunityOut, status_code=201)
async def add_community(payload: CommunityCreate, session: AsyncSession = Depends(get_session)) -> CommunityOut:
    existing = await session.scalar(select(Community).where(Community.telegram_ref == payload.telegram_ref))
    if existing:
        existing.enabled = payload.enabled
        await session.commit()
        await session.refresh(existing)
        return CommunityOut.model_validate(existing)
    community = Community(telegram_ref=payload.telegram_ref, enabled=payload.enabled)
    session.add(community)
    await session.commit()
    await session.refresh(community)
    return CommunityOut.model_validate(community)


@app.get("/communities", response_model=list[CommunityOut])
async def list_communities(session: AsyncSession = Depends(get_session)) -> list[CommunityOut]:
    rows = (await session.scalars(select(Community).order_by(Community.id))).all()
    return [CommunityOut.model_validate(row) for row in rows]


@app.patch("/communities/{community_id}/enabled", response_model=CommunityOut)
async def set_community_enabled(
    community_id: int,
    enabled: bool,
    session: AsyncSession = Depends(get_session),
) -> CommunityOut:
    community = await session.get(Community, community_id)
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")
    community.enabled = enabled
    await session.commit()
    await session.refresh(community)
    return CommunityOut.model_validate(community)


@app.post("/scans", response_model=ScanOut, status_code=202)
async def request_scan(
    days: int = Query(default=settings.scan_default_days, ge=1, le=365),
    community_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> ScanOut:
    if community_id is not None and not await session.get(Community, community_id):
        raise HTTPException(status_code=404, detail="Community not found")
    query = select(func.count(Community.id)).where(Community.enabled.is_(True))
    if community_id:
        query = query.where(Community.id == community_id)
    count = int((await session.scalar(query)) or 0)
    if count == 0:
        raise HTTPException(status_code=400, detail="No enabled communities selected")

    # Mirror scheduler dedupe: do not pile queued/running scans for the same scope.
    if community_id is None:
        existing = await session.scalar(
            select(ScanRun.id).where(
                ScanRun.community_id.is_(None),
                ScanRun.status.in_(["queued", "running"]),
            ).limit(1)
        )
    else:
        existing = await session.scalar(
            select(ScanRun.id).where(
                ScanRun.community_id == community_id,
                ScanRun.status.in_(["queued", "running"]),
            ).limit(1)
        )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A scan is already queued or running for this scope",
        )

    run = ScanRun(community_id=community_id, days=days, status="queued")
    session.add(run)
    await session.commit()
    await session.refresh(run)
    bus = RedisBus(settings.redis_url)
    try:
        await bus.publish(SCAN_STREAM, {"run_id": run.id, "community_id": community_id, "days": days})
    finally:
        await bus.close()
    return ScanOut(run_id=run.id, status=run.status, communities=count, days=days)


@app.get("/scans")
async def list_scans(
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.scalars(select(ScanRun).order_by(ScanRun.id.desc()).limit(limit))).all()
    return [
        {
            "id": row.id,
            "community_id": row.community_id,
            "days": row.days,
            "status": row.status,
            "requested_at": row.requested_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "messages_seen": row.messages_seen,
            "messages_published": row.messages_published,
            "error": row.error,
        }
        for row in rows
    ]


@app.get("/leads", response_model=list[LeadOut])
async def list_leads(
    min_score: float = Query(default=50, ge=0, le=100),
    tier: str | None = Query(default=None),
    lead_type: str | None = Query(default=None),
    buyer_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    since_days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[LeadOut]:
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    stmt = (
        select(Lead, Message, Community, Author)
        .join(Message, Lead.message_id == Message.id)
        .join(Community, Message.community_id == Community.id)
        .outerjoin(Author, Message.author_id == Author.id)
        .where(Lead.score >= min_score, Message.message_date >= since)
        .order_by(Lead.score.desc(), Message.message_date.desc())
        .limit(limit)
    )
    if tier:
        stmt = stmt.where(Lead.tier == tier.upper())
    if lead_type:
        stmt = stmt.where(Lead.lead_type == lead_type.upper())
    if buyer_type:
        stmt = stmt.where(Lead.buyer_type == buyer_type.upper())
    if status:
        stmt = stmt.where(Lead.status == status.upper())
    results = (await session.execute(stmt)).all()
    return [_lead_out(lead, message, community, author) for lead, message, community, author in results]


def _lead_out(lead: Lead, message: Message, community: Community, author: Author | None) -> LeadOut:
    return LeadOut(
        id=lead.id,
        score=lead.score,
        tier=lead.tier,
        lead_type=lead.lead_type,
        buyer_type=lead.buyer_type,
        status=lead.status,
        intent_score=lead.intent_score,
        technical_score=lead.technical_score,
        commercial_score=lead.commercial_score,
        promotion_score=lead.promotion_score,
        matched_keywords=json.loads(lead.matched_keywords or "[]"),
        matched_categories=json.loads(lead.matched_categories or "[]"),
        reasons=json.loads(lead.reasons or "[]"),
        contact_usernames=json.loads(lead.contact_usernames or "[]"),
        contact_urls=json.loads(lead.contact_urls or "[]"),
        budget_amount=lead.budget_amount,
        budget_currency=lead.budget_currency,
        semantic_score=lead.semantic_score,
        message_id=message.telegram_message_id,
        message_url=message.message_url,
        message_date=message.message_date,
        text=message.text,
        community_name=community.name,
        community_username=community.username,
        community_url=community.url,
        author_id=author.telegram_id if author else None,
        author_username=author.username if author else None,
        author_url=(f"https://t.me/{author.username}" if author and author.username else None),
        author_name=author.name if author else None,
        author_bio=author.bio if author else None,
    )


@app.patch("/leads/{lead_id}/status")
async def update_lead_status(
    lead_id: int,
    status: str = Query(pattern="^(NEW|REVIEWED|CONTACTED|RESPONDED|QUALIFIED|REJECTED|WON|LOST)$"),
    session: AsyncSession = Depends(get_session),
):
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.status = status.upper()
    await session.commit()
    return {"id": lead.id, "status": lead.status}


@app.get("/stats", response_model=StatsOut)
async def stats(session: AsyncSession = Depends(get_session)) -> StatsOut:
    communities = int((await session.scalar(select(func.count(Community.id)))) or 0)
    messages = int((await session.scalar(select(func.count(Message.id)))) or 0)
    leads = int((await session.scalar(select(func.count(Lead.id)))) or 0)
    high = int((await session.scalar(select(func.count(Lead.id)).where(Lead.tier == "HIGH"))) or 0)
    distinct_opportunities = int(
        (await session.scalar(select(func.count(func.distinct(Lead.opportunity_key))))) or 0
    )
    unkeyed_leads = int(
        (await session.scalar(select(func.count(Lead.id)).where(Lead.opportunity_key.is_(None)))) or 0
    )
    # After migration + unique constraint this should be 0; kept for monitoring.
    dup_groups = int(
        (
            await session.scalar(
                text(
                    """
                    SELECT COUNT(*) FROM (
                      SELECT opportunity_key
                      FROM leads
                      WHERE opportunity_key IS NOT NULL
                      GROUP BY opportunity_key
                      HAVING COUNT(*) > 1
                    ) t
                    """
                )
            )
        )
        or 0
    )
    scored_messages = int((await session.scalar(select(func.count(MessageScore.id)))) or 0)
    persisted_negatives = int(
        (await session.scalar(select(func.count(MessageScore.id)).where(MessageScore.decision == "NEGATIVE")))
        or 0
    )
    persisted_positives = int(
        (await session.scalar(select(func.count(MessageScore.id)).where(MessageScore.decision == "POSITIVE")))
        or 0
    )
    persisted_ambiguous = int(
        (await session.scalar(select(func.count(MessageScore.id)).where(MessageScore.decision == "AMBIGUOUS")))
        or 0
    )
    reviewed_labels = int((await session.scalar(select(func.count(HumanLabel.id)))) or 0)
    true_lead_labels = int(
        (await session.scalar(select(func.count(HumanLabel.id)).where(HumanLabel.label == "TRUE_LEAD"))) or 0
    )
    false_positive_labels = int(
        (await session.scalar(select(func.count(HumanLabel.id)).where(HumanLabel.label == "FALSE_POSITIVE")))
        or 0
    )
    return StatsOut(
        communities=communities,
        messages=messages,
        leads=leads,
        high_leads=high,
        lead_rows=leads,
        distinct_opportunities=distinct_opportunities,
        duplicate_opportunity_groups=dup_groups,
        scored_messages=scored_messages,
        persisted_negatives=persisted_negatives,
        persisted_positives=persisted_positives,
        persisted_ambiguous=persisted_ambiguous,
        reviewed_labels=reviewed_labels,
        true_lead_labels=true_lead_labels,
        false_positive_labels=false_positive_labels,
    )


@app.get("/labels/queue", response_model=list[LabelQueueItem])
async def label_queue(
    source: str = Query(default="leads", pattern="^(leads|scores|negatives)$"),
    limit: int = Query(default=25, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[LabelQueueItem]:
    """Return unlabeled examples for human review.

    - leads: current CRM lead opportunities (highest priority)
    - scores: any persisted score rows
    - negatives: persisted NEGATIVE decisions (for FP/TN balance)
    """
    labeled_ids = select(HumanLabel.message_id)
    items: list[LabelQueueItem] = []

    if source == "leads":
        stmt = (
            select(Lead, Message, Community, Author, MessageScore)
            .join(Message, Lead.message_id == Message.id)
            .join(Community, Message.community_id == Community.id)
            .outerjoin(Author, Message.author_id == Author.id)
            .outerjoin(MessageScore, MessageScore.message_id == Message.id)
            .where(Lead.message_id.not_in(labeled_ids))
            .order_by(Lead.score.desc(), Lead.created_at.desc())
            .limit(limit)
        )
        for lead, message, community, author, score_row in (await session.execute(stmt)).all():
            items.append(
                LabelQueueItem(
                    message_id=message.id,
                    community_id=message.community_id,
                    opportunity_key=lead.opportunity_key,
                    text=message.text,
                    community_username=community.username,
                    community_name=community.name,
                    author_username=author.username if author else None,
                    author_name=author.name if author else None,
                    message_url=message.message_url,
                    message_date=message.message_date,
                    scorer_score=lead.score,
                    scorer_tier=lead.tier,
                    scorer_decision=score_row.decision if score_row else None,
                    scorer_lead_type=lead.lead_type,
                    scorer_buyer_type=lead.buyer_type,
                    lead_id=lead.id,
                )
            )
        return items

    decision_filter = None
    if source == "negatives":
        decision_filter = MessageScore.decision == "NEGATIVE"

    stmt = (
        select(MessageScore, Message, Community, Author, Lead)
        .join(Message, MessageScore.message_id == Message.id)
        .join(Community, Message.community_id == Community.id)
        .outerjoin(Author, Message.author_id == Author.id)
        .outerjoin(Lead, Lead.message_id == Message.id)
        .where(MessageScore.message_id.not_in(labeled_ids))
        .order_by(MessageScore.scored_at.desc())
        .limit(limit)
    )
    if decision_filter is not None:
        stmt = stmt.where(decision_filter)

    for score_row, message, community, author, lead in (await session.execute(stmt)).all():
        items.append(
            LabelQueueItem(
                message_id=message.id,
                community_id=message.community_id,
                opportunity_key=lead.opportunity_key if lead else make_opportunity_key(message.community_id, message.text),
                text=message.text,
                community_username=community.username,
                community_name=community.name,
                author_username=author.username if author else None,
                author_name=author.name if author else None,
                message_url=message.message_url,
                message_date=message.message_date,
                scorer_score=score_row.score,
                scorer_tier=score_row.tier,
                scorer_decision=score_row.decision,
                scorer_lead_type=score_row.lead_type,
                scorer_buyer_type=score_row.buyer_type,
                lead_id=lead.id if lead else None,
            )
        )
    return items


@app.post("/labels", response_model=LabelOut, status_code=201)
async def create_label(payload: LabelCreate, session: AsyncSession = Depends(get_session)) -> LabelOut:
    message = await session.get(Message, payload.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if payload.label == "FALSE_POSITIVE" and not payload.fp_class:
        raise HTTPException(status_code=400, detail="fp_class required when label=FALSE_POSITIVE")
    if payload.label == "TRUE_LEAD" and payload.fp_class:
        raise HTTPException(status_code=400, detail="fp_class must be null when label=TRUE_LEAD")

    existing = await session.scalar(select(HumanLabel).where(HumanLabel.message_id == message.id))
    lead = await session.scalar(select(Lead).where(Lead.message_id == message.id))
    key = (lead.opportunity_key if lead else None) or make_opportunity_key(message.community_id, message.text)

    if existing:
        row = existing
    else:
        row = HumanLabel(message_id=message.id, community_id=message.community_id)
        session.add(row)

    row.community_id = message.community_id
    row.opportunity_key = key
    row.label = payload.label
    row.fp_class = payload.fp_class
    row.commercially_actionable = (
        payload.commercially_actionable
        if payload.commercially_actionable is not None
        else (True if payload.label == "TRUE_LEAD" else False if payload.label == "FALSE_POSITIVE" else None)
    )
    row.language = payload.language
    row.notes = payload.notes
    row.labeled_by = payload.labeled_by or "human"
    row.labeled_at = datetime.now(timezone.utc)

    if lead and lead.status == "NEW":
        lead.status = "REVIEWED"

    score_row = await session.scalar(select(MessageScore).where(MessageScore.message_id == message.id))
    community = await session.get(Community, message.community_id)
    author = await session.get(Author, message.author_id) if message.author_id else None
    await session.commit()
    await session.refresh(row)

    return LabelOut(
        id=row.id,
        message_id=row.message_id,
        community_id=row.community_id,
        opportunity_key=row.opportunity_key,
        label=row.label,
        fp_class=row.fp_class,
        commercially_actionable=row.commercially_actionable,
        language=row.language,
        notes=row.notes,
        labeled_by=row.labeled_by,
        labeled_at=row.labeled_at,
        text=message.text,
        community_username=community.username if community else None,
        author_username=author.username if author else None,
        scorer_tier=score_row.tier if score_row else (lead.tier if lead else None),
        scorer_decision=score_row.decision if score_row else None,
    )


@app.get("/labels/stats", response_model=LabelStatsOut)
async def label_stats(session: AsyncSession = Depends(get_session)) -> LabelStatsOut:
    reviewed = int((await session.scalar(select(func.count(HumanLabel.id)))) or 0)
    true_lead = int(
        (await session.scalar(select(func.count(HumanLabel.id)).where(HumanLabel.label == "TRUE_LEAD"))) or 0
    )
    false_positive = int(
        (await session.scalar(select(func.count(HumanLabel.id)).where(HumanLabel.label == "FALSE_POSITIVE"))) or 0
    )
    ambiguous = int(
        (await session.scalar(select(func.count(HumanLabel.id)).where(HumanLabel.label == "AMBIGUOUS"))) or 0
    )
    fp_rows = (
        await session.execute(
            select(HumanLabel.fp_class, func.count(HumanLabel.id))
            .where(HumanLabel.fp_class.is_not(None))
            .group_by(HumanLabel.fp_class)
        )
    ).all()
    lang_rows = (
        await session.execute(
            select(HumanLabel.language, func.count(HumanLabel.id))
            .where(HumanLabel.language.is_not(None))
            .group_by(HumanLabel.language)
        )
    ).all()
    actionable_true = int(
        (
            await session.scalar(
                select(func.count(HumanLabel.id)).where(HumanLabel.commercially_actionable.is_(True))
            )
        )
        or 0
    )
    actionable_false = int(
        (
            await session.scalar(
                select(func.count(HumanLabel.id)).where(HumanLabel.commercially_actionable.is_(False))
            )
        )
        or 0
    )
    independent_reviews = int((await session.scalar(select(func.count(LabelReview.id)))) or 0)
    independent_true = int(
        (
            await session.scalar(
                select(func.count(LabelReview.id)).where(LabelReview.label.in_(_INDEPENDENT_TRUE))
            )
        )
        or 0
    )
    independent_false = int(
        (
            await session.scalar(
                select(func.count(LabelReview.id)).where(LabelReview.label.in_(_INDEPENDENT_FALSE))
            )
        )
        or 0
    )
    independent_uncertain = int(
        (
            await session.scalar(
                select(func.count(LabelReview.id)).where(LabelReview.label.in_(_INDEPENDENT_UNCERTAIN))
            )
        )
        or 0
    )
    agent_provisional = int(
        (
            await session.scalar(
                select(func.count(HumanLabel.id)).where(
                    HumanLabel.labeled_by.like("agent%") | HumanLabel.labeled_by.like("audit%")
                )
            )
        )
        or 0
    )
    return LabelStatsOut(
        reviewed_labels=reviewed,
        true_lead=true_lead,
        false_positive=false_positive,
        ambiguous=ambiguous,
        by_fp_class={str(k): int(v) for k, v in fp_rows if k},
        by_language={str(k): int(v) for k, v in lang_rows if k},
        commercially_actionable_true=actionable_true,
        commercially_actionable_false=actionable_false,
        ml_gate_m1_ready=reviewed >= 500,
        ml_gate_positive_ready=true_lead >= 150,
        independent_reviews=independent_reviews,
        independent_true=independent_true,
        independent_false=independent_false,
        independent_uncertain=independent_uncertain,
        agent_provisional_labels=agent_provisional,
        ml_gate_independence_ready=independent_reviews >= 100 and independent_true >= 30,
    )


@app.get("/labels/independent-queue", response_model=list[IndependentReviewQueueItem])
async def independent_review_queue(
    sample_batch_id: str = Query(..., min_length=1, max_length=64),
    reviewer_id: str = Query(default="human", max_length=64),
    blind: bool = Query(default=True, description="Hide scorer + prior agent labels when true"),
    stratum: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=25, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[IndependentReviewQueueItem]:
    """Queue items from a stratified sample for independent review.

    Does not mutate human_labels. Prefer blind=true for evaluation samples.
    """
    already = select(LabelReview.message_id).where(
        LabelReview.reviewer_id == reviewer_id,
        LabelReview.sample_batch_id == sample_batch_id,
    )
    # Blind stratum filter still works server-side; response never reveals real stratum.
    stmt = (
        select(LabelReviewSample, Message, Community, Author, MessageScore, Lead, HumanLabel)
        .join(Message, LabelReviewSample.message_id == Message.id)
        .join(Community, Message.community_id == Community.id)
        .outerjoin(Author, Message.author_id == Author.id)
        .outerjoin(MessageScore, MessageScore.message_id == Message.id)
        .outerjoin(Lead, Lead.message_id == Message.id)
        .outerjoin(HumanLabel, HumanLabel.message_id == Message.id)
        .where(LabelReviewSample.sample_batch_id == sample_batch_id)
        .where(LabelReviewSample.message_id.not_in(already))
        .order_by(LabelReviewSample.id.asc())
    )
    if stratum:
        stmt = stmt.where(LabelReviewSample.stratum == stratum)

    rows = list((await session.execute(stmt)).all())
    # Blind mode: shuffle so stratum-block ordering cannot leak the sampling key.
    if blind:
        random.shuffle(rows)
    rows = rows[:limit]

    items: list[IndependentReviewQueueItem] = []
    for sample, message, community, author, score_row, lead, prior in rows:
        items.append(
            IndependentReviewQueueItem(
                message_id=message.id,
                community_id=message.community_id,
                sample_batch_id=sample.sample_batch_id,
                stratum="blinded" if blind else sample.stratum,
                text=message.text,
                community_username=community.username,
                community_name=community.name,
                author_username=author.username if author else None,
                message_url=message.message_url,
                message_date=message.message_date,
                scorer_score=None if blind else (score_row.score if score_row else (lead.score if lead else None)),
                scorer_tier=None if blind else (score_row.tier if score_row else (lead.tier if lead else None)),
                scorer_decision=None if blind else (score_row.decision if score_row else None),
                scorer_lead_type=None
                if blind
                else (score_row.lead_type if score_row else (lead.lead_type if lead else None)),
                scorer_buyer_type=None
                if blind
                else (score_row.buyer_type if score_row else (lead.buyer_type if lead else None)),
                prior_agent_label=None if blind else (prior.label if prior else None),
            )
        )
    return items


@app.post("/labels/reviews", response_model=IndependentReviewOut, status_code=201)
async def create_independent_review(
    payload: IndependentReviewCreate,
    session: AsyncSession = Depends(get_session),
) -> IndependentReviewOut:
    """Append an independent human review without overwriting human_labels."""
    message = await session.get(Message, payload.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    label = _INDEPENDENT_LABEL_NORMALIZE.get(payload.label, payload.label)
    if label == "HUMAN_REVIEWED_FALSE" and not payload.fp_class:
        raise HTTPException(status_code=400, detail="fp_class required when label is FALSE")
    if label == "HUMAN_REVIEWED_TRUE" and payload.fp_class:
        raise HTTPException(status_code=400, detail="fp_class must be null when label is TRUE")

    existing = await session.scalar(
        select(LabelReview).where(
            LabelReview.message_id == payload.message_id,
            LabelReview.reviewer_id == payload.reviewer_id,
            LabelReview.sample_batch_id == payload.sample_batch_id,
        )
    )
    row = existing or LabelReview(
        message_id=payload.message_id,
        reviewer_id=payload.reviewer_id,
        sample_batch_id=payload.sample_batch_id,
    )
    if not existing:
        session.add(row)
    row.label = label
    row.fp_class = payload.fp_class
    row.commercially_actionable = payload.commercially_actionable
    row.scorer_shown = payload.scorer_shown
    row.prior_label_shown = payload.prior_label_shown
    row.notes = payload.notes
    row.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(row)
    return IndependentReviewOut(
        id=row.id,
        message_id=row.message_id,
        sample_batch_id=row.sample_batch_id,
        reviewer_id=row.reviewer_id,
        label=row.label,
        fp_class=row.fp_class,
        commercially_actionable=row.commercially_actionable,
        scorer_shown=row.scorer_shown,
        prior_label_shown=row.prior_label_shown,
        notes=row.notes,
        reviewed_at=row.reviewed_at,
    )


@app.get("/search")
async def search_messages(
    q: str = Query(min_length=2, max_length=200),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Message, Community, Author)
        .join(Community, Message.community_id == Community.id)
        .outerjoin(Author, Message.author_id == Author.id)
        .where(Message.text.ilike(f"%{q}%"))
        .order_by(Message.message_date.desc())
        .limit(limit)
    )
    results = (await session.execute(stmt)).all()
    return [
        {
            "message_id": m.telegram_message_id,
            "message_url": m.message_url,
            "message_date": m.message_date,
            "text": m.text,
            "community": c.name,
            "community_url": c.url,
            "author_id": a.telegram_id if a else None,
            "author_username": a.username if a else None,
            "author_url": (f"https://t.me/{a.username}" if a and a.username else None),
            "author_name": a.name if a else None,
            "author_bio": a.bio if a else None,
        }
        for m, c, a in results
    ]


async def _export_rows(session: AsyncSession, min_score: float, since_days: int) -> list[dict]:
    leads = await list_leads(
        min_score=min_score,
        since_days=since_days,
        limit=1000,
        session=session,
    )
    return [lead.model_dump() for lead in leads]


@app.get("/exports/leads.xlsx")
async def export_xlsx(
    min_score: float = Query(default=50, ge=0, le=100),
    since_days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    import pandas as pd

    rows = await _export_rows(session, min_score, since_days)
    frame = pd.DataFrame(rows)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Leads")
    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=telegram_leads.xlsx"},
    )


@app.get("/exports/leads.csv")
async def export_csv(
    min_score: float = Query(default=50, ge=0, le=100),
    since_days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    rows = await _export_rows(session, min_score, since_days)
    if rows:
        fieldnames = list(rows[0].keys())
    else:
        fieldnames = list(LeadOut.model_fields.keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=telegram_leads.csv"},
    )
