import asyncio
import json
import os
import time

import structlog
from sqlalchemy import delete, select

from services.analyzer.app.scoring import LeadScorer
from services.analyzer.app.semantic import SemanticMatcher
from shared.db import SessionLocal
from shared.events import MessageEvent
from shared.logging import configure_logging
from shared.models import Author, Community, Lead, Message
from shared.redis_bus import MESSAGES_STREAM, RedisBus
from shared.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
log = structlog.get_logger()


class Analyzer:
    def __init__(self) -> None:
        self.bus = RedisBus(settings.redis_url)
        self.scorer = LeadScorer(settings.scoring_config)
        self.semantic = SemanticMatcher(
            settings.semantic_enabled,
            settings.semantic_model,
            settings.semantic_threshold,
        )
        self.processed = 0
        self.high = 0
        self.medium = 0
        self.errors = 0
        self.started_at = time.monotonic()

    async def close(self) -> None:
        await self.bus.close()

    async def run_forever(self) -> None:
        consumer = f"analyzer-{os.getpid()}"
        log.info("analyzer_started", consumer=consumer, semantic_enabled=settings.semantic_enabled)
        while True:
            entries = await self.bus.consume(
                MESSAGES_STREAM,
                "analyzer-workers",
                consumer,
                block_ms=5000,
                count=50,
            )
            if not entries:
                continue
            for _stream, messages in entries:
                for redis_message_id, fields in messages:
                    try:
                        event = MessageEvent.model_validate(json.loads(fields["payload"]))
                        result = await self.process(event)
                        self.processed += 1
                        if result.tier == "HIGH":
                            self.high += 1
                        elif result.tier == "MEDIUM":
                            self.medium += 1
                        if self.processed % 250 == 0:
                            elapsed = max(time.monotonic() - self.started_at, 0.001)
                            log.info(
                                "analyzer_progress",
                                processed=self.processed,
                                high=self.high,
                                medium=self.medium,
                                errors=self.errors,
                                messages_per_second=round(self.processed / elapsed, 2),
                            )
                    except Exception as exc:
                        self.errors += 1
                        log.exception("message_analysis_failed", error=str(exc))
                    finally:
                        await self.bus.ack(MESSAGES_STREAM, "analyzer-workers", redis_message_id)

    async def process(self, event: MessageEvent):
        async with SessionLocal() as db:
            community = await db.get(Community, event.community_id)
            if not community:
                community = Community(
                    id=event.community_id,
                    telegram_ref=event.community_username or str(event.telegram_chat_id),
                    telegram_chat_id=event.telegram_chat_id,
                    name=event.community_name,
                    username=event.community_username,
                    url=event.community_url,
                    kind="unknown",
                    resolve_status="resolved",
                )
                db.add(community)
            else:
                community.telegram_chat_id = event.telegram_chat_id
                community.name = event.community_name
                community.username = event.community_username
                community.url = event.community_url
                community.resolve_status = "resolved"
                community.last_error = None

            author = None
            if event.author_id:
                author = await db.scalar(select(Author).where(Author.telegram_id == event.author_id))
                if not author:
                    author = Author(telegram_id=event.author_id)
                    db.add(author)
                author.username = event.author_username
                author.name = event.author_name
                author.bio = event.author_bio
                author.is_bot = event.author_is_bot
                await db.flush()

            message = await db.scalar(
                select(Message).where(
                    Message.community_id == event.community_id,
                    Message.telegram_message_id == event.message_id,
                )
            )
            if not message:
                message = Message(
                    community_id=event.community_id,
                    author_id=author.id if author else None,
                    telegram_chat_id=event.telegram_chat_id,
                    telegram_message_id=event.message_id,
                    message_url=event.message_url,
                    message_date=event.message_date,
                    text=event.message_text,
                    is_reply=event.is_reply,
                )
                db.add(message)
                await db.flush()
            else:
                message.author_id = author.id if author else None
                message.message_url = event.message_url
                message.message_date = event.message_date
                message.text = event.message_text
                message.is_reply = event.is_reply

            result = self.scorer.score(event.message_text)
            semantic_score = self.semantic.score(event.message_text)
            if semantic_score is not None:
                result.semantic_score = semantic_score
                if semantic_score >= settings.semantic_threshold and result.tier == "LOW" and result.buyer_type == "CLIENT":
                    result.score = min(100.0, result.score + 12.0)
                    result.tier = "HIGH" if result.score >= self.scorer.high else "MEDIUM"
                    result.reasons.append(f"semantic relevance {semantic_score:.2f}")

            lead = await db.scalar(select(Lead).where(Lead.message_id == message.id))
            if result.tier == "LOW":
                if lead:
                    await db.delete(lead)
                await db.commit()
                return result

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
            await db.commit()
            return result


async def main() -> None:
    worker = Analyzer()
    try:
        await worker.run_forever()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
