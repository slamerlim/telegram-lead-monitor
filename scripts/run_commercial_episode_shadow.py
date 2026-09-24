#!/usr/bin/env python3
"""Clean paired A/B commercial episode shadow runner (disc_v2).

Selects LOW discovery candidates, stratifies by available context size, runs
paired A/B reviews via Cursor SDK. NEVER promotes CRM / outreach.

Usage:
  python -m scripts.run_commercial_episode_shadow --analyze-only --days 7 --pool 200
  python -m scripts.run_commercial_episode_shadow --limit 5 --backend sdk --stratify \\
      --experiment-id aival_episode_ab_disc_v2_20260924
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, select, text as sql_text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from services.commercial_discovery.app.main import process_discovery
from services.commercial_episode_shadow.app.main import _backend, process_shadow
from shared.commercial_ai.context import build_commercial_context
from shared.commercial_ai.discovery import DISCOVERY_VERSION, signals_json
from shared.db import SessionLocal
from shared.models import (
    CommercialAIReview,
    CommercialDiscoveryCandidate,
    CommercialEpisode,
    LabelReviewSample,
    Lead,
    Message,
)
from shared.redis_bus import RedisBus
from shared.settings import get_settings


def _disc_ver(settings) -> str:
    return settings.commercial_discovery_version or DISCOVERY_VERSION


async def _already_paired(session, seed_id: int) -> bool:
    arms = set(
        (
            await session.execute(
                select(CommercialAIReview.experiment_arm).where(
                    CommercialAIReview.message_id == seed_id,
                    CommercialAIReview.row_kind == "DECISION",
                    CommercialAIReview.experiment_arm.in_(("A", "B")),
                )
            )
        ).scalars().all()
    )
    return "A" in arms and "B" in arms


async def gather_pool(*, days: int, pool: int, scorer: LeadScorer) -> list[dict]:
    """Return eligible disc_v2 candidates with precomputed context sizes (no AI)."""
    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict] = []
    async with SessionLocal() as session:
        pre = (
            await session.execute(
                sql_text(
                    """
                    SELECT m.id, m.text, m.author_id, m.community_id, s.score,
                           s.commercial_score, s.technical_score, s.lead_type
                    FROM messages m
                    JOIN message_scores s ON s.message_id = m.id
                    WHERE s.tier = 'LOW'
                      AND s.scored_at >= :since
                      AND m.id NOT IN (SELECT message_id FROM label_review_samples)
                      AND m.id NOT IN (
                        SELECT message_id FROM leads WHERE status = 'AI_CONFIRMED'
                      )
                      AND (
                        (s.commercial_score > 0 AND s.technical_score > 0)
                        OR m.text ~* 'trading bot|торговый бот|bybit|binance api|pybit'
                        OR m.text ~* '(hire|looking to (pay|hire)|need).{0,40}(bot|trading|quant)'
                        OR m.text ~* 'freelance opportunity.{0,80}(bot|trading|quant|bybit)'
                        OR m.text ~* 'починить|кастом.{0,20}бот|ищу.{0,30}(бот|разработ)'
                        OR m.text ~* 'commission.{0,40}(bot|trading)|build.{0,40}trading bot'
                      )
                      AND m.text !~* 'Daily USDT Buyers'
                      AND m.text !~* 'BITRUE 8TH ANNIVERSARY'
                      AND m.text !~* 'Do you want to make money'
                    ORDER BY (s.commercial_score + s.technical_score) DESC, m.message_date DESC
                    LIMIT :cap
                    """
                ),
                {"since": since, "cap": max(pool * 5, 800)},
            )
        ).all()

        for mid, text, author_id, community_id, score, cscore, tscore, lead_type in pre:
            if await _already_paired(session, mid):
                continue
            # Exclude seeds that already have a disc_v1 candidate (prior cohort).
            old = await session.scalar(
                select(CommercialDiscoveryCandidate.id).where(
                    CommercialDiscoveryCandidate.seed_message_id == mid,
                    CommercialDiscoveryCandidate.discovery_version == "disc_v1",
                )
            )
            if old is not None:
                continue
            feats = scorer.discovery_features(text or "")
            if not feats.eligible:
                continue

            # Context availability diagnostics (author history in DB).
            same_author_total = 0
            same_author_24h = 0
            same_author_30d = 0
            if author_id is not None:
                same_author_total = int(
                    (
                        await session.scalar(
                            sql_text(
                                "SELECT COUNT(*) FROM messages WHERE author_id=:a AND id<>:m"
                            ),
                            {"a": author_id, "m": mid},
                        )
                    )
                    or 0
                )
                same_author_24h = int(
                    (
                        await session.scalar(
                            sql_text(
                                """
                                SELECT COUNT(*) FROM messages m
                                JOIN messages seed ON seed.id=:m
                                WHERE m.author_id=:a AND m.id<>:m
                                  AND m.community_id=seed.community_id
                                  AND m.message_date >= seed.message_date - interval '24 hours'
                                  AND m.message_date <= seed.message_date + interval '1 hour'
                                """
                            ),
                            {"a": author_id, "m": mid},
                        )
                    )
                    or 0
                )
                same_author_30d = int(
                    (
                        await session.scalar(
                            sql_text(
                                """
                                SELECT COUNT(*) FROM messages m
                                JOIN messages seed ON seed.id=:m
                                WHERE m.author_id=:a AND m.id<>:m
                                  AND m.community_id=seed.community_id
                                  AND m.message_date >= seed.message_date - interval '30 days'
                                  AND m.message_date < seed.message_date
                                """
                            ),
                            {"a": author_id, "m": mid},
                        )
                    )
                    or 0
                )

            ctx = await build_commercial_context(
                session,
                mid,
                include_related=True,
                scorer=scorer,
                max_messages=int(settings.commercial_episode_max_context_messages or 20),
                max_chars=int(settings.commercial_episode_max_context_chars or 12000),
                context_version=settings.commercial_context_version or "ctx_v1",
            )
            out.append(
                {
                    "seed_message_id": mid,
                    "author_id": author_id,
                    "community_id": community_id,
                    "score": float(score or 0),
                    "commercial_score": float(cscore or 0),
                    "technical_score": float(tscore or 0),
                    "lead_type": lead_type,
                    "trigger_type": feats.trigger_type,
                    "topic_fingerprint": feats.topic_fingerprint,
                    "context_size": len(ctx.members),
                    "context_hash": ctx.context_hash,
                    "n_excluded": ctx.n_excluded,
                    "same_author_total": same_author_total,
                    "same_author_24h": same_author_24h,
                    "same_author_30d": same_author_30d,
                    "relations": Counter(m.relation for m in ctx.members),
                    "history_exists_not_selected": (
                        same_author_30d > 0 and len(ctx.members) == 1
                    ),
                    "no_related_history": same_author_30d == 0 and same_author_24h == 0,
                }
            )
            if len(out) >= pool:
                break
    return out


def stratify_sample(pool: list[dict], limit: int) -> list[dict]:
    """Prefer ~1/3 each of context sizes 1 / 2-3 / 4+ when available."""
    g1 = [c for c in pool if c["context_size"] == 1]
    g2 = [c for c in pool if 2 <= c["context_size"] <= 3]
    g3 = [c for c in pool if c["context_size"] >= 4]
    if limit <= 0:
        return []
    # Natural distribution if any stratum empty.
    if not g2 and not g3:
        return pool[:limit]
    target = max(1, limit // 3)
    picked: list[dict] = []
    for group in (g1, g2, g3):
        for c in group[:target]:
            if len(picked) >= limit:
                break
            picked.append(c)
    # Fill remainder from remaining pool order.
    seen = {c["seed_message_id"] for c in picked}
    for c in pool:
        if len(picked) >= limit:
            break
        if c["seed_message_id"] not in seen:
            picked.append(c)
            seen.add(c["seed_message_id"])
    return picked[:limit]


def analyze_report(pool: list[dict], selected: list[dict] | None = None) -> dict:
    def _stats(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0}
        sizes = [r["context_size"] for r in rows]
        return {
            "n": len(rows),
            "unique_authors": len({r["author_id"] for r in rows if r["author_id"]}),
            "unique_communities": len({r["community_id"] for r in rows}),
            "trigger_distribution": dict(Counter(r["trigger_type"] for r in rows)),
            "context_size_histogram": dict(Counter(sizes)),
            "median_context_size": sorted(sizes)[len(sizes) // 2],
            "pct_context_gt1": round(100 * sum(1 for s in sizes if s > 1) / len(sizes), 1),
            "pct_no_related_history": round(
                100 * sum(1 for r in rows if r["no_related_history"]) / len(rows), 1
            ),
            "pct_history_exists_not_selected": round(
                100
                * sum(1 for r in rows if r["history_exists_not_selected"])
                / len(rows),
                1,
            ),
            "score_p50": sorted(r["score"] for r in rows)[len(rows) // 2],
        }

    return {
        "discovery_version": DISCOVERY_VERSION,
        "pool": _stats(pool),
        "selected": _stats(selected or []),
        "selected_seed_ids": [r["seed_message_id"] for r in (selected or [])],
    }


async def persist_candidates(rows: list[dict], experiment_id: str) -> None:
    settings = get_settings()
    ver = _disc_ver(settings)
    async with SessionLocal() as session:
        for r in rows:
            existing = await session.scalar(
                select(CommercialDiscoveryCandidate.id).where(
                    CommercialDiscoveryCandidate.seed_message_id == r["seed_message_id"],
                    CommercialDiscoveryCandidate.discovery_version == ver,
                )
            )
            if existing is not None:
                continue
            msg = await session.get(Message, r["seed_message_id"])
            if not msg:
                continue
            feats_json = {
                "experiment_id": experiment_id,
                "trigger_type": r["trigger_type"],
                "topic_fingerprint": r["topic_fingerprint"],
                "context_size_pre": r["context_size"],
                "same_author_24h": r["same_author_24h"],
                "same_author_30d": r["same_author_30d"],
                "history_exists_not_selected": r["history_exists_not_selected"],
                "no_related_history": r["no_related_history"],
            }
            session.add(
                CommercialDiscoveryCandidate(
                    seed_message_id=r["seed_message_id"],
                    community_id=r["community_id"],
                    author_id=r["author_id"],
                    discovery_version=ver,
                    source="shadow_sweep_v2",
                    trigger_type=r["trigger_type"] or "unknown",
                    trigger_score=float(r.get("commercial_score") or 0),
                    scorer_tier="LOW",
                    signals_json=json.dumps(feats_json, ensure_ascii=False, sort_keys=True),
                    topic_fingerprint=r["topic_fingerprint"] or "none",
                    recall_rank=float(r.get("commercial_score") or 0),
                    status="PENDING",
                    context_version=settings.commercial_context_version,
                )
            )
        await session.commit()


async def run_experiment(
    *,
    limit: int,
    days: int,
    backend_kind: str,
    stratify: bool,
    experiment_id: str,
    pool_size: int,
    analyze_only: bool,
) -> dict:
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    t0 = time.monotonic()
    pool = await gather_pool(days=days, pool=pool_size, scorer=scorer)
    selected = stratify_sample(pool, limit) if stratify else pool[:limit]
    analysis = analyze_report(pool, selected)
    analysis["experiment_id"] = experiment_id
    analysis["backend"] = backend_kind
    analysis["discovery_version"] = _disc_ver(settings)

    if analyze_only:
        analysis["runtime_sec"] = round(time.monotonic() - t0, 1)
        return analysis

    await persist_candidates(selected, experiment_id)
    bus = RedisBus(settings.redis_url)
    backend = _backend()
    seeds = [r["seed_message_id"] for r in selected]
    seed_meta = {r["seed_message_id"]: r for r in selected}

    for sid in seeds:
        await process_discovery(
            {
                "seed_message_id": sid,
                "trigger_type": "shadow_sweep_v2",
                "context_version": settings.commercial_context_version,
                "experiment_id": experiment_id,
            },
            scorer,
            bus,
        )

    model_calls = 0
    for sid in seeds:
        for arm, include in (("A", False), ("B", True)):
            await process_shadow(
                {
                    "seed_message_id": sid,
                    "experiment_arm": arm,
                    "include_related": include,
                    "trigger": experiment_id,
                },
                backend,
                scorer,
            )
            model_calls += 3  # A/B/C roster

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
        episodes = {}
        for sid in seeds:
            ep = await session.scalar(
                select(CommercialEpisode)
                .where(CommercialEpisode.seed_message_id == sid)
                .order_by(CommercialEpisode.id.desc())
                .limit(1)
            )
            if ep:
                episodes[sid] = ep

    by_seed: dict[int, dict[str, str]] = {}
    evidence_ok = 0
    evidence_bad = 0
    for d in decisions:
        by_seed.setdefault(d.message_id, {})[d.experiment_arm or "?"] = d.decision or "ERROR"
        if d.experiment_arm == "B":
            if d.evidence_valid is True:
                evidence_ok += 1
            elif d.evidence_valid is False:
                evidence_bad += 1

    a_counts = Counter()
    b_counts = Counter()
    incremental = lost = paired = discordance = 0
    by_size = defaultdict(lambda: {"a": Counter(), "b": Counter(), "inc": 0, "n": 0})

    for sid, arms in by_seed.items():
        meta = seed_meta.get(sid, {})
        sz = int(meta.get("context_size") or (episodes[sid].n_messages if sid in episodes else 1))
        bucket = "1" if sz == 1 else ("2-3" if sz <= 3 else "4+")
        if "A" in arms:
            a_counts[arms["A"]] += 1
            by_size[bucket]["a"][arms["A"]] += 1
        if "B" in arms:
            b_counts[arms["B"]] += 1
            by_size[bucket]["b"][arms["B"]] += 1
        if "A" in arms and "B" in arms:
            paired += 1
            by_size[bucket]["n"] += 1
            if arms["A"] != arms["B"]:
                discordance += 1
            if arms["B"] == "AI_CONFIRMED" and arms["A"] != "AI_CONFIRMED":
                incremental += 1
                by_size[bucket]["inc"] += 1
            if arms["A"] == "AI_CONFIRMED" and arms["B"] != "AI_CONFIRMED":
                lost += 1

    report = {
        **analysis,
        "seeds": seeds,
        "n_seeds": len(seeds),
        "paired": paired,
        "arm_a": dict(a_counts),
        "arm_b": dict(b_counts),
        "incremental_confirmations": incremental,
        "lost_confirmations": lost,
        "paired_discordance": discordance,
        "paired_discordance_rate": (discordance / paired) if paired else 0.0,
        "evidence_valid_b": evidence_ok,
        "evidence_invalid_b": evidence_bad,
        "by_context_size": {
            k: {
                "n": v["n"],
                "arm_a": dict(v["a"]),
                "arm_b": dict(v["b"]),
                "incremental_confirmed": v["inc"],
            }
            for k, v in by_size.items()
        },
        "model_calls_est": model_calls,
        "runtime_sec": round(time.monotonic() - t0, 1),
        "per_seed": [
            {
                "seed_message_id": sid,
                "context_size_pre": seed_meta.get(sid, {}).get("context_size"),
                "episode_n": episodes[sid].n_messages if sid in episodes else None,
                "a": by_seed.get(sid, {}).get("A"),
                "b": by_seed.get(sid, {}).get("B"),
                "trigger": seed_meta.get(sid, {}).get("trigger_type"),
                "no_related_history": seed_meta.get(sid, {}).get("no_related_history"),
                "history_exists_not_selected": seed_meta.get(sid, {}).get(
                    "history_exists_not_selected"
                ),
            }
            for sid in seeds
        ],
    }
    await bus.close()
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--pool", type=int, default=200)
    p.add_argument("--backend", choices=("fake", "sdk"), default="sdk")
    p.add_argument("--stratify", action="store_true")
    p.add_argument("--analyze-only", action="store_true")
    p.add_argument(
        "--experiment-id",
        type=str,
        default=f"aival_episode_ab_disc_v2_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
    )
    p.add_argument("--out", type=str, default="")
    args = p.parse_args()
    report = asyncio.run(
        run_experiment(
            limit=args.limit,
            days=args.days,
            backend_kind=args.backend,
            stratify=args.stratify,
            experiment_id=args.experiment_id,
            pool_size=args.pool,
            analyze_only=args.analyze_only,
        )
    )
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
