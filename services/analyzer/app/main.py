import asyncio
import json
import os

from sqlalchemy import select

from shared.db import SessionLocal
from shared.events import MessageEvent
from shared.logging import configure_logging
from shared.models import Author, Community, Lead, Message
from shared.redis_bus import MESSAGES_STREAM, RedisBus
from shared.settings import get_settings
from services.analyzer.app.scoring import LeadScorer
from services.analyzer.app.semantic import SemanticMatcher

settings = get_settings()
configure_logging(settings.log_level)


class Analyzer:
    def __init__(self) -> None:
        self.bus = RedisBus(settings.redis_url)
        self.scorer = LeadScorer(settings.scoring_config)
        self.semantic = SemanticMatcher(settings.semantic_enabled, settings.semantic_model, settings.semantic_threshold)

    async def close(self) -> None:
        await self.bus.close()

    async def run_forever(self) -> None:
        consumer = f"analyzer-{os.getpid()}"
        while True:
            entries = await self.bus.consume(MESSAGES_STREAM, "analyzer-workers", consumer, block_ms=5000, count=50)
            if not entries:
                continue
            for _stream, messages in entries:
                for redis_message_id, fields in messages:
                    try:
                        event = MessageEvent.model_validate(json.loads(fields["payload"]))
                        await self.process(event)
                    except Exception as exc:
                        import structlog
                        structlog.get_logger().exception("message_analysis_failed", error=str(exc))
                    finally:
                        await self.bus.ack(MESSAGES_STREAM, "analyzer-workers", redis_message_id)

    async def process(self, event: MessageEvent) -> None:
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
                )
                db.add(community)
            else:
                community.telegram_chat_id = event.telegram_chat_id
                community.name = event.community_name
                community.username = event.community_username
                community.url = event.community_url

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
                message.text = event.message_text
                message.message_date = event.message_date

            result = self.scorer.score(event.message_text)
            semantic_score = self.semantic.score(event.message_text)
            if semantic_score is not None:
                result.semantic_score = semantic_score
                if semantic_score >= settings.semantic_threshold and result.tier == "LOW":
                    result.score = min(100.0, result.score + 18.0)
                    result.tier = "HIGH" if result.score >= 75 else "MEDIUM"
                    result.reasons.append(f"semantic relevance {semantic_score:.2f}")
            if result.tier == "LOW":
                await db.commit()
                return
            lead = await db.scalar(select(Lead).where(Lead.message_id == message.id))
            if not lead:
                lead = Lead(message_id=message.id)
                db.add(lead)
            lead.score = result.score
            lead.tier = result.tier
            lead.matched_keywords = json.dumps(result.matched_keywords, ensure_ascii=False)
            lead.matched_categories = json.dumps(result.matched_categories, ensure_ascii=False)
            lead.reasons = json.dumps(result.reasons, ensure_ascii=False)
            lead.semantic_score = result.semantic_score
            await db.commit()


async def main() -> None:
    worker = Analyzer()
    try:
        await worker.run_forever()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
