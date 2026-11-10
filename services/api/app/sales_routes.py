"""Owner sales inbox and outcome measurements; never independent ML labels."""
from __future__ import annotations

import math
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.app.ops_routes import _require_operator, _require_ops_enabled
from shared.capacity import storage_snapshot
from shared.db import get_session
from shared.models import Author, CommercialAIReview, Community, Lead, LeadEvent, LeadNotification, Message
from shared.sales import evidence, utc
from shared.settings import get_settings
from shared.provider_health import sdk_health
from shared.redis_bus import RedisBus

router = APIRouter(prefix="/ops", tags=["sales-inbox"])
_PAGE = Path(__file__).resolve().parents[1] / "static/inbox/index.html"
_FEEDBACK = {"accepted": "OWNER_ACCEPTED", "rejected": "OWNER_REJECTED", "proposal": "PROPOSAL_SENT"}
_ACTIVE = ("AI_CONFIRMED", "QUALIFIED", "CONTACTED", "RESPONDED")


@router.get("/inbox/", response_class=HTMLResponse)
async def inbox_page():
    _require_ops_enabled()
    return HTMLResponse(_PAGE.read_text(), headers={"Cache-Control": "no-store",
                        "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"})


class FeedbackIn(BaseModel):
    decision: Literal["accepted", "rejected", "proposal"]
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/leads/{lead_id}/feedback")
async def owner_feedback(lead_id: int, body: FeedbackIn,
                         session: AsyncSession = Depends(get_session),
                         operator_id: str = Depends(_require_operator)):
    lead = await session.get(Lead, lead_id, with_for_update=True)
    if not lead:
        raise HTTPException(404, "lead not found")
    if body.decision == "rejected" and not (body.reason or "").strip():
        raise HTTPException(422, "Please record why the lead was rejected")
    if body.decision == "proposal" and lead.status not in {"CONTACTED", "RESPONDED", "QUALIFIED"}:
        raise HTTPException(409, "Record qualification/contact before a proposal")
    if lead.status in {"NEW", "REVIEWED"}:
        raise HTTPException(409, "Commercial review has not confirmed this lead")
    event_type = _FEEDBACK[body.decision]
    last = await session.scalar(select(LeadEvent).where(
        LeadEvent.lead_id == lead.id, LeadEvent.event_type.in_(
            ("PROPOSAL_SENT",) if body.decision == "proposal" else ("OWNER_ACCEPTED", "OWNER_REJECTED")
        )).order_by(LeadEvent.id.desc()).limit(1))
    if last and last.event_type == event_type and last.note == body.reason:
        return {"recorded": True, "event_id": last.id}
    now = datetime.now(timezone.utc)
    event = LeadEvent(lead_id=lead.id, message_id=lead.message_id,
                      opportunity_key=lead.opportunity_key, event_type=event_type,
                      from_status=lead.status, to_status=lead.status,
                      actor_id=operator_id, reason_code="owner_feedback", note=body.reason,
                      occurred_at=now, created_at=now)
    session.add(event)
    await session.commit()
    return {"recorded": True, "event_id": event.id}


