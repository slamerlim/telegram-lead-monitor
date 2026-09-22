import asyncio
import json
import os
import time

import structlog
from sqlalchemy import select

from services.analyzer.app.scoring import LeadScorer
from services.analyzer.app.semantic import SemanticMatcher
from shared.db import SessionLocal
from shared.events import MessageEvent
from shared.logging import configure_logging
from shared.models import Author, Community, Message
from shared.lead_write import upsert_opportunity_lead
from shared.score_persist import persist_message_score
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
                        await self.bus.ack(
                            MESSAGES_STREAM,
                            "analyzer-workers",
                            redis_message_id,
                        )
                    except Exception as exc:
                        self.errors += 1
                        log.exception("message_analysis_failed", error=str(exc))
                        # Do not ACK: leave the entry pending for retry/inspection.

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

            result = self.scorer.score(
                event.message_text,
                community_username=event.community_username,
                community_name=event.community_name,
            )
            semantic_score = self.semantic.score(event.message_text)
            if semantic_score is not None:
                result.semantic_score = semantic_score
                # Never promote non-commercial / out-of-domain messages past the
                # deterministic commercial gate via semantic similarity alone.
                if (
                    semantic_score >= settings.semantic_threshold
                    and result.tier == "LOW"
                    and result.buyer_type == "CLIENT"
                    and LeadScorer.is_commercial_lead_type(result.lead_type)
                    and self.scorer._has_target_financial_domain(event.message_text)
                ):
                    result.score = min(100.0, result.score + 12.0)
                    if result.score >= self.scorer.high:
                        result.tier = "HIGH"
                    elif result.score >= self.scorer.medium:
                        result.tier = "MEDIUM"
                    else:
                        result.tier = "LOW"
                    result.reasons.append(f"semantic relevance {semantic_score:.2f}")

            action, _lead = await upsert_opportunity_lead(db, message, result)
            await persist_message_score(
                db,
                message,
                result,
                rule_version=self.scorer.rule_version,
            )
            await db.commit()
            if action in {"deduped_skipped", "deduped_updated"}:
                log.info(
                    "opportunity_deduped",
                    message_id=message.id,
                    community_id=message.community_id,
                    action=action,
                    tier=result.tier,
                    score=result.score,
                )
            return result


async def main() -> None:
    worker = Analyzer()
    try:
        await worker.run_forever()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
