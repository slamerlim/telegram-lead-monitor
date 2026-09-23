"""Commercial operator API — human qualification, contact, outcomes.

AI validation remains advisory. This router never writes AI/independence gates.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import get_session
from shared.independent_review_auth import parse_id_csv, reserved_id_match
from shared.lead_fsm import TransitionError, resolve_event_transition
from shared.lead_write import promote_candidate_lead
from shared.models import (
    Author,
    Community,
    HumanLabel,
    LabelReview,
    LabelReviewSample,
    Lead,
    LeadEvent,
    Message,
    MessageScore,
)
from shared.opportunity import opportunity_key as make_opportunity_key
from shared.settings import get_settings
from services.api.app.schemas import (
    OpsCandidateOut,
    OpsEventIn,
    OpsEventOut,
    OpsFunnelOut,
    OpsLeadOut,
    OpsMilestoneOut,
    OpsPromoteIn,
)

logger = logging.getLogger("api.ops")

router = APIRouter(prefix="/ops", tags=["commercial-ops"])

_OPS_RESERVED_PREFIXES = (
    "agent",
    "audit",
    "blind_adjudicator",
    "smoke",
    "phaseD_smoke",
    "prod_phase0",
    "aival",
    "migration",
    "system",
)


def _filter_ops_ids(ids: set[str]) -> set[str]:
    return {i for i in ids if not reserved_id_match(i, _OPS_RESERVED_PREFIXES)}


def _require_ops_enabled() -> None:
    if not get_settings().commercial_ops_enabled:
        raise HTTPException(status_code=404, detail="commercial ops disabled")


def _require_operator(
    x_operator_id: str | None = Header(default=None, alias="X-Operator-Id"),
    x_label_token: str | None = Header(default=None, alias="X-Label-Token"),
) -> str:
    """Fail-closed commercial operator auth (shared LABEL_WRITE_TOKEN + allowlist)."""
    _require_ops_enabled()
    cfg = get_settings()
    expected = (cfg.label_write_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="LABEL_WRITE_TOKEN not configured; commercial ops refuse writes",
        )
    if not x_label_token or not secrets.compare_digest(x_label_token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-Label-Token")

    op_id = (x_operator_id or "").strip()
    if not op_id:
        raise HTTPException(status_code=400, detail="X-Operator-Id required")
    if reserved_id_match(op_id, _OPS_RESERVED_PREFIXES):
        raise HTTPException(status_code=403, detail="reserved operator id")

    allow = _filter_ops_ids(parse_id_csv(cfg.human_label_reviewer_ids))
    if not allow:
        raise HTTPException(
            status_code=503,
            detail="HUMAN_LABEL_REVIEWER_IDS empty; commercial ops refuse writes",
        )
    if op_id not in allow:
        raise HTTPException(status_code=403, detail="operator not allowlisted")

    # Keep blind reviewers out of scorer-visible commercial ops.
    from shared.independent_review_auth import parse_reviewer_tokens

    blind_tokens = parse_reviewer_tokens(cfg.independent_human_reviewer_tokens)
    blind_ids = parse_id_csv(cfg.independent_human_reviewer_ids)
    blind_allow = _filter_ops_ids(
        set(blind_tokens.keys()) if not blind_ids else (blind_ids & set(blind_tokens.keys()))
    )
    if op_id in blind_allow:
        raise HTTPException(
            status_code=409,
            detail="operator/blind-reviewer role conflict",
        )
    return op_id


def _loads_list(raw: str | None) -> list:
    try:
        val = json.loads(raw or "[]")
        return val if isinstance(val, list) else []
    except Exception:
        return []


def _lead_to_ops(
    lead: Lead,
    message: Message,
    community: Community | None,
    author: Author | None,
    *,
    ai_hint: str | None = None,
) -> OpsLeadOut:
    return OpsLeadOut(
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
        matched_keywords=_loads_list(lead.matched_keywords),
        matched_categories=_loads_list(lead.matched_categories),
        reasons=_loads_list(lead.reasons),
        contact_usernames=_loads_list(lead.contact_usernames),
        contact_urls=_loads_list(lead.contact_urls),
        budget_amount=lead.budget_amount,
        budget_currency=lead.budget_currency,
        semantic_score=lead.semantic_score,
        message_id=message.id,
        message_url=message.message_url,
        message_date=message.message_date,
        text=message.text,
        community_name=community.name if community else None,
        community_username=community.username if community else None,
        community_url=community.url if community else None,
        author_id=author.id if author else None,
        author_username=author.username if author else None,
        author_url=(f"https://t.me/{author.username}" if author and author.username else None),
        author_name=author.name if author else None,
        author_bio=author.bio if author else None,
        source=getattr(lead, "source", None) or "scorer",
        owner=getattr(lead, "owner", None),
        next_action_at=getattr(lead, "next_action_at", None),
        opportunity_key=lead.opportunity_key,
        ai_hint=ai_hint,
    )


async def _ai_hint_for_message(session: AsyncSession, message_id: int) -> str | None:
    row = await session.execute(
        select(LabelReview.label)
        .where(
            LabelReview.message_id == message_id,
            LabelReview.validator_kind == "ai",
            LabelReview.label == "AI_TRUE",
        )
        .limit(1)
    )
    if row.scalar_one_or_none():
        return "AI_TRUE"
    return None


@router.get("/health")
async def ops_health(operator_id: str = Depends(_require_operator)):
    return {
        "status": "ok",
        "commercial_ops_enabled": True,
        "operator_id": operator_id,
        "ai_advisory_only": True,
        "ml_training_enabled": False,
    }


@router.get("/candidates", response_model=list[OpsCandidateOut])
async def list_candidates(
    source: str = Query(default="scorer", pattern="^(scorer|agent_provisional|all)$"),
    since_days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    operator_id: str = Depends(_require_operator),
):
    """Triage queue: scorer HIGH/MEDIUM NEW leads and/or agent_provisional TRUE_LEAD."""
    del operator_id  # auth side-effect
    cfg = get_settings()
    max_age = min(since_days, cfg.ops_candidate_max_age_days)
    since = datetime.now(timezone.utc) - timedelta(days=max_age)

    blind_sample_ids = select(LabelReviewSample.message_id)

    out: list[OpsCandidateOut] = []

    if source in ("scorer", "all"):
        q = (
            select(Lead, Message, Community, Author)
            .join(Message, Message.id == Lead.message_id)
            .join(Community, Community.id == Message.community_id)
            .outerjoin(Author, Author.id == Message.author_id)
            .where(
                Lead.status == "NEW",
                Lead.tier.in_(("HIGH", "MEDIUM")),
                Message.message_date >= since,
                ~Lead.message_id.in_(blind_sample_ids),
            )
            .order_by(Lead.score.desc(), Message.message_date.desc())
            .limit(limit)
        )
        rows = (await session.execute(q)).all()
        for lead, msg, community, author in rows:
            hint = await _ai_hint_for_message(session, msg.id)
            out.append(
                OpsCandidateOut(
                    message_id=msg.id,
                    text=msg.text,
                    message_url=msg.message_url,
                    message_date=msg.message_date,
                    community_name=community.name if community else None,
                    community_username=community.username if community else None,
                    author_username=author.username if author else None,
                    author_url=(
                        f"https://t.me/{author.username}" if author and author.username else None
                    ),
                    score=lead.score,
                    tier=lead.tier,
                    lead_type=lead.lead_type,
                    buyer_type=lead.buyer_type,
                    candidate_source="scorer",
                    ai_hint=hint,
                    existing_lead_id=lead.id,
                    existing_lead_status=lead.status,
                )
            )

    if source in ("agent_provisional", "all") and len(out) < limit:
        remaining = limit - len(out)
        q2 = (
            select(HumanLabel, Message, Community, Author, Lead)
            .join(Message, Message.id == HumanLabel.message_id)
            .join(Community, Community.id == Message.community_id)
            .outerjoin(Author, Author.id == Message.author_id)
            .outerjoin(Lead, Lead.message_id == Message.id)
            .where(
                HumanLabel.label == "TRUE_LEAD",
                HumanLabel.labeled_at >= since,
                or_(
                    HumanLabel.labeled_by.ilike("agent%"),
                    HumanLabel.labeled_by.ilike("audit%"),
                ),
                ~HumanLabel.message_id.in_(blind_sample_ids),
                or_(Lead.id.is_(None), Lead.status == "NEW"),
            )
            .order_by(HumanLabel.labeled_at.desc())
            .limit(remaining)
        )
        rows2 = (await session.execute(q2)).all()
        seen = {c.message_id for c in out}
        for hl, msg, community, author, lead in rows2:
            if msg.id in seen:
                continue
            hint = await _ai_hint_for_message(session, msg.id)
            out.append(
                OpsCandidateOut(
                    message_id=msg.id,
                    text=msg.text,
                    message_url=msg.message_url,
                    message_date=msg.message_date,
                    community_name=community.name if community else None,
                    community_username=community.username if community else None,
                    author_username=author.username if author else None,
                    author_url=(
                        f"https://t.me/{author.username}" if author and author.username else None
                    ),
                    score=lead.score if lead else None,
                    tier=lead.tier if lead else None,
                    lead_type=lead.lead_type if lead else None,
                    buyer_type=lead.buyer_type if lead else None,
                    candidate_source="agent_provisional",
                    ai_hint=hint,
                    existing_lead_id=lead.id if lead else None,
                    existing_lead_status=lead.status if lead else None,
                )
            )

    # Prefer AI_TRUE hints first, then score.
    out.sort(key=lambda c: (0 if c.ai_hint == "AI_TRUE" else 1, -(c.score or 0)))
    return out[:limit]


@router.post("/leads/promote", response_model=OpsLeadOut)
async def promote_lead(
    body: OpsPromoteIn,
    session: AsyncSession = Depends(get_session),
    operator_id: str = Depends(_require_operator),
):
    message = await session.get(Message, body.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="message not found")

    # Never promote blind-sample messages into open CRM.
    in_sample = await session.scalar(
        select(LabelReviewSample.id).where(LabelReviewSample.message_id == message.id).limit(1)
    )
    if in_sample:
        raise HTTPException(status_code=409, detail="message is in independent review sample")

    score_row = await session.scalar(
        select(MessageScore).where(MessageScore.message_id == message.id)
    )
    snapshot = None
    if score_row:
        snapshot = {
            "score": score_row.score,
            "tier": score_row.tier,
            "lead_type": score_row.lead_type,
            "buyer_type": score_row.buyer_type,
            "intent_score": score_row.intent_score,
            "technical_score": score_row.technical_score,
            "commercial_score": score_row.commercial_score,
            "promotion_score": score_row.promotion_score,
            "matched_keywords": score_row.matched_keywords,
            "matched_categories": score_row.matched_categories,
            "reasons": score_row.reasons,
            "semantic_score": score_row.semantic_score,
        }

    try:
        _action, lead = await promote_candidate_lead(
            session,
            message,
            source=body.candidate_source,
            actor_id=operator_id,
            score_snapshot=snapshot,
            status=body.to_status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    session.add(
        LeadEvent(
            lead_id=lead.id,
            message_id=message.id,
            opportunity_key=lead.opportunity_key,
            event_type="PROMOTE",
            from_status="NEW",
            to_status=lead.status,
            actor_id=operator_id,
            reason_code=body.reason_code or "operator_promote",
            note=body.note,
            occurred_at=now,
            created_at=now,
        )
    )
    await session.commit()
    await session.refresh(lead)

    community = await session.get(Community, message.community_id)
    author = await session.get(Author, message.author_id) if message.author_id else None
    hint = await _ai_hint_for_message(session, message.id)
    return _lead_to_ops(lead, message, community, author, ai_hint=hint)


@router.get("/leads", response_model=list[OpsLeadOut])
async def list_ops_leads(
    status: str | None = Query(default=None),
    overdue: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    operator_id: str = Depends(_require_operator),
):
    del operator_id
    q = (
        select(Lead, Message, Community, Author)
        .join(Message, Message.id == Lead.message_id)
        .join(Community, Community.id == Message.community_id)
        .outerjoin(Author, Author.id == Message.author_id)
    )
    if status:
        q = q.where(Lead.status == status.upper())
    if overdue:
        now = datetime.now(timezone.utc)
        q = q.where(
            Lead.next_action_at.is_not(None),
            Lead.next_action_at < now,
            Lead.status.in_(("QUALIFIED", "CONTACTED", "RESPONDED")),
        )
    q = q.order_by(Lead.score.desc()).limit(limit)
    rows = (await session.execute(q)).all()
    out = []
    for lead, msg, community, author in rows:
        hint = await _ai_hint_for_message(session, msg.id)
        out.append(_lead_to_ops(lead, msg, community, author, ai_hint=hint))
    return out


@router.get("/leads/{lead_id}", response_model=OpsLeadOut)
async def get_ops_lead(
    lead_id: int,
    session: AsyncSession = Depends(get_session),
    operator_id: str = Depends(_require_operator),
):
    del operator_id
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")
    message = await session.get(Message, lead.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="message missing")
    community = await session.get(Community, message.community_id)
    author = await session.get(Author, message.author_id) if message.author_id else None
    hint = await _ai_hint_for_message(session, message.id)
    return _lead_to_ops(lead, message, community, author, ai_hint=hint)


@router.get("/leads/{lead_id}/events", response_model=list[OpsEventOut])
async def list_lead_events(
    lead_id: int,
    session: AsyncSession = Depends(get_session),
    operator_id: str = Depends(_require_operator),
):
    del operator_id
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")
    rows = (
        await session.scalars(
            select(LeadEvent)
            .where(LeadEvent.lead_id == lead_id)
            .order_by(LeadEvent.occurred_at.asc(), LeadEvent.id.asc())
        )
    ).all()
    return [OpsEventOut.model_validate(r) for r in rows]


@router.post("/leads/{lead_id}/events", response_model=OpsEventOut)
async def post_lead_event(
    lead_id: int,
    body: OpsEventIn,
    session: AsyncSession = Depends(get_session),
    operator_id: str = Depends(_require_operator),
):
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")

    if body.idempotency_key:
        existing = await session.scalar(
            select(LeadEvent).where(LeadEvent.idempotency_key == body.idempotency_key)
        )
        if existing:
            return OpsEventOut.model_validate(existing)

    now = datetime.now(timezone.utc)
    occurred = body.occurred_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    if occurred > now + timedelta(minutes=5):
        raise HTTPException(status_code=400, detail="occurred_at in the future")

    message = await session.get(Message, lead.message_id)
    if message and message.message_date:
        md = message.message_date
        if md.tzinfo is None:
            md = md.replace(tzinfo=timezone.utc)
        if occurred < md - timedelta(days=1):
            raise HTTPException(status_code=400, detail="occurred_at before message_date")

    et = body.event_type.upper()
    if et in ("RESPONSE", "STATUS_CHANGE") and body.to_status == "WON":
        if not (body.evidence_ref or "").strip():
            raise HTTPException(status_code=400, detail="evidence_ref required for WON")
    if et == "RESPONSE" and not (body.evidence_ref or "").strip():
        raise HTTPException(status_code=400, detail="evidence_ref required for RESPONSE")

    if et == "RESPONSE":
        prior_contact = await session.scalar(
            select(LeadEvent.id).where(
                LeadEvent.lead_id == lead_id,
                LeadEvent.event_type == "CONTACT_ATTEMPT",
            ).limit(1)
        )
        if not prior_contact and (lead.status or "").upper() != "CONTACTED":
            raise HTTPException(
                status_code=400,
                detail="RESPONSE requires prior CONTACT_ATTEMPT or CONTACTED status",
            )

    try:
        fr, new_status = resolve_event_transition(
            current_status=lead.status,
            event_type=et,
            to_status=body.to_status,
        )
    except TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if new_status:
        lead.status = new_status
    if body.next_action_at is not None:
        lead.next_action_at = body.next_action_at
    lead.owner = operator_id

    evt = LeadEvent(
        lead_id=lead.id,
        message_id=lead.message_id,
        opportunity_key=lead.opportunity_key or (
            make_opportunity_key(message.community_id, message.text) if message else None
        ),
        event_type=et,
        from_status=fr,
        to_status=new_status or fr,
        channel=body.channel,
        actor_id=operator_id,
        reason_code=body.reason_code,
        note=body.note,
        evidence_ref=body.evidence_ref,
        outcome_amount=body.outcome_amount,
        outcome_currency=body.outcome_currency,
        occurred_at=occurred,
        created_at=now,
        idempotency_key=body.idempotency_key,
    )
    session.add(evt)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if body.idempotency_key:
            existing = await session.scalar(
                select(LeadEvent).where(LeadEvent.idempotency_key == body.idempotency_key)
            )
            if existing:
                return OpsEventOut.model_validate(existing)
        raise HTTPException(status_code=409, detail="event conflict") from exc
    await session.refresh(evt)
    return OpsEventOut.model_validate(evt)


@router.get("/metrics/funnel", response_model=OpsFunnelOut)
async def funnel_metrics(
    since_days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    operator_id: str = Depends(_require_operator),
):
    del operator_id
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    rows = (
        await session.execute(
            select(Lead.status, func.count(Lead.id)).where(Lead.updated_at >= since).group_by(Lead.status)
        )
    ).all()
    by_status = {str(s): int(c) for s, c in rows}

    def rate(num: int, den: int) -> float | None:
        if den <= 0:
            return None
        return round(num / den, 4)

    confirmed = by_status.get("QUALIFIED", 0) + by_status.get("CONTACTED", 0) + by_status.get(
        "RESPONDED", 0
    ) + by_status.get("WON", 0)
    rejected = by_status.get("REJECTED", 0)
    contacted = (
        by_status.get("CONTACTED", 0)
        + by_status.get("RESPONDED", 0)
        + by_status.get("WON", 0)
        + by_status.get("LOST", 0)
    )
    replied = by_status.get("RESPONDED", 0) + by_status.get("WON", 0)
    won = by_status.get("WON", 0)
    lost = by_status.get("LOST", 0)

    community_rows = (
        await session.execute(
            select(
                Community.username,
                Community.name,
                Lead.status,
                func.count(Lead.id),
            )
            .join(Message, Message.id == Lead.message_id)
            .join(Community, Community.id == Message.community_id)
            .where(Lead.updated_at >= since)
            .group_by(Community.username, Community.name, Lead.status)
        )
    ).all()
    by_community_map: dict[str, dict] = {}
    for uname, name, st, cnt in community_rows:
        key = uname or name or "unknown"
        bucket = by_community_map.setdefault(
            key, {"community": key, "confirmed": 0, "rejected": 0, "contacted": 0, "replied": 0, "won": 0}
        )
        st_u = (st or "").upper()
        if st_u in ("QUALIFIED", "CONTACTED", "RESPONDED", "WON"):
            bucket["confirmed"] += int(cnt)
        if st_u == "REJECTED":
            bucket["rejected"] += int(cnt)
        if st_u in ("CONTACTED", "RESPONDED", "WON", "LOST"):
            bucket["contacted"] += int(cnt)
        if st_u in ("RESPONDED", "WON"):
            bucket["replied"] += int(cnt)
        if st_u == "WON":
            bucket["won"] += int(cnt)

    now = datetime.now(timezone.utc)
    overdue = int(
        (
            await session.scalar(
                select(func.count(Lead.id)).where(
                    Lead.next_action_at.is_not(None),
                    Lead.next_action_at < now,
                    Lead.status.in_(("QUALIFIED", "CONTACTED", "RESPONDED")),
                )
            )
        )
        or 0
    )

    # Drift: leads whose latest non-BACKFILL event to_status disagrees with lead.status
    drift_sql = text(
        """
        SELECT COUNT(*) FROM leads l
        WHERE EXISTS (
          SELECT 1 FROM lead_events e
          WHERE e.lead_id = l.id
            AND e.event_type <> 'BACKFILL'
            AND e.to_status IS NOT NULL
            AND e.to_status <> l.status
            AND e.id = (
              SELECT MAX(e2.id) FROM lead_events e2
              WHERE e2.lead_id = l.id AND e2.event_type <> 'BACKFILL'
            )
        )
        """
    )
    drift = int((await session.scalar(drift_sql)) or 0)

    return OpsFunnelOut(
        since_days=since_days,
        by_status=by_status,
        human_confirmed=confirmed,
        human_rejected=rejected,
        contacted=contacted,
        replied=replied,
        won=won,
        lost=lost,
        rates={
            "confirmed_over_confirmed_plus_rejected": rate(confirmed, confirmed + rejected),
            "contacted_over_confirmed": rate(contacted, confirmed),
            "replied_over_contacted": rate(replied, contacted),
            "won_over_contacted": rate(won, contacted),
        },
        by_community=list(by_community_map.values()),
        overdue_followups=overdue,
        status_event_drift=drift,
    )


@router.get("/metrics/milestone", response_model=OpsMilestoneOut)
async def milestone_metrics(
    session: AsyncSession = Depends(get_session),
    operator_id: str = Depends(_require_operator),
):
    del operator_id
    promoted = int(
        (
            await session.scalar(
                select(func.count(func.distinct(LeadEvent.lead_id))).where(
                    LeadEvent.event_type == "PROMOTE"
                )
            )
        )
        or 0
    )
    confirmed = int(
        (
            await session.scalar(
                select(func.count(Lead.id)).where(
                    Lead.status.in_(("QUALIFIED", "CONTACTED", "RESPONDED", "WON"))
                )
            )
        )
        or 0
    )
    rejected = int(
        (await session.scalar(select(func.count(Lead.id)).where(Lead.status == "REJECTED"))) or 0
    )
    contacted = int(
        (
            await session.scalar(
                select(func.count(Lead.id)).where(
                    Lead.status.in_(("CONTACTED", "RESPONDED", "WON", "LOST"))
                )
            )
        )
        or 0
    )
    replied = int(
        (
            await session.scalar(
                select(func.count(Lead.id)).where(Lead.status.in_(("RESPONDED", "WON")))
            )
        )
        or 0
    )
    won = int((await session.scalar(select(func.count(Lead.id)).where(Lead.status == "WON"))) or 0)
    dispositions = confirmed + rejected
    return OpsMilestoneOut(
        candidates_promoted=promoted,
        human_confirmed=confirmed,
        human_rejected=rejected,
        contacted=contacted,
        replied=replied,
        qualified_conversations=replied,
        won=won,
        first_25_ready=dispositions >= 25,
    )
