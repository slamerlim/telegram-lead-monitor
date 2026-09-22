"""Persist every scoring decision (including LOW / negatives) for ML readiness."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from shared.decisions import decision_from_result
from shared.models import Message, MessageScore


async def persist_message_score(
    db: Any,
    message: Message,
    result: Any,
    *,
    rule_version: str,
) -> MessageScore:
    from sqlalchemy import select

    row = await db.scalar(select(MessageScore).where(MessageScore.message_id == message.id))
    if not row:
        row = MessageScore(message_id=message.id, community_id=message.community_id)
        db.add(row)
    row.community_id = message.community_id
    row.scored_at = datetime.now(timezone.utc)
    row.score = float(result.score)
    row.tier = result.tier
    row.buyer_type = result.buyer_type
    row.lead_type = result.lead_type
    row.intent_score = float(result.intent_score)
    row.technical_score = float(result.technical_score)
    row.commercial_score = float(result.commercial_score)
    row.promotion_score = float(result.promotion_score)
    row.semantic_score = result.semantic_score
    row.matched_keywords = json.dumps(result.matched_keywords, ensure_ascii=False)
    row.matched_categories = json.dumps(result.matched_categories, ensure_ascii=False)
    row.reasons = json.dumps(result.reasons, ensure_ascii=False)
    row.rule_version = str(rule_version)
    row.decision = decision_from_result(result)
    return row
