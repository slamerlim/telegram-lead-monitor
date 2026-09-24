"""Commercial discovery worker: build episodes + enqueue paired A/B shadow reviews.

Never promotes CRM leads. Never writes outreach events. Never touches 0007 tables.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.context import build_commercial_context
from shared.commercial_ai.discovery import DISCOVERY_VERSION, episode_key
from shared.commercial_ai.streams import (
    COMMERCIAL_DISCOVERY_DLQ,
    COMMERCIAL_DISCOVERY_GROUP,
    COMMERCIAL_DISCOVERY_STREAM,
    COMMERCIAL_EPISODE_SHADOW_STREAM,
)
from shared.db import SessionLocal
from shared.logging import configure_logging
from shared.models import (
    CommercialDiscoveryCandidate,
    CommercialEpisode,
    LabelReviewSample,
    Lead,
    Message,
)
from shared.redis_bus import RedisBus
from shared.settings import get_settings

logger = logging.getLogger("commercial_discovery")
MAX_DELIVERY = 5


async def process_discovery(payload: dict, scorer: LeadScorer, bus: RedisBus) -> None:
    settings = get_settings()
    if not settings.commercial_discovery_enabled and not settings.commercial_episode_shadow_enabled:
        return

    seed_id = int(payload["seed_message_id"])
    async with SessionLocal() as session:
        message = await session.get(Message, seed_id)
        if not message:
            logger.warning("discovery skip missing message %s", seed_id)
            return

        blind = await session.scalar(
            select(LabelReviewSample.id).where(LabelReviewSample.message_id == seed_id).limit(1)
        )
        if blind:
            cand = await session.scalar(
                select(CommercialDiscoveryCandidate).where(
                    CommercialDiscoveryCandidate.seed_message_id == seed_id,
                    CommercialDiscoveryCandidate.discovery_version
                    == (settings.commercial_discovery_version or DISCOVERY_VERSION),
                )
            )
            if cand:
                cand.status = "SKIPPED"
                cand.skip_reason = "blind_sample"
                await session.commit()
            return

        # Never touch existing AI_CONFIRMED seed messages.
        confirmed = await session.scalar(
            select(Lead.id).where(
                Lead.message_id == seed_id,
                Lead.status == "AI_CONFIRMED",
            ).limit(1)
        )
        if confirmed:
            cand = await session.scalar(
                select(CommercialDiscoveryCandidate).where(
                    CommercialDiscoveryCandidate.seed_message_id == seed_id,
                    CommercialDiscoveryCandidate.discovery_version
                    == (settings.commercial_discovery_version or DISCOVERY_VERSION),
                )
            )
            if cand:
                cand.status = "SKIPPED"
                cand.skip_reason = "existing_ai_confirmed"
                await session.commit()
            return

        cand = await session.scalar(
            select(CommercialDiscoveryCandidate).where(
                CommercialDiscoveryCandidate.seed_message_id == seed_id,
                CommercialDiscoveryCandidate.discovery_version
                == (settings.commercial_discovery_version or DISCOVERY_VERSION),
            )
        )
        if not cand:
            logger.info("discovery no candidate row seed=%s", seed_id)
            return

        ctx_b = await build_commercial_context(
            session,
            seed_id,
            include_related=True,
            scorer=scorer,
            max_messages=int(settings.commercial_episode_max_context_messages or 20),
            max_chars=int(settings.commercial_episode_max_context_chars or 12000),
            context_version=settings.commercial_context_version or "ctx_v1",
        )
        ekey = episode_key(
            author_id=message.author_id,
            topic_fp=cand.topic_fingerprint or ctx_b.topic_fingerprint,
            message_date=message.message_date,
            message_id=message.id,
        )
        existing_ep = await session.scalar(
            select(CommercialEpisode).where(
                CommercialEpisode.episode_key == ekey,
                CommercialEpisode.context_hash == ctx_b.context_hash,
            )
        )
        if existing_ep:
            episode = existing_ep
        else:
            dates = [m.message_date for m in ctx_b.members]
            episode = CommercialEpisode(
                episode_key=ekey,
                seed_message_id=seed_id,
                author_id=message.author_id,
                community_id=message.community_id,
                topic_fingerprint=cand.topic_fingerprint or ctx_b.topic_fingerprint or "none",
                episode_status="BUILT",
                context_version=ctx_b.context_version,
                context_hash=ctx_b.context_hash,
                members_json=json.dumps(ctx_b.to_members_json(), ensure_ascii=False),
                n_messages=len(ctx_b.members),
                n_excluded_blind=ctx_b.n_excluded_blind,
                window_start=min(dates),
                window_end=max(dates),
            )
            session.add(episode)
            await session.flush()

        cand.episode_id = episode.id
        cand.context_hash = ctx_b.context_hash
        cand.context_version = ctx_b.context_version
        cand.status = "EPISODE_BUILT"
        await session.commit()

        if not settings.commercial_episode_shadow_enabled:
            return

        for arm, include_related in (("A", False), ("B", True)):
            shadow_payload = {
                "seed_message_id": seed_id,
                "episode_id": episode.id,
                "experiment_arm": arm,
                "include_related": include_related,
                "context_version": ctx_b.context_version,
                "trigger": "discovery_worker",
            }
            try:
                await bus.publish(COMMERCIAL_EPISODE_SHADOW_STREAM, shadow_payload, maxlen=10_000)
            except Exception as exc:
                logger.warning("shadow_enqueue_failed arm=%s err=%s", arm, exc)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    bus = RedisBus(settings.redis_url)
    scorer = LeadScorer(settings.scoring_config)
    consumer = f"disc-{os.getpid()}"
    deliveries: dict[str, int] = {}

    if not settings.commercial_discovery_enabled:
        logger.warning("COMMERCIAL_DISCOVERY_ENABLED=false — worker idle")

    try:
        await bus.redis.xgroup_create(
            COMMERCIAL_DISCOVERY_STREAM, COMMERCIAL_DISCOVERY_GROUP, id="0", mkstream=True
        )
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    while True:
        if not settings.commercial_discovery_enabled:
            await asyncio.sleep(5)
            settings = get_settings()
            continue
        rows = await bus.consume(
            COMMERCIAL_DISCOVERY_STREAM,
            COMMERCIAL_DISCOVERY_GROUP,
            consumer,
            block_ms=5000,
            count=5,
        )
        if not rows:
            continue
        for _stream, messages in rows:
            for msg_id, fields in messages:
                try:
                    payload = json.loads(fields.get("payload") or "{}")
                    await process_discovery(payload, scorer, bus)
                    await bus.ack(COMMERCIAL_DISCOVERY_STREAM, COMMERCIAL_DISCOVERY_GROUP, msg_id)
                    deliveries.pop(msg_id, None)
                except Exception as exc:
                    n = deliveries.get(msg_id, 0) + 1
                    deliveries[msg_id] = n
                    logger.exception("discovery_process_failed id=%s n=%s err=%s", msg_id, n, exc)
                    if n >= MAX_DELIVERY:
                        try:
                            await bus.publish(
                                COMMERCIAL_DISCOVERY_DLQ,
                                {"orig_id": msg_id, "error": str(exc), "fields": fields},
                                maxlen=5_000,
                            )
                            await bus.ack(
                                COMMERCIAL_DISCOVERY_STREAM, COMMERCIAL_DISCOVERY_GROUP, msg_id
                            )
                        except Exception:
                            logger.exception("discovery_dlq_failed")
                        deliveries.pop(msg_id, None)


if __name__ == "__main__":
    asyncio.run(main())
