"""Lead write helpers with (text_hash, community_id) opportunity deduplication."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.opportunity import opportunity_key as make_opportunity_key
from shared.models import Lead, Message

# Do not hard-delete CRM pipeline progress on rescore/dedup.
_PROTECTED_STATUSES = frozenset(
    {"REVIEWED", "CONTACTED", "RESPONDED", "QUALIFIED", "WON", "LOST", "REJECTED"}
)


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
    """
    key = make_opportunity_key(message.community_id, message.text)
    lead_for_message = await db.scalar(select(Lead).where(Lead.message_id == message.id))

    if result.tier == "LOW":
        if not lead_for_message:
            return "noop", None
        if (lead_for_message.status or "NEW").upper() in _PROTECTED_STATUSES:
            # Keep human pipeline state; demote scores instead of deleting.
            before = _lead_snapshot(lead_for_message)
            apply_score_result(lead_for_message, result)
            lead_for_message.opportunity_key = key
            return ("unchanged" if _lead_snapshot(lead_for_message) == before else "demoted"), lead_for_message
        await db.delete(lead_for_message)
        return "deleted", None

    existing = await db.scalar(select(Lead).where(Lead.opportunity_key == key))

    if existing and existing.message_id != message.id:
        # Same commercial opportunity already represented by another message.
        if lead_for_message:
            if (lead_for_message.status or "NEW").upper() in _PROTECTED_STATUSES:
                # Prefer the CRM-advanced row as the survivor.
                if (existing.status or "NEW").upper() not in _PROTECTED_STATUSES:
                    await db.delete(existing)
                    existing = lead_for_message
                else:
                    await db.delete(lead_for_message)
            else:
                await db.delete(lead_for_message)
        if result.score > existing.score:
            before = _lead_snapshot(existing)
            apply_score_result(existing, result)
            existing.opportunity_key = key
            # Repoint representative message so CRM joins match the winning text instance.
            existing.message_id = message.id
            return (
                "unchanged" if _lead_snapshot(existing) == before else "deduped_updated"
            ), existing
        return "deduped_skipped", existing

    if not lead_for_message:
        lead_for_message = Lead(message_id=message.id, opportunity_key=key)
        db.add(lead_for_message)
        apply_score_result(lead_for_message, result, status=status_default)
        return "created", lead_for_message

    before = _lead_snapshot(lead_for_message)
    apply_score_result(lead_for_message, result)
    lead_for_message.opportunity_key = key
    if _lead_snapshot(lead_for_message) == before:
        return "unchanged", lead_for_message
    return "updated", lead_for_message
