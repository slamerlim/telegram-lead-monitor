#!/usr/bin/env python3
"""Phase B: targeted score backfill for commercial∧domain 360d pool (evidence 96/97).

ONLY scores messages matching the evidence-96 pool regex that lack message_scores.
Uses LeadScorer + persist_message_score. Does NOT upsert CRM leads, wipe scores,
loosen thresholds, or expand beyond this pool.

Evidence: 100-commercial-domain-score-backfill-*.{txt,json}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V6,
    evaluate_discovery,
)
from shared.db import SessionLocal
from shared.models import Community, Message, MessageScore
from shared.score_persist import persist_message_score
from shared.settings import get_settings

# Evidence-96/97 byte-compatible pool regexes.
POOL_COMMERCIAL = (
    r"(budget|fixed price|commission|need someone|"
    r"looking for (a )?(developer|contractor|freelancer)|"
    r"нужен разработчик|починить|Freelance Opportunity)"
)
POOL_DOMAIN = (
    r"(trading bot|bybit|binance|okx|pybit|arbitrage|futures|strategy|"
    r"торговый бот|Crypto Trading Bot)"
)

EV = "100"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-commercial-domain-score-backfill.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-commercial-domain-score-backfill.json"

POOL_COUNT_SQL = """
SELECT COUNT(*) FROM messages m
WHERE m.message_date >= :since
  AND m.text ~* :comm
  AND m.text ~* :dom
"""

POOL_SCORED_SQL = """
SELECT COUNT(*) FROM messages m
JOIN message_scores s ON s.message_id = m.id
WHERE m.message_date >= :since
  AND m.text ~* :comm
  AND m.text ~* :dom
"""

POOL_UNSCORED_IDS_SQL = """
SELECT m.id
FROM messages m
LEFT JOIN message_scores s ON s.message_id = m.id
WHERE m.message_date >= :since
  AND m.text ~* :comm
  AND m.text ~* :dom
  AND s.id IS NULL
ORDER BY m.message_date DESC
LIMIT :lim
"""

LOW_PREFILTER_SHAPE_SQL = """
SELECT COUNT(*) FROM messages m
JOIN message_scores s ON s.message_id = m.id
WHERE m.message_date >= :since
  AND m.text ~* :comm
  AND m.text ~* :dom
  AND s.tier = 'LOW'
  AND (
    (s.commercial_score > 0 AND s.technical_score > 0)
    OR m.text ~* 'trading bot|торговый бот|bybit|binance api|pybit'
    OR m.text ~* 'freelance opportunity.{0,80}(bot|trading|quant|bybit)'
  )
"""

ISO_SQL = """
SELECT
  (SELECT COUNT(*) FROM human_labels) AS human_labels,
  (SELECT COUNT(*) FROM leads WHERE status = 'AI_CONFIRMED') AS ai_confirmed,
  (SELECT COUNT(*) FROM message_scores) AS scores,
  (SELECT COUNT(*) FROM messages) AS messages
