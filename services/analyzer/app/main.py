import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select

from services.analyzer.app.scoring import LeadScorer
from services.analyzer.app.semantic import SemanticMatcher
from shared.commercial_ai.discovery import DISCOVERY_VERSION, signals_json
from shared.commercial_ai.streams import COMMERCIAL_DISCOVERY_STREAM
from shared.db import SessionLocal
from shared.events import MessageEvent
from shared.lead_write import upsert_opportunity_lead
from shared.logging import configure_logging
from shared.models import Author, CommercialDiscoveryCandidate, Community, Message
from shared.outbox import enqueue
from shared.redis_bus import COMMERCIAL_AI_REVIEW_STREAM, MESSAGES_STREAM, RedisBus, consumer_name
from shared.commercial_ai.prompts import PROMPT_VERSION
from shared.commercial_ai.policy import POLICY_VERSION
from shared.score_persist import persist_message_score
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
        consumer = consumer_name("analyzer")
        log.info("analyzer_started", consumer=consumer, semantic_enabled=settings.semantic_enabled)
        while True:
            entries = await self.bus.consume(
                MESSAGES_STREAM,
                "analyzer-workers",
                consumer,
                block_ms=5000,
                count=1,
            )
            if not entries:
                continue
            for _stream, messages in entries:
                for redis_message_id, fields in messages:
                    if not await self.bus.handle(
                        MESSAGES_STREAM, "analyzer-workers", consumer,
                        redis_message_id, fields, self.process_payload,
                    ):
                        self.errors += 1

    async def process_payload(self, payload: dict) -> None:
        result = await self.process(MessageEvent.model_validate(payload))
        self.processed += 1
        self.high += result.tier == "HIGH"
        self.medium += result.tier == "MEDIUM"
        if self.processed % 250 == 0:
            elapsed = max(time.monotonic() - self.started_at, 0.001)
            log.info("analyzer_progress", processed=self.processed, high=self.high,
                     medium=self.medium, errors=self.errors,
                     messages_per_second=round(self.processed / elapsed, 2))

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
                    reply_to_telegram_message_id=getattr(
                        event, "reply_to_telegram_message_id", None
                    ),
                )
                db.add(message)
                await db.flush()
            else:
                message.author_id = author.id if author else None
                message.message_url = event.message_url
                message.message_date = event.message_date
                message.text = event.message_text
                message.is_reply = event.is_reply
                # Only set when present — do not null out historical unknowns.
                if getattr(event, "reply_to_telegram_message_id", None) is not None:
                    message.reply_to_telegram_message_id = event.reply_to_telegram_message_id

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

            action, lead = await upsert_opportunity_lead(db, message, result)
            await persist_message_score(
                db,
                message,
                result,
                rule_version=self.scorer.rule_version,
            )

            discovery_payload = None
            if (
                settings.commercial_discovery_enabled
                and result.tier == "LOW"
            ):
                feats = self.scorer.discovery_features(event.message_text, version=settings.commercial_discovery_version)
                if feats.eligible:
                    # Rate limit candidates/hour (best-effort).
                    max_c = int(settings.commercial_discovery_max_candidates_per_hour or 0)
                    over_rate = False
                    if max_c > 0:
                        since = datetime.now(timezone.utc) - timedelta(hours=1)
                        n_hour = int(
                            (
                                await db.scalar(
                                    select(func.count(CommercialDiscoveryCandidate.id)).where(
                                        CommercialDiscoveryCandidate.created_at >= since
                                    )
                                )
                            )
                            or 0
                        )
                        over_rate = n_hour >= max_c
                    existing = await db.scalar(
                        select(CommercialDiscoveryCandidate.id).where(
                            CommercialDiscoveryCandidate.seed_message_id == message.id,
                            CommercialDiscoveryCandidate.discovery_version
                            == (settings.commercial_discovery_version or DISCOVERY_VERSION),
                        )
                    )
                    if existing is None and not over_rate:
                        cand = CommercialDiscoveryCandidate(
                            seed_message_id=message.id,
                            community_id=message.community_id,
                            author_id=message.author_id,
                            discovery_version=settings.commercial_discovery_version or DISCOVERY_VERSION,
                            source="analyzer",
                            trigger_type=feats.trigger_type or "unknown",
                            trigger_score=feats.trigger_score,
                            scorer_tier=result.tier,
                            signals_json=signals_json(feats),
                            topic_fingerprint=feats.topic_fingerprint or "none",
                            recall_rank=feats.trigger_score,
                            status="PENDING",
                            context_version=settings.commercial_context_version,
                        )
                        db.add(cand)
                        discovery_payload = {
                            "seed_message_id": message.id,
                            "community_id": message.community_id,
                            "author_id": message.author_id,
                            "trigger_type": feats.trigger_type,
                            "context_version": settings.commercial_context_version,
                            "discovery_version": settings.commercial_discovery_version or DISCOVERY_VERSION,
                            "topic_fingerprint": feats.topic_fingerprint,
                        }

            if discovery_payload is not None:
                await enqueue(
                    db, COMMERCIAL_DISCOVERY_STREAM, discovery_payload,
                    key=f"discovery:{message.id}:{discovery_payload['discovery_version']}",
                )
            if (
                settings.commercial_ai_enabled
                and lead is not None
                and result.tier != "LOW"
                and (lead.status or "NEW").upper() == "NEW"
            ):
                # Deduplication can retain another message as the canonical lead.
                canonical = message if lead.message_id == message.id else await db.get(Message, lead.message_id)
                if canonical is not None:
                    digest = hashlib.sha256((canonical.text or "").encode()).hexdigest()
                    await enqueue(
                        db, COMMERCIAL_AI_REVIEW_STREAM,
                        {"lead_id": lead.id, "message_id": canonical.id,
                         "text_sha256": digest, "trigger": "analyzer_upsert"},
                        key=f"review:{lead.id}:{digest}:{PROMPT_VERSION}:{POLICY_VERSION}",
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
