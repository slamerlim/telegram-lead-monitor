#!/usr/bin/env python3
"""Grow human_labels toward M1 gates without full-corpus reprocess.

Provisional labels (labeled_by=agent_*_seed) — still require human confirmation for
true M1 independence, but they populate the evaluation store and queue coverage.

Strategies:
  true_lead  — LaborX / commercial RFQ phrase hits that score HIGH/MEDIUM commercial
  false_pos  — clear FP slices (exchange promo, job seeker, vacancy, freelancer-of-week)
               that score LOW (or were never leads)
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from sqlalchemy import select, text

from services.analyzer.app.scoring import LeadScorer
from shared.db import SessionLocal
from shared.models import Community, HumanLabel, Message
from shared.opportunity import opportunity_key as make_ok

COMMERCIAL_KEEP = {
    "BOT_PURCHASE",
    "BOT_REPAIR",
    "BOT_CUSTOMIZATION",
    "STRATEGY_IMPLEMENTATION",
    "TRADING_SYSTEM_CONTRACT",
    "QUANT_ENGINEERING_CONTRACT",
    "ML_AI_ENGINEERING_CONTRACT",
    "COPY_TRADING_PROJECT",
    "ARBITRAGE_PROJECT",
    "MARKET_MAKING_PROJECT",
    "SOLANA_DEX_BOT_PROJECT",
}

TRUE_SQL = [
    (
        "laborx_freelance_bot",
        """
        SELECT m.id, c.username, m.text, m.community_id
        FROM messages m
        JOIN communities c ON c.id = m.community_id
        WHERE c.username ILIKE '%laborx%'
          AND m.text ILIKE '%Freelance Opportunity%'
          AND (
            m.text ILIKE '%Bot Developer%'
            OR m.text ILIKE '%trading bot%'
            OR m.text ILIKE '%Trading Bot%'
            OR m.text ILIKE '%fix%bot%'
            OR m.text ILIKE '%bot%fix%'
          )
          AND m.text ILIKE '%Budget%'
        ORDER BY m.id DESC
        LIMIT :lim
        """,
    ),
    (
        "en_buy_bot",
        """
        SELECT m.id, c.username, m.text, m.community_id
        FROM messages m
        JOIN communities c ON c.id = m.community_id
        WHERE m.text ILIKE '%looking to buy%'
          AND m.text ILIKE '%bot%'
          AND (m.text ILIKE '%budget%' OR m.text ILIKE '%$%')
        ORDER BY m.id DESC
        LIMIT :lim
        """,
    ),
    (
        "en_need_fix",
        """
        SELECT m.id, c.username, m.text, m.community_id
        FROM messages m
        JOIN communities c ON c.id = m.community_id
        WHERE m.text ILIKE '%need a developer%'
          AND (m.text ILIKE '%fix%' OR m.text ILIKE '%build%' OR m.text ILIKE '%implement%')
          AND (m.text ILIKE '%bot%' OR m.text ILIKE '%trading%')
        ORDER BY m.id DESC
        LIMIT :lim
        """,
    ),
    (
        "ru_nuzhen_bot",
        """
        SELECT m.id, c.username, m.text, m.community_id
        FROM messages m
        JOIN communities c ON c.id = m.community_id
        WHERE m.text ILIKE '%нужен%'
          AND m.text ILIKE '%бот%'
          AND (m.text ILIKE '%бюджет%' OR m.text ILIKE '%$%' OR m.text ILIKE '%куплю%')
        ORDER BY m.id DESC
        LIMIT :lim
        """,
    ),
]

FP_SQL = [
    (
        "MARKETING_BROADCAST",
        """
        SELECT m.id, c.username, m.text, m.community_id
        FROM messages m
        JOIN communities c ON c.id = m.community_id
        WHERE c.username ILIKE ANY(ARRAY['bitunixglobal','BloFin_Official','BybitEnglish','binanceexchange','BingXOfficial'])
          AND (
            m.text ILIKE '%challenge%'
            OR m.text ILIKE '%how to join%'
            OR m.text ILIKE '%sign up%'
            OR m.text ILIKE '%futures grid%'
            OR m.text ILIKE '%announcement%'
          )
        ORDER BY m.id DESC
        LIMIT :lim
        """,
    ),
    (
        "JOB_SEEKER",
        """
        SELECT m.id, c.username, m.text, m.community_id
        FROM messages m
        JOIN communities c ON c.id = m.community_id
        WHERE (
            m.text ILIKE '%#resume%'
            OR m.text ILIKE '%#резюме%'
            OR m.text ILIKE '%open to work%'
            OR m.text ILIKE '%ищу работу%'
            OR m.text ILIKE '%available for web3%'
          )
        ORDER BY m.id DESC
        LIMIT :lim
        """,
    ),
    (
        "JOB_VACANCY",
        """
        SELECT m.id, c.username, m.text, m.community_id
        FROM messages m
        JOIN communities c ON c.id = m.community_id
        WHERE (
            m.text ILIKE '%#vacancy%'
            OR m.text ILIKE '%#вакансия%'
            OR m.text ILIKE '%#Recruitment%'
            OR m.text ILIKE '%we are hiring%'
            OR m.text ILIKE '%Responsibilities:%'
          )
        ORDER BY m.id DESC
        LIMIT :lim
        """,
    ),
    (
        "SERVICE_AD",
        """
        SELECT m.id, c.username, m.text, m.community_id
        FROM messages m
        JOIN communities c ON c.id = m.community_id
        WHERE (
            m.text ILIKE '%FREELANCER OF THE WEEK%'
            OR m.text ILIKE '%Gig of the day%'
            OR m.text ILIKE '%hire me%'
            OR m.text ILIKE '%$/hr%'
            OR m.text ILIKE '%$%/hr%'
          )
        ORDER BY m.id DESC
        LIMIT :lim
        """,
    ),
]


async def upsert_label(
    db,
    *,
    mid: int,
    cid: int,
    body: str,
    label: str,
    fp_class: str | None,
    by: str,
    notes: str,
) -> str:
    existing = await db.scalar(select(HumanLabel).where(HumanLabel.message_id == mid))
    if existing and existing.label == "TRUE_LEAD" and label != "TRUE_LEAD":
        return "skip_true"
    if existing and existing.label == label:
        return "exists"
    if not existing:
        existing = HumanLabel(message_id=mid, community_id=cid)
        db.add(existing)
        action = "created"
    else:
        action = "updated"
    existing.community_id = cid
    existing.opportunity_key = make_ok(cid, body)
    existing.label = label
    existing.fp_class = fp_class
    existing.labeled_by = by
    existing.notes = notes
    return action


async def seed_true(scorer: LeadScorer, limit: int) -> Counter:
    stats: Counter = Counter()
    async with SessionLocal() as db:
        seen: set[int] = set()
        for name, sql in TRUE_SQL:
            rows = list((await db.execute(text(sql), {"lim": limit})).all())
            stats[f"cand_{name}"] = len(rows)
            for mid, user, body, cid in rows:
                if mid in seen:
                    continue
                seen.add(mid)
                r = scorer.score(body or "", community_username=user)
                if r.tier not in {"HIGH", "MEDIUM"}:
                    stats["score_low"] += 1
                    continue
                if r.buyer_type not in {"CLIENT", "RECRUITER"}:
                    stats["buyer_reject"] += 1
                    continue
                if r.lead_type not in COMMERCIAL_KEEP:
                    stats["type_reject"] += 1
                    continue
                action = await upsert_label(
                    db,
                    mid=mid,
                    cid=cid,
                    body=body or "",
                    label="TRUE_LEAD",
                    fp_class=None,
                    by="agent_m1_true_seed_2026-09-22",
                    notes=f"Provisional TRUE_LEAD via {name}; scorer={r.tier}/{r.lead_type}; human confirm for M1",
                )
                stats[action] += 1
                if action in {"created", "updated"}:
                    stats["true_seeded"] += 1
        await db.commit()
    return stats


async def seed_fp(scorer: LeadScorer, limit: int) -> Counter:
    stats: Counter = Counter()
    async with SessionLocal() as db:
        seen: set[int] = set()
        for fp_class, sql in FP_SQL:
            rows = list((await db.execute(text(sql), {"lim": limit})).all())
            stats[f"cand_{fp_class}"] = len(rows)
            for mid, user, body, cid in rows:
                if mid in seen:
                    continue
                seen.add(mid)
                r = scorer.score(body or "", community_username=user)
                # Prefer confirmed non-leads; allow MEDIUM only if buyer is non-client
                if r.tier in {"HIGH"} and r.buyer_type == "CLIENT" and r.lead_type in COMMERCIAL_KEEP:
                    stats["skip_possible_true"] += 1
                    continue
                action = await upsert_label(
                    db,
                    mid=mid,
                    cid=cid,
                    body=body or "",
                    label="FALSE_POSITIVE",
                    fp_class=fp_class,
                    by="agent_m1_fp_seed_2026-09-22",
                    notes=f"Provisional FP ({fp_class}); scorer={r.tier}/{r.lead_type}; human confirm for M1",
                )
                stats[action] += 1
                if action in {"created", "updated"}:
                    stats["fp_seeded"] += 1
        await db.commit()
    return stats


async def run(*, true_limit: int, fp_limit: int, true_only: bool, fp_only: bool) -> None:
    scorer = LeadScorer("config/scoring.yaml")
    if not fp_only:
        tstats = await seed_true(scorer, true_limit)
        print("TRUE", dict(tstats))
    if not true_only:
        fstats = await seed_fp(scorer, fp_limit)
        print("FP", dict(fstats))
    async with SessionLocal() as db:
        rows = list((await db.execute(select(HumanLabel.label))).scalars().all())
        print("TOTALS", dict(Counter(rows)), "n=", len(rows))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--true-limit", type=int, default=200)
    p.add_argument("--fp-limit", type=int, default=150)
    p.add_argument("--true-only", action="store_true")
    p.add_argument("--fp-only", action="store_true")
    args = p.parse_args()
    asyncio.run(
        run(
            true_limit=args.true_limit,
            fp_limit=args.fp_limit,
            true_only=args.true_only,
            fp_only=args.fp_only,
        )
    )


if __name__ == "__main__":
    main()
