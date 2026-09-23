"""Lead write helpers with (text_hash, community_id) opportunity deduplication."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.opportunity import opportunity_key as make_opportunity_key
from shared.models import Lead, Message

# Do not hard-delete CRM pipeline progress on rescore/dedup.
_PROTECTED_STATUSES = frozenset(
    {"REVIEWED", "CONTACTED", "RESPONDED", "QUALIFIED", "WON", "LOST", "REJECTED"}
)

LEAD_SOURCES = frozenset({"scorer", "agent_provisional", "operator_search", "promote"})


def _is_protected(lead: Lead) -> bool:
    return (lead.status or "NEW").upper() in _PROTECTED_STATUSES


def apply_score_result(lead: Lead, result: Any, *, status: str | None = None) -> None:
    lead.score = result.score
    lead.tier = result.tier
    lead.lead_type = result.lead_type
    lead.buyer_type = result.buyer_type
    if status is not None:
        lead.status = status
    elif not lead.status:
        lead.status = "NEW"
    lead.intent_score = result.intent_score
    lead.technical_score = result.technical_score
    lead.commercial_score = result.commercial_score
    lead.promotion_score = result.promotion_score
    lead.matched_keywords = json.dumps(result.matched_keywords, ensure_ascii=False)
    lead.matched_categories = json.dumps(result.matched_categories, ensure_ascii=False)
    lead.reasons = json.dumps(result.reasons, ensure_ascii=False)
    lead.contact_usernames = json.dumps(result.contact_usernames, ensure_ascii=False)
    lead.contact_urls = json.dumps(result.contact_urls, ensure_ascii=False)
    lead.budget_amount = result.budget_amount
    lead.budget_currency = result.budget_currency
    lead.semantic_score = result.semantic_score


def _lead_snapshot(lead: Lead) -> dict[str, Any]:
    return {
        "score": lead.score,
        "tier": lead.tier,
        "lead_type": lead.lead_type,
        "buyer_type": lead.buyer_type,
        "intent_score": lead.intent_score,
        "technical_score": lead.technical_score,
        "commercial_score": lead.commercial_score,
        "promotion_score": lead.promotion_score,
        "matched_keywords": lead.matched_keywords,
        "matched_categories": lead.matched_categories,
        "reasons": lead.reasons,
        "contact_usernames": lead.contact_usernames,
        "contact_urls": lead.contact_urls,
        "budget_amount": lead.budget_amount,
        "budget_currency": lead.budget_currency,
        "semantic_score": lead.semantic_score,
        "opportunity_key": lead.opportunity_key,
        "message_id": lead.message_id,
        "status": lead.status,
    }


async def _assign_opportunity_key_if_free(
    db: AsyncSession,
    lead: Lead,
    key: str,
) -> None:
    """Set opportunity_key only when free/owned; clear on conflict (incl. concurrent writers)."""
    owner = await db.scalar(select(Lead.message_id).where(Lead.opportunity_key == key))
    if owner is not None and owner != lead.message_id:
        lead.opportunity_key = None
        return
    try:
        async with db.begin_nested():
            lead.opportunity_key = key
            await db.flush()
    except IntegrityError:
        lead.opportunity_key = None


async def upsert_opportunity_lead(
    db: AsyncSession,
    message: Message,
    result: Any,
    *,
    status_default: str = "NEW",
) -> tuple[str, Lead | None]:
    """Persist a non-LOW score as at most one lead per (text_hash, community_id).

    Returns (action, lead) where action is one of:
    deleted | demoted | noop | created | updated | unchanged |
    deduped_updated | deduped_skipped

    Concurrent UNIQUE races on opportunity_key are absorbed via savepoints
    (key cleared or path re-resolved) so the outer transaction can still commit.
    """
    key = make_opportunity_key(message.community_id, message.text)
    lead_for_message = await db.scalar(select(Lead).where(Lead.message_id == message.id))

    if result.tier == "LOW":
        if not lead_for_message:
            return "noop", None
        if _is_protected(lead_for_message):
            before = _lead_snapshot(lead_for_message)
            apply_score_result(lead_for_message, result)
            await _assign_opportunity_key_if_free(db, lead_for_message, key)
            return ("unchanged" if _lead_snapshot(lead_for_message) == before else "demoted"), lead_for_message
        await db.delete(lead_for_message)
        await db.flush()
        return "deleted", None

    existing = await db.scalar(select(Lead).where(Lead.opportunity_key == key))

    if existing and existing.message_id != message.id:
        if lead_for_message:
            if _is_protected(lead_for_message) and not _is_protected(existing):
                await db.delete(existing)
                await db.flush()
                existing = lead_for_message
            elif _is_protected(lead_for_message) and _is_protected(existing):
                lead_for_message.opportunity_key = None
                await db.flush()
                apply_score_result(lead_for_message, result)
                return "updated", lead_for_message
            else:
                await db.delete(lead_for_message)
                await db.flush()
                lead_for_message = None

        before = _lead_snapshot(existing)
        if existing.message_id == message.id:
            # Survivor is this message's own lead (after replacing an unprotected duplicate).
            apply_score_result(existing, result)
            await _assign_opportunity_key_if_free(db, existing, key)
            action = "unchanged" if _lead_snapshot(existing) == before else "deduped_updated"
            return action, existing
        if result.score > existing.score and not _is_protected(existing):
            apply_score_result(existing, result)
            existing.message_id = message.id
            action = "unchanged" if _lead_snapshot(existing) == before else "deduped_updated"
            return action, existing
        return "deduped_skipped", existing

    if not lead_for_message:
        lead_for_message = Lead(message_id=message.id, opportunity_key=None)
        db.add(lead_for_message)
        apply_score_result(lead_for_message, result, status=status_default)
        try:
            async with db.begin_nested():
                lead_for_message.opportunity_key = key
                await db.flush()
            return "created", lead_for_message
        except IntegrityError:
            # Concurrent writer took the key — keep own row without key and attach to survivor.
            lead_for_message.opportunity_key = None
            await db.flush()
            existing = await db.scalar(select(Lead).where(Lead.opportunity_key == key))
            if existing and existing.message_id != message.id:
                if _is_protected(lead_for_message) and _is_protected(existing):
                    apply_score_result(lead_for_message, result)
                    return "updated", lead_for_message
                if not _is_protected(lead_for_message):
                    await db.delete(lead_for_message)
                    await db.flush()
                    if result.score > existing.score and not _is_protected(existing):
                        before = _lead_snapshot(existing)
                        apply_score_result(existing, result)
                        existing.message_id = message.id
                        return (
                            ("unchanged" if _lead_snapshot(existing) == before else "deduped_updated"),
                            existing,
                        )
                    return "deduped_skipped", existing
            return "created", lead_for_message

    before = _lead_snapshot(lead_for_message)
    apply_score_result(lead_for_message, result)
    await _assign_opportunity_key_if_free(db, lead_for_message, key)
    if _lead_snapshot(lead_for_message) == before:
        return "unchanged", lead_for_message
    return "updated", lead_for_message


async def promote_candidate_lead(
    db: AsyncSession,
    message: Message,
    *,
    source: str,
    actor_id: str,
    score_snapshot: dict[str, Any] | None = None,
    status: str = "REVIEWED",
) -> tuple[str, Lead]:
    """Create or protect a lead for commercial operator review without threshold changes.

    Does not invent commercial scores: copies provided snapshot or leaves defaults.
    Lands at REVIEWED (protected) so rescore cannot delete the row.
    """
    src = (source or "promote").strip().lower()
    if src not in LEAD_SOURCES:
        raise ValueError(f"invalid lead source: {source!r}")
    st = (status or "REVIEWED").upper()
    if st not in {"REVIEWED", "QUALIFIED"}:
        raise ValueError("promote status must be REVIEWED or QUALIFIED")

    key = make_opportunity_key(message.community_id, message.text)
    existing_key_owner = await db.scalar(select(Lead).where(Lead.opportunity_key == key))
    lead = await db.scalar(select(Lead).where(Lead.message_id == message.id))

    if existing_key_owner and (not lead or existing_key_owner.message_id != message.id):
        raise ValueError(f"opportunity_key owned by lead_id={existing_key_owner.id}")

    if not lead:
        lead = Lead(message_id=message.id, opportunity_key=None, status=st, source=src)
        db.add(lead)
        action = "created"
    else:
        action = "updated"
        if not _is_protected(lead) or (lead.status or "").upper() == "NEW":
            lead.status = st
        lead.source = src

    if score_snapshot:
        for field in (
            "score",
            "tier",
            "lead_type",
            "buyer_type",
            "intent_score",
            "technical_score",
            "commercial_score",
            "promotion_score",
            "matched_keywords",
            "matched_categories",
            "reasons",
            "contact_usernames",
            "contact_urls",
            "budget_amount",
            "budget_currency",
            "semantic_score",
        ):
            if field in score_snapshot and score_snapshot[field] is not None:
                setattr(lead, field, score_snapshot[field])

    lead.owner = actor_id
    await _assign_opportunity_key_if_free(db, lead, key)
    await db.flush()
    return action, lead
