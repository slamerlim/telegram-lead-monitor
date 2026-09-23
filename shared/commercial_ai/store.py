"""DB writers for commercial AI reviews + AI_PROMOTE (internal worker only)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.commercial_ai.policy import (
    DECISION_CONFIRMED,
    CommercialDecision,
    OpinionView,
    POLICY_VERSION,
)
from shared.commercial_ai.prompts import PROMPT_VERSION
from shared.lead_fsm import resolve_event_transition
from shared.models import CommercialAIReview, Lead, LeadEvent, Message


def text_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


async def already_decided(
    session: AsyncSession,
    *,
    lead_id: int,
    text_hash: str,
) -> CommercialAIReview | None:
    return await session.scalar(
        select(CommercialAIReview)
        .where(
            CommercialAIReview.lead_id == lead_id,
            CommercialAIReview.row_kind == "DECISION",
            CommercialAIReview.text_sha256 == text_hash,
            CommercialAIReview.prompt_version == PROMPT_VERSION,
            CommercialAIReview.policy_version == POLICY_VERSION,
        )
        .order_by(CommercialAIReview.id.desc())
        .limit(1)
    )


async def record_opinion(
    session: AsyncSession,
    *,
    run_id: str,
    lead: Lead,
    message: Message,
    text_hash: str,
    slot_id: str,
    mode: str,
    model: str,
    model_family: str,
    status: str,
    payload: dict[str, Any] | None,
    synthetic: bool,
    request_id: str | None,
    latency_ms: int | None,
    raw: str | None,
) -> CommercialAIReview:
    p = payload or {}
    key = f"cai_op:{run_id}:{mode}:{slot_id}"
    row = CommercialAIReview(
        run_id=run_id,
        row_kind="OPINION",
        lead_id=lead.id,
        message_id=message.id,
        opportunity_key=lead.opportunity_key,
        text_sha256=text_hash,
        slot_id=slot_id,
        mode=mode,
        provider="cursor",
        model=model,
        model_family=model_family,
        prompt_version=PROMPT_VERSION,
        policy_version=POLICY_VERSION,
        synthetic=synthetic,
        request_id=request_id,
        latency_ms=latency_ms,
        status=status,
        label="AI_TRUE" if p.get("is_commercial_opportunity") is True else (
            "AI_FALSE" if p.get("is_commercial_opportunity") is False else "AI_UNCERTAIN"
        ),
        confidence=p.get("confidence"),
        lead_type=p.get("lead_type"),
        matches_objectives=p.get("matches_objectives") if isinstance(p.get("matches_objectives"), bool) else None,
        hard_veto=p.get("hard_veto") if isinstance(p.get("hard_veto"), bool) else None,
        uncertain=bool(p.get("uncertain")) if "uncertain" in p else None,
        buyer_action=p.get("buyer_action"),
        buyer_role=p.get("buyer_role"),
        contactability=p.get("contactability"),
        evidence_json=json.dumps(p.get("evidence") or [], ensure_ascii=False)[:8000],
        rationale_short=(p.get("rationale_short") or "")[:2000] or None,
        raw_response=(raw or "")[:16000] or None,
        idempotency_key=key,
    )
    session.add(row)
    return row


async def apply_decision(
    session: AsyncSession,
    *,
    run_id: str,
    lead: Lead,
    message: Message,
    text_hash: str,
    decision: CommercialDecision,
    opinions: list[OpinionView],
) -> CommercialAIReview:
    """Persist DECISION; auto-promote AI_CONFIRMED via system AI_PROMOTE event."""
    locked = await session.scalar(select(Lead).where(Lead.id == lead.id).with_for_update())
    if not locked:
        raise ValueError("lead missing")
    if (locked.status or "").upper() not in {"NEW", "REVIEWED"}:
        # Already advanced — still record decision for audit if not duplicate.
        pass

    key = f"cai_dec:{locked.id}:{text_hash[:16]}:{PROMPT_VERSION}:{POLICY_VERSION}"
    existing = await session.scalar(
        select(CommercialAIReview).where(CommercialAIReview.idempotency_key == key)
    )
    if existing:
        return existing

    row = CommercialAIReview(
        run_id=run_id,
        row_kind="DECISION",
        lead_id=locked.id,
        message_id=message.id,
        opportunity_key=locked.opportunity_key,
        text_sha256=text_hash,
        prompt_version=PROMPT_VERSION,
        policy_version=POLICY_VERSION,
        synthetic=False,
        status="ok",
        decision=decision.decision,
        reason_code=decision.reason_code,
        n_confirm=decision.n_confirm,
        n_reject=decision.n_reject,
        n_uncertain=decision.n_uncertain,
        lead_type=decision.lead_type,
        rank_score=decision.rank_score,
        idempotency_key=key,
    )
    session.add(row)

    if decision.decision == DECISION_CONFIRMED and (locked.status or "").upper() in {
        "NEW",
        "REVIEWED",
    }:
        fr, to = resolve_event_transition(
            current_status=locked.status,
            event_type="AI_PROMOTE",
            to_status="AI_CONFIRMED",
            actor_kind="system",
        )
        locked.status = to or "AI_CONFIRMED"
        now = datetime.now(timezone.utc)
        session.add(
            LeadEvent(
                lead_id=locked.id,
                message_id=message.id,
                opportunity_key=locked.opportunity_key,
                event_type="AI_PROMOTE",
                from_status=fr,
                to_status=locked.status,
                channel="cursor_sdk",
                actor_id="commercial_ai",
                reason_code=decision.reason_code,
                note=f"n_confirm={decision.n_confirm} families={decision.model_family_count}",
                evidence_ref=f"commercial_ai_reviews:run={run_id}",
                occurred_at=now,
                created_at=now,
                idempotency_key=f"ai_promote:{locked.id}:{text_hash[:16]}:{POLICY_VERSION}",
            )
        )
    return row


async def record_draft(
    session: AsyncSession,
    *,
    run_id: str,
    lead: Lead,
    message: Message,
    text_hash: str,
    draft_text: str,
    valid: bool,
    reject_reason: str | None = None,
) -> CommercialAIReview:
    key = f"cai_draft:{run_id}"
    row = CommercialAIReview(
        run_id=run_id,
        row_kind="DRAFT",
        lead_id=lead.id,
        message_id=message.id,
        opportunity_key=lead.opportunity_key,
        text_sha256=text_hash,
        prompt_version=PROMPT_VERSION,
        policy_version=POLICY_VERSION,
        status="ok",
        draft_text=draft_text[:2000],
        draft_valid=valid,
        draft_reject_reason=reject_reason,
        idempotency_key=key,
    )
    session.add(row)
    return row


def validate_draft(draft_text: str, source_text: str) -> tuple[bool, str | None]:
    t = (draft_text or "").strip()
    if not t:
        return False, "empty"
    if len(t) > 600:
        return False, "too_long"
    low = t.lower()
    if "http://" in low or "https://" in low or "t.me/" in low:
        return False, "contains_url"
    for banned in ("guaranteed", "guarantee", "100%", "$", "€", "rub/hour", "per hour"):
        if banned in low:
            return False, "forbidden_claim"
    # Prefer presence of a short shared token (≥4 chars) from source.
    src = (source_text or "").lower()
    tokens = [w for w in "".join(ch if ch.isalnum() else " " for ch in src).split() if len(w) >= 4]
    if tokens and not any(tok in low for tok in tokens[:20]):
        return False, "no_source_anchor"
    return True, None