async def pilot_metrics(session: AsyncSession, now: datetime) -> dict:
    first = await session.scalar(select(func.min(LeadNotification.sent_at)).where(LeadNotification.status == "SENT"))
    base = {"delivered": 0, "accepted": 0, "rejected": 0, "reviewed": 0, "acceptance_rate": None,
            "conversations": 0, "proposals": 0, "wins": 0, "reachable_buyers": 0,
            "p95_alert_seconds": None, "first_delivery_at": first, "elapsed_days": 0,
            "target_days": 7, "target_leads": 20, "target_acceptance_rate": .8,
            "target_p95_seconds": 300, "target_met": False, "by_source": []}
    if not first:
        return base
    first = utc(first)
    end = first + timedelta(days=7)
    rows = (await session.execute(select(LeadNotification, Lead, Message, Community)
        .join(Lead, Lead.id == LeadNotification.lead_id).join(Message, Message.id == Lead.message_id)
        .join(Community, Community.id == Message.community_id)
        .where(LeadNotification.status == "SENT", LeadNotification.sent_at < end))).all()
    ids = [lead.id for _, lead, _, _ in rows]
    events = (await session.scalars(select(LeadEvent).where(LeadEvent.lead_id.in_(ids),
        LeadEvent.occurred_at >= first, LeadEvent.occurred_at < end).order_by(LeadEvent.id))).all() if ids else []
    feedback, replied, proposed, won, reachable = {}, set(), set(), set(), set()
    for event in events:
        if event.event_type in {"OWNER_ACCEPTED", "OWNER_REJECTED"}:
            feedback[event.lead_id] = event.event_type
        if event.event_type == "RESPONSE" and event.evidence_ref:
            replied.add(event.lead_id)
            reachable.add(event.lead_id)
        if event.event_type == "PROPOSAL_SENT": proposed.add(event.lead_id)
        if event.to_status == "WON" and event.evidence_ref: won.add(event.lead_id)
    accepted = {key for key, value in feedback.items() if value == "OWNER_ACCEPTED"}
    rejected = {key for key, value in feedback.items() if value == "OWNER_REJECTED"}
    # Nearest-rank p95, in seconds from the original Telegram post, not DB insertion.
    latencies = sorted(max(0, (utc(alert.sent_at) - utc(msg.message_date)).total_seconds())
                       for alert, _, msg, _ in rows)
    by_source = {}
    for _, lead, _, source in rows:
        group = by_source.setdefault(source.id, {"community_id": source.id,
            "name": source.name or source.telegram_ref, "delivered": 0, "accepted": 0,
            "rejected": 0, "conversations": 0, "proposals": 0, "wins": 0})
        group["delivered"] += 1
        for name, members in (("accepted", accepted), ("rejected", rejected),
                              ("conversations", replied), ("proposals", proposed), ("wins", won)):
            group[name] += lead.id in members
    elapsed = max(0, (now - first).total_seconds() / 86400)
    reviewed = len(accepted) + len(rejected)
    rate = len(accepted) / reviewed if reviewed else None
    base.update(delivered=len(rows), accepted=len(accepted), rejected=len(rejected), reviewed=reviewed,
                acceptance_rate=rate, conversations=len(replied), proposals=len(proposed), wins=len(won),
                reachable_buyers=len(reachable), elapsed_days=round(elapsed, 2),
                p95_alert_seconds=round(latencies[math.ceil(.95 * len(latencies)) - 1], 1) if latencies else None,
                by_source=list(by_source.values()),
                target_met=bool(elapsed >= 7 and len(rows) >= 20 and reviewed == len(rows) and rate is not None and rate >= .8))
    return base


