#!/usr/bin/env python3
"""Bounded commercial episode A/B shadow experiment runner.

Selects LOW discovery candidates from a recent window, builds episodes, runs
paired A/B reviews via Fake or SDK backend. NEVER promotes CRM / outreach.

Usage (inside api container or venv):
  python -m scripts.run_commercial_episode_shadow --limit 5 --backend fake
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, select

# Ensure repo root on path when run as script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from services.commercial_discovery.app.main import process_discovery
from services.commercial_episode_shadow.app.main import _backend, process_shadow
from shared.commercial_ai.discovery import DISCOVERY_VERSION, signals_json
from shared.db import SessionLocal
from shared.models import (
    CommercialAIReview,
    CommercialDiscoveryCandidate,
    LabelReviewSample,
    Lead,
    Message,
    MessageScore,
)
from shared.redis_bus import RedisBus
from shared.settings import get_settings


async def sweep_candidates(*, days: int, limit: int, scorer: LeadScorer) -> list[int]:
    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    seed_ids: list[int] = []
    async with SessionLocal() as session:
        # Exclude blind + AI_CONFIRMED seeds.
        blind_q = select(LabelReviewSample.message_id)
        confirmed_q = select(Lead.message_id).where(Lead.status == "AI_CONFIRMED")
        rows = (
            await session.execute(
                select(MessageScore.message_id, Message.id, Message.text, Message.author_id, Message.community_id)
                .join(Message, Message.id == MessageScore.message_id)
                .where(
                    MessageScore.tier == "LOW",
                    MessageScore.scored_at >= since,
                    MessageScore.message_id.notin_(blind_q),
                    MessageScore.message_id.notin_(confirmed_q),
                )
                .order_by(MessageScore.scored_at.desc())
                .limit(limit * 20)
            )
        ).all()

        for mid, _msgid, text, author_id, community_id in rows:
            if len(seed_ids) >= limit:
                break
            feats = scorer.discovery_features(text or "")
            if not feats.eligible:
                continue
            existing = await session.scalar(
                select(CommercialDiscoveryCandidate.id).where(
                    CommercialDiscoveryCandidate.seed_message_id == mid,
                    CommercialDiscoveryCandidate.discovery_version
                    == (settings.commercial_discovery_version or DISCOVERY_VERSION),
                )
            )
            if existing is None:
                session.add(
                    CommercialDiscoveryCandidate(
                        seed_message_id=mid,
                        community_id=community_id,
                        author_id=author_id,
                        discovery_version=settings.commercial_discovery_version or DISCOVERY_VERSION,
                        source="shadow_sweep",
                        trigger_type=feats.trigger_type or "unknown",
                        trigger_score=feats.trigger_score,
                        scorer_tier="LOW",
                        signals_json=signals_json(feats),
                        topic_fingerprint=feats.topic_fingerprint or "none",
                        recall_rank=feats.trigger_score,
                        status="PENDING",
                        context_version=settings.commercial_context_version,
                    )
                )
            seed_ids.append(mid)
        await session.commit()
    return seed_ids


async def run_experiment(*, limit: int, days: int, backend_kind: str) -> dict:
    settings = get_settings()
    # Temporarily force backend via env already set; _backend reads settings.
    scorer = LeadScorer(settings.scoring_config)
    bus = RedisBus(settings.redis_url)
    seeds = await sweep_candidates(days=days, limit=limit, scorer=scorer)
    # Build episodes
    for sid in seeds:
        await process_discovery(
            {
                "seed_message_id": sid,
                "trigger_type": "shadow_sweep",
                "context_version": settings.commercial_context_version,
            },
            scorer,
            bus,
        )
    backend = _backend()
    for sid in seeds:
        for arm, include in (("A", False), ("B", True)):
            await process_shadow(
                {
                    "seed_message_id": sid,
                    "experiment_arm": arm,
                    "include_related": include,
                    "trigger": "shadow_cli",
                },
                backend,
                scorer,
            )

    # Aggregate paired results
    async with SessionLocal() as session:
        decisions = (
            await session.execute(
                select(CommercialAIReview).where(
                    CommercialAIReview.row_kind == "DECISION",
                    CommercialAIReview.experiment_arm.in_(("A", "B")),
                    CommercialAIReview.message_id.in_(seeds) if seeds else and_(False),
                )
            )
        ).scalars().all()

    by_seed: dict[int, dict[str, str]] = {}
    for d in decisions:
        by_seed.setdefault(d.message_id, {})[d.experiment_arm or "?"] = d.decision or "ERROR"

    a_counts = {"AI_CONFIRMED": 0, "AI_CANDIDATE": 0, "AI_UNCERTAIN": 0, "AI_REJECTED": 0, "ERROR": 0}
    b_counts = dict(a_counts)
    incremental = 0
    lost = 0
    paired = 0
    discordance = 0
    for sid, arms in by_seed.items():
        if "A" in arms and "B" in arms:
            paired += 1
            if arms["A"] != arms["B"]:
                discordance += 1
            if arms["B"] == "AI_CONFIRMED" and arms["A"] != "AI_CONFIRMED":
                incremental += 1
            if arms["A"] == "AI_CONFIRMED" and arms["B"] != "AI_CONFIRMED":
                lost += 1
        if "A" in arms and arms["A"] in a_counts:
            a_counts[arms["A"]] += 1
        if "B" in arms and arms["B"] in b_counts:
            b_counts[arms["B"]] += 1

    report = {
        "seeds": seeds,
        "n_seeds": len(seeds),
        "paired": paired,
        "arm_a": a_counts,
        "arm_b": b_counts,
        "incremental_confirmations": incremental,
        "lost_confirmations": lost,
        "paired_discordance": discordance,
        "paired_discordance_rate": (discordance / paired) if paired else 0.0,
        "backend": backend_kind,
    }
    await bus.close()
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--backend", choices=("fake", "sdk"), default="fake")
    p.add_argument("--out", type=str, default="")
    args = p.parse_args()
    # Backend is controlled by COMMERCIAL_AI_BACKEND in env; note for operator.
    report = asyncio.run(run_experiment(limit=args.limit, days=args.days, backend_kind=args.backend))
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
