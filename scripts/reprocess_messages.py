#!/usr/bin/env python3
"""Re-score already stored messages using community-aware commercial semantics."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from services.analyzer.app.scoring import LeadScorer
from shared.db import SessionLocal
from shared.models import Author, Community, Lead, Message
from shared.settings import get_settings


def _json_value(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
    )


def lead_values_from_result(result: Any, status: str) -> dict[str, Any]:
    """Map a scorer result onto Lead columns without mutating the Lead."""
    return {
        "score": result.score,
        "tier": result.tier,
        "lead_type": result.lead_type,
        "buyer_type": result.buyer_type,
        "status": status or "NEW",
        "intent_score": result.intent_score,
        "technical_score": result.technical_score,
        "commercial_score": result.commercial_score,
        "promotion_score": result.promotion_score,
        "matched_keywords": _json_value(result.matched_keywords),
        "matched_categories": _json_value(result.matched_categories),
        "reasons": _json_value(result.reasons),
        "contact_usernames": _json_value(result.contact_usernames),
        "contact_urls": _json_value(result.contact_urls),
        "budget_amount": result.budget_amount,
        "budget_currency": result.budget_currency,
        "semantic_score": result.semantic_score,
    }


def apply_lead_values(lead: Any, new_values: dict[str, Any]) -> bool:
    """Copy changed fields onto lead. Returns True when at least one field changed."""
    changed = False
    for field, new_value in new_values.items():
        if getattr(lead, field) != new_value:
            setattr(lead, field, new_value)
            changed = True
    return changed


async def reprocess(
    since_days: int | None,
    batch_size: int,
    max_messages: int | None,
) -> None:
    settings = get_settings()

    scoring_path = Path(settings.scoring_config)

    if not scoring_path.exists():
        project_root = Path(__file__).resolve().parents[1]
        scoring_path = project_root / "config" / "scoring.yaml"

    if not scoring_path.exists():
        raise FileNotFoundError(
            f"Scoring config not found: {settings.scoring_config} "
            f"or fallback {scoring_path}"
        )

    scorer = LeadScorer(str(scoring_path))

    since = (
        datetime.now(timezone.utc) - timedelta(days=since_days)
        if since_days
        else None
    )

    async with SessionLocal() as db:
        # Freeze the processing boundary so newly arriving Telegram messages
        # are not pulled into this reprocess run.
        max_stmt = (
            select(Message.id)
            .order_by(Message.id.desc())
            .limit(1)
        )

        if since is not None:
            max_stmt = max_stmt.where(
                Message.message_date >= since
            )

        max_message_id = await db.scalar(max_stmt)

        if max_message_id is None:
            print(
                "DONE processed=0 high=0 medium=0 low=0 created=0 updated=0 unchanged=0 deleted=0",
                flush=True,
            )
            return

        processed = 0
        high = 0
        medium = 0
        low = 0
        updated = 0
        created = 0
        unchanged = 0
        deleted = 0

        last_message_id = 0

        while True:
            remaining = None
            if max_messages is not None:
                remaining = max_messages - processed
                if remaining <= 0:
                    break

            current_batch_size = batch_size
            if remaining is not None:
                current_batch_size = min(current_batch_size, remaining)
            stmt = (
                select(Message, Community, Author)
                .join(
                    Community,
                    Message.community_id == Community.id,
                )
                .outerjoin(
                    Author,
                    Message.author_id == Author.id,
                )
                .where(
                    Message.id > last_message_id,
                    Message.id <= max_message_id,
                )
                .order_by(Message.id.asc())
                .limit(current_batch_size)
            )

            if since is not None:
                stmt = stmt.where(
                    Message.message_date >= since
                )

            rows = (await db.execute(stmt)).all()

            if not rows:
                break

            message_ids = [
                message.id
                for message, _, _ in rows
            ]

            # Load all existing leads for this batch in one query instead
            # of one SELECT per message.
            existing_leads = {
                lead.message_id: lead
                for lead in (
                    await db.scalars(
                        select(Lead).where(
                            Lead.message_id.in_(message_ids)
                        )
                    )
                ).all()
            }

            for message, community, _author in rows:
                result = scorer.score(
                    message.text,
                    community_username=community.username,
                    community_name=community.name,
                )

                lead = existing_leads.get(message.id)

                if result.tier == "LOW":
                    if lead:
                        await db.delete(lead)
                        deleted += 1

                    low += 1
                    processed += 1
                    last_message_id = message.id
                    continue

                if lead is None:
                    lead = Lead(message_id=message.id)
                    db.add(lead)
                    existing_leads[message.id] = lead
                    created += 1

                new_values = lead_values_from_result(
                    result,
                    lead.status or "NEW",
                )

                if apply_lead_values(lead, new_values):
                    updated += 1
                else:
                    unchanged += 1

                if result.tier == "HIGH":
                    high += 1
                else:
                    medium += 1

                processed += 1
                last_message_id = message.id

            await db.commit()

            print(
                f"processed={processed} "
                f"high={high} "
                f"medium={medium} "
                f"low={low} "
                f"created={created} "
                f"updated={updated} "
                f"unchanged={unchanged} "
                f"deleted={deleted} "
                f"last_message_id={last_message_id}/{max_message_id}",
                flush=True,
            )

        print(
            f"DONE "
            f"processed={processed} "
            f"high={high} "
            f"medium={medium} "
            f"low={low} "
            f"created={created} "
            f"updated={updated} "
            f"unchanged={unchanged} "
            f"deleted={deleted}",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--since-days",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
    )

    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Process at most this many messages for controlled smoke tests.",
    )

    args = parser.parse_args()

    if args.since_days is not None and args.since_days <= 0:
        parser.error("--since-days must be > 0")

    if args.batch_size <= 0:
        parser.error("--batch-size must be > 0")

    if args.max_messages is not None and args.max_messages <= 0:
        parser.error("--max-messages must be > 0")

    asyncio.run(
        reprocess(
            args.since_days,
            args.batch_size,
            args.max_messages,
        )
    )