@router.get("/inbox/data")
async def inbox_data(view: Literal["fresh", "stale", "pipeline", "duplicates"] = "fresh",
                     limit: int = Query(100, ge=1, le=200),
                     session: AsyncSession = Depends(get_session),
                     operator_id: str = Depends(_require_operator)):
    del operator_id
    now = datetime.now(timezone.utc)
    cfg = get_settings()
    cutoff = now - timedelta(hours=cfg.hot_lead_max_age_hours)
    latest_feedback = (select(LeadEvent.event_type).where(LeadEvent.lead_id == Lead.id,
        LeadEvent.event_type.in_(("OWNER_ACCEPTED", "OWNER_REJECTED")))
        .order_by(LeadEvent.id.desc()).limit(1).correlate(Lead).scalar_subquery())
    visible = func.coalesce(latest_feedback, "") != "OWNER_REJECTED"
    dedup_key = func.coalesce(func.nullif(func.split_part(Lead.opportunity_key, ":", 2), ""), cast(Lead.id, String))
    copies = select(Lead.id.label("lead_id"),
        func.row_number().over(partition_by=dedup_key, order_by=Lead.id).label("position"),
        func.count().over(partition_by=dedup_key).label("copies"),
        func.min(Lead.id).over(partition_by=dedup_key).label("original_id")).subquery()
    canonical = copies.c.position == 1
    filters = {
        "fresh": (canonical, Lead.status.in_(("AI_CONFIRMED", "QUALIFIED")), Message.message_date >= cutoff),
        "stale": (canonical, Lead.status.in_(("AI_CONFIRMED", "QUALIFIED")), Message.message_date < cutoff),
        "pipeline": (canonical, Lead.status.in_(("CONTACTED", "RESPONDED")),),
        "duplicates": (copies.c.position > 1, Lead.status.in_(_ACTIVE)),
    }
    summary = {}
    for name, conditions in filters.items():
        summary["in_progress" if name == "pipeline" else name] = await session.scalar(
            select(func.count(Lead.id)).join(Message, Lead.message_id == Message.id)
            .join(copies, copies.c.lead_id == Lead.id).where(visible, *conditions, Message.message_date <= now + timedelta(minutes=5)))
    rows = (await session.execute(select(Lead, Message, Community, Author, LeadNotification, copies.c.copies, copies.c.original_id)
        .join(Message, Message.id == Lead.message_id).join(Community, Community.id == Message.community_id)
        .outerjoin(Author, Author.id == Message.author_id).outerjoin(LeadNotification, LeadNotification.lead_id == Lead.id)
        .join(copies, copies.c.lead_id == Lead.id)
        .where(visible, *filters[view], Message.message_date <= now + timedelta(minutes=5))
        .order_by(Message.message_date.desc(), Lead.id.desc()).limit(limit))).all()
    items = []
    for lead, msg, community, author, alert, n_copies, original_id in rows:
        draft = await session.scalar(select(CommercialAIReview.draft_text).where(
            CommercialAIReview.lead_id == lead.id, CommercialAIReview.row_kind == "DRAFT",
            CommercialAIReview.draft_valid.is_(True)).order_by(CommercialAIReview.id.desc()).limit(1))
        decision = await session.scalar(select(CommercialAIReview.reason_code).where(
            CommercialAIReview.lead_id == lead.id, CommercialAIReview.row_kind == "DECISION")
            .order_by(CommercialAIReview.id.desc()).limit(1))
        items.append({"id": lead.id, "status": lead.status, "text": msg.text, "message_url": msg.message_url,
            "message_date": msg.message_date, **evidence(lead, msg, author, now=now),
            "community_name": community.name, "community_username": community.username,
            "draft": draft, "reason": decision, "next_action_at": lead.next_action_at,
            "duplicate_count": n_copies - 1, "duplicate_of_lead_id": original_id if original_id != lead.id else None,
            "alert_status": alert.status if alert else None, "alert_sent_at": alert.sent_at if alert else None})
    health = await storage_snapshot(session)
    health.update(sdk_health())
    collector_connected = False
    bus = RedisBus(cfg.redis_url)
    try:
        raw = await asyncio.wait_for(bus.redis.get("tlm:collector:heartbeat"), timeout=1)
        collector_connected = json.loads(raw or "{}").get("connected") is True
    except (OSError, ValueError, TimeoutError, RedisError):
        pass
    finally:
        await bus.close()
    health.update(telegram_authorized=collector_connected,
                  telegram_session_ready=Path(cfg.telegram_session_path).with_name("authorized").exists(),
                  enabled_sources=await session.scalar(select(func.count(Community.id)).where(Community.enabled.is_(True))),
                  last_message_at=await session.scalar(select(func.max(Message.message_date))))
    return {"items": items, "summary": summary, "max_age_hours": cfg.hot_lead_max_age_hours,
            "health": health, "pilot": await pilot_metrics(session, now)}
