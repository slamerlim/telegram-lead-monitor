#!/usr/bin/env python3
"""Re-score messages already stored in PostgreSQL without contacting Telegram.

Run inside the project environment/container:
  python scripts/reprocess_messages.py
  python scripts/reprocess_messages.py --since-days 30

The operation is idempotent: matching leads are updated, and messages that now score LOW
have their existing lead record removed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from services.analyzer.app.scoring import LeadScorer
from shared.db import SessionLocal
from shared.models import Author, Community, Lead, Message
from shared.settings import get_settings


async def reprocess(since_days: int | None) -> None:
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    since = datetime.now(timezone.utc) - timedelta(days=since_days) if since_days else None

    async with SessionLocal() as db:
        stmt = select(Message, Community, Author).join(Community, Message.community_id == Community.id).outerjoin(
            Author, Message.author_id == Author.id
        ).order_by(Message.id.asc())
        if since is not None:
            stmt = stmt.where(Message.message_date >= since)

        rows = (await db.execute(stmt)).all()
        processed = high = medium = low = 0
        for message, _community, _author in rows:
            result = scorer.score(message.text)
            lead = await db.scalar(select(Lead).where(Lead.message_id == message.id))
            if result.tier == "LOW":
                if lead:
                    await db.delete(lead)
                low += 1
            else:
                if not lead:
                    lead = Lead(message_id=message.id)
                    db.add(lead)
                lead.score = result.score
                lead.tier = result.tier
                lead.lead_type = result.lead_type
                lead.buyer_type = result.buyer_type
                lead.status = lead.status or "NEW"
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
                if result.tier == "HIGH":
                    high += 1
                else:
                    medium += 1
            processed += 1
            if processed % 250 == 0:
                await db.commit()
                print(f"processed={processed} high={high} medium={medium} low={low}", flush=True)
        await db.commit()
        print(f"DONE processed={processed} high={high} medium={medium} low={low}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-days", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(reprocess(args.since_days))