"""


def _git_head() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
            ).strip()
        )
    except Exception:
        return "unknown"


async def _pool_metrics(session, since) -> dict:
    params = {"since": since, "comm": POOL_COMMERCIAL, "dom": POOL_DOMAIN}
    pool_n = int((await session.scalar(sql_text(POOL_COUNT_SQL), params)) or 0)
    scored_n = int((await session.scalar(sql_text(POOL_SCORED_SQL), params)) or 0)
    low_pref = int(
        (await session.scalar(sql_text(LOW_PREFILTER_SHAPE_SQL), params)) or 0
    )
    return {
        "pool_n": pool_n,
        "scored_n": scored_n,
        "unscored_n": pool_n - scored_n,
        "low_prefilter_shape_n": low_pref,
        "pct_scored": round(100.0 * scored_n / max(pool_n, 1), 2),
    }


async def _discovery_visible_eligible(session, since, scorer, *, limit: int = 5000) -> dict:
    """disc_v4/v6 eligible among scored pool rows (discovery JOIN visibility)."""
    rows = (
        await session.execute(
            sql_text(
                """
                SELECT m.id, m.text, s.tier
                FROM messages m
                JOIN message_scores s ON s.message_id = m.id
                WHERE m.message_date >= :since
                  AND m.text ~* :comm
                  AND m.text ~* :dom
                ORDER BY m.message_date DESC
                LIMIT :lim
                """
            ),
            {
                "since": since,
                "comm": POOL_COMMERCIAL,
                "dom": POOL_DOMAIN,
                "lim": limit,
            },
        )
    ).all()
    v4 = v6 = v4_low = v6_low = 0
    for _mid, text, tier in rows:
        f4 = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V4)
        f6 = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V6)
        if f4.eligible:
            v4 += 1
            if tier == "LOW":
                v4_low += 1
        if f6.eligible:
            v6 += 1
            if tier == "LOW":
                v6_low += 1
    return {
        "scored_sampled": len(rows),
        "disc_v4_eligible_any_tier": v4,
        "disc_v6_eligible_any_tier": v6,
        "disc_v4_eligible_LOW": v4_low,
        "disc_v6_eligible_LOW": v6_low,
    }


async def run(
    *,
    days: int,
    batch_size: int,
    max_messages: int | None,
    dry_run: bool,
    sleep_ms: int,
) -> dict:
    settings = get_settings()
    scoring_path = Path(settings.scoring_config)
    if not scoring_path.exists():
        scoring_path = ROOT / "config" / "scoring.yaml"
    scorer = LeadScorer(str(scoring_path))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    t0 = time.time()

    async with SessionLocal() as db:
        before = await _pool_metrics(db, since)
        iso_before = dict((await db.execute(sql_text(ISO_SQL))).mappings().one())
        elig_before = await _discovery_visible_eligible(db, since, scorer)

        lim = max_messages if max_messages is not None else before["unscored_n"] + 10
        ids = [
            int(r[0])
            for r in (
                await db.execute(
                    sql_text(POOL_UNSCORED_IDS_SQL),
                    {
                        "since": since,
                        "comm": POOL_COMMERCIAL,
                        "dom": POOL_DOMAIN,
                        "lim": lim,
                    },
                )
            ).all()
        ]
        if max_messages is not None:
            ids = ids[:max_messages]

        summary: dict = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "head": _git_head(),
            "days": days,
            "dry_run": dry_run,
            "pool_regex": {"commercial": POOL_COMMERCIAL, "domain": POOL_DOMAIN},
            "before": before,
            "discovery_visible_eligible_before": elig_before,
            "isolation_before": iso_before,
            "candidate_ids": len(ids),
        }

        if dry_run:
            summary["processed"] = 0
            summary["note"] = "dry_run — no writes"
            after = before
            elig_after = elig_before
            iso_after = iso_before
        else:
            tier_counts: Counter = Counter()
            processed = skipped = 0
            for i in range(0, len(ids), batch_size):
                batch_ids = ids[i : i + batch_size]
                stmt = (
                    select(Message, Community)
                    .join(Community, Message.community_id == Community.id)
                    .where(Message.id.in_(batch_ids))
                )
                rows = list((await db.execute(stmt)).all())
                for message, community in rows:
                    # Idempotent: skip if a score appeared mid-run.
                    existing = await db.scalar(
                        select(MessageScore.id).where(
                            MessageScore.message_id == message.id
                        )
                    )
                    if existing:
                        skipped += 1
                        continue
                    result = scorer.score(
                        message.text,
                        community_username=community.username,
                        community_name=community.name,
                    )
                    # Score-only: no upsert_opportunity_lead (no CRM mutation).
                    await persist_message_score(
                        db,
                        message,
                        result,
                        rule_version=scorer.rule_version,
                    )
                    tier_counts[result.tier] += 1
                    processed += 1
                await db.commit()
                if sleep_ms > 0:
                    await asyncio.sleep(sleep_ms / 1000.0)
                print(
                    f"batch={i // batch_size + 1} processed={processed} "
                    f"skipped={skipped} tiers={dict(tier_counts)}",
                    flush=True,
                )

            after = await _pool_metrics(db, since)
            elig_after = await _discovery_visible_eligible(db, since, scorer)
            iso_after = dict((await db.execute(sql_text(ISO_SQL))).mappings().one())
            summary.update(
                {
                    "processed": processed,
                    "skipped_already_scored": skipped,
                    "tiers_written": dict(tier_counts),
                    "rule_version": scorer.rule_version,
                }
            )

        summary["after"] = after
        summary["discovery_visible_eligible_after"] = elig_after
        summary["isolation_after"] = iso_after
        summary["delta"] = {
            "scored_n": after["scored_n"] - before["scored_n"],
            "unscored_n": after["unscored_n"] - before["unscored_n"],
            "low_prefilter_shape_n": after["low_prefilter_shape_n"]
            - before["low_prefilter_shape_n"],
            "disc_v4_eligible_LOW": elig_after["disc_v4_eligible_LOW"]
            - elig_before["disc_v4_eligible_LOW"],
            "disc_v6_eligible_LOW": elig_after["disc_v6_eligible_LOW"]
            - elig_before["disc_v6_eligible_LOW"],
            "disc_v4_eligible_any_tier": elig_after["disc_v4_eligible_any_tier"]
            - elig_before["disc_v4_eligible_any_tier"],
            "disc_v6_eligible_any_tier": elig_after["disc_v6_eligible_any_tier"]
            - elig_before["disc_v6_eligible_any_tier"],
            "human_labels": iso_after["human_labels"] - iso_before["human_labels"],
            "ai_confirmed": iso_after["ai_confirmed"] - iso_before["ai_confirmed"],
        }
        summary["runtime_sec"] = round(time.time() - t0, 2)
        summary["stopped_after_pool"] = True
        summary["expanded_to_full_corpus"] = False

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"Evidence {EV}: commercial∧domain score backfill (Phase B)",
        f"generated_at={summary['generated_at']} head={summary['head']}",
        f"runtime_sec={summary['runtime_sec']} dry_run={dry_run} days={days}",
        "",
        "=== 1. POOL BEFORE ===",
        f"  pool={before['pool_n']} scored={before['scored_n']} "
        f"unscored={before['unscored_n']} pct={before['pct_scored']}% "
        f"low_prefilter_shape={before['low_prefilter_shape_n']}",
        f"  discovery-visible eligible: {elig_before}",
        "",
        "=== 2. BACKFILL ===",
        f"  candidates={summary['candidate_ids']} processed={summary.get('processed', 0)} "
        f"skipped={summary.get('skipped_already_scored', 0)}",
        f"  tiers={summary.get('tiers_written', {})}",
        f"  CRM upsert=False (score-only persist_message_score)",
        "",
        "=== 3. POOL AFTER ===",
        f"  pool={after['pool_n']} scored={after['scored_n']} "
        f"unscored={after['unscored_n']} pct={after['pct_scored']}% "
        f"low_prefilter_shape={after['low_prefilter_shape_n']}",
        f"  discovery-visible eligible: {elig_after}",
        f"  delta={summary['delta']}",
        "",
        "=== 4. ISOLATION ===",
        f"  before={iso_before}",
        f"  after={iso_after}",
        f"  human_labels/AI_CONFIRMED unchanged="
        f"{summary['delta']['human_labels'] == 0 and summary['delta']['ai_confirmed'] == 0}",
        "",
        "=== 5. SCOPE ===",
        "  Stopped after commercial∧domain pool. Did not expand to full corpus.",
        "",
        f"JSON: {OUT_JSON}",
    ]
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=360)
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--max-messages", type=int, default=None)
    p.add_argument("--sleep-ms", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(
        run(
            days=args.days,
            batch_size=args.batch_size,
            max_messages=args.max_messages,
            dry_run=args.dry_run,
            sleep_ms=args.sleep_ms,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
