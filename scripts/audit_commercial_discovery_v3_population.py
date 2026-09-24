#!/usr/bin/env python3
"""Deterministic disc_v2 vs disc_v3 population audit (no Cursor SDK).

Scans a bounded 7-day LOW-tier prefilter (same SQL shape as shadow runner),
classifies with both discovery versions, writes evidence 94.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION,
    DISCOVERY_VERSION_V2,
    evaluate_discovery,
    population_bucket,
)
from shared.db import SessionLocal
from shared.settings import get_settings


def _preview(t: str, n: int = 110) -> str:
    return " ".join((t or "").split())[:n]


async def main() -> int:
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    since = datetime.now(timezone.utc) - timedelta(days=7)
    cap = 2500
    t0 = time.perf_counter()

    async with SessionLocal() as session:
        # Baseline message counts
        msgs_7d = int(
            (
                await session.scalar(
                    sql_text(
                        "SELECT COUNT(*) FROM messages WHERE message_date >= :since"
                    ),
                    {"since": since},
                )
            )
            or 0
        )
        low_7d = int(
            (
                await session.scalar(
                    sql_text(
                        """
                        SELECT COUNT(*) FROM message_scores
                        WHERE tier='LOW' AND scored_at >= :since
                        """
                    ),
                    {"since": since},
                )
            )
            or 0
        )

        explain = (
            await session.execute(
                sql_text(
                    """
                    EXPLAIN (ANALYZE, BUFFERS)
                    SELECT m.id
                    FROM messages m
                    JOIN message_scores s ON s.message_id = m.id
                    WHERE s.tier = 'LOW'
                      AND s.scored_at >= :since
                      AND (
                        (s.commercial_score > 0 AND s.technical_score > 0)
                        OR m.text ~* 'trading bot|торговый бот|bybit|binance api|pybit'
                        OR m.text ~* '(hire|looking to (pay|hire)|need).{0,40}(bot|trading|quant)'
                      )
                    ORDER BY (s.commercial_score + s.technical_score) DESC, m.message_date DESC
                    LIMIT 100
                    """
                ),
                {"since": since},
            )
        ).fetchall()
        explain_lines = [r[0] for r in explain]

        rows = (
            await session.execute(
                sql_text(
                    """
                    SELECT m.id, m.text, m.author_id, m.community_id,
                           s.score, s.commercial_score, s.technical_score, s.tier
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
                        OR m.text ~* 'is hiring|looking for a .{0,40}engineer'
                        OR m.text ~* '#резюме|#opentowork|#вакансия'
                      )
                    ORDER BY (s.commercial_score + s.technical_score) DESC, m.message_date DESC
                    LIMIT :cap
                    """
                ),
                {"since": since, "cap": cap},
            )
        ).all()

    examined = len(rows)
    v2_cands = []
    v3_cands = []
    v2_buckets = Counter()
    v3_buckets = Counter()
    v2_dirs = Counter()
    v3_dirs = Counter()
    v3_vetoes = Counter()
    v3_triggers = Counter()

    for mid, text, author_id, community_id, score, cscore, tscore, tier in rows:
        f2 = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V2)
        f3 = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION)
        b2 = population_bucket(f2)
        b3 = population_bucket(f3)
        # For non-eligible, still classify bucket from direction signals
        if f2.eligible:
            v2_cands.append((mid, text, f2, b2, author_id, community_id))
            v2_buckets[b2] += 1
            v2_dirs[f2.buyer_direction] += 1
        if f3.eligible:
            v3_cands.append((mid, text, f3, b3, author_id, community_id))
            v3_buckets[b3] += 1
            v3_dirs[f3.buyer_direction] += 1
            v3_triggers[f3.trigger_type or "none"] += 1
        else:
            for v in f3.veto_categories:
                v3_vetoes[v] += 1

    def contamination(cands):
        non_buyer = 0
        for _, _, feats, bucket, *_ in cands:
            if bucket != "Direct buyer/RFQ candidate":
                non_buyer += 1
            elif feats.buyer_direction not in ("BUYER",):
                # Project recruiters count as soft-buyer; employer-shaped RECRUITER is contamination.
                if feats.buyer_direction == "RECRUITER" and (
                    feats.employment_signal or not feats.project_scope
                ):
                    non_buyer += 1
                elif feats.buyer_direction != "RECRUITER":
                    non_buyer += 1
        n = len(cands) or 1
        return round(100.0 * non_buyer / n, 1)

    def density(n_cand: int) -> float:
        return round(1000.0 * n_cand / max(examined, 1), 3)

    # Sample 100 v3 (or all) stratified by discovery_score
    sample_report = {"strongest": [], "medium": [], "ambiguous": [], "random": []}
    if v3_cands:
        ranked = sorted(v3_cands, key=lambda x: -x[2].discovery_score)
        n = len(ranked)

        def pack(items):
            return [
                {
                    "message_id": mid,
                    "discovery_score": feats.discovery_score,
                    "buyer_direction": feats.buyer_direction,
                    "trigger": feats.trigger_type,
                    "bucket": bucket,
                    "preview": _preview(text),
                }
                for mid, text, feats, bucket, *_ in items
            ]

        sample_report["strongest"] = pack(ranked[:25])
        mid_start = max(0, n // 3)
        sample_report["medium"] = pack(ranked[mid_start : mid_start + 25])
        # Ambiguous: UNKNOWN direction or weak trigger among eligible
        amb = [c for c in ranked if c[2].buyer_direction == "UNKNOWN" or (c[2].discovery_score < 2.0)]
        sample_report["ambiguous"] = pack(amb[:25] if amb else ranked[-25:])
        # Random: every k-th
        step = max(1, n // 25)
        sample_report["random"] = pack(ranked[::step][:25])

    elapsed = time.perf_counter() - t0
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "discovery_versions": {"v2": DISCOVERY_VERSION_V2, "v3": DISCOVERY_VERSION},
        "baseline": {
            "messages_7d": msgs_7d,
            "low_tier_scores_7d": low_7d,
            "prefilter_examined": examined,
            "prefilter_cap": cap,
        },
        "v2": {
            "candidates": len(v2_cands),
            "unique_authors": len({a for *_, a, _c in v2_cands}),
            "unique_communities": len({c for *_, _a, c in v2_cands}),
            "density_per_1000_examined": density(len(v2_cands)),
            "non_buyer_contamination_pct": contamination(v2_cands),
            "population_buckets": dict(v2_buckets),
            "buyer_direction": dict(v2_dirs),
        },
        "v3": {
            "candidates": len(v3_cands),
            "unique_authors": len({a for *_, a, _c in v3_cands}),
            "unique_communities": len({c for *_, _a, c in v3_cands}),
            "density_per_1000_examined": density(len(v3_cands)),
            "non_buyer_contamination_pct": contamination(v3_cands),
            "population_buckets": dict(v3_buckets),
            "buyer_direction": dict(v3_dirs),
            "triggers": dict(v3_triggers),
            "veto_counts_on_examined": dict(v3_vetoes.most_common(20)),
        },
        "sdk_ab_run": False,
        "decision_pending": True,
        "runtime_sec": round(elapsed, 2),
        "explain_prefilter": explain_lines,
        "sample_report": sample_report,
        "isolation_note": "no SDK; no CRM; ctx_v1 remains default",
    }

    # Decision heuristic
    v2_cont = summary["v2"]["non_buyer_contamination_pct"]
    v3_cont = summary["v3"]["non_buyer_contamination_pct"]
    v3_n = summary["v3"]["candidates"]
    buyer_share = (
        100.0
        * summary["v3"]["population_buckets"].get("Direct buyer/RFQ candidate", 0)
        / max(v3_n, 1)
    )
    if v3_n >= 20 and v3_cont <= 25 and buyer_share >= 60:
        decision = "A"
        rationale = "Population cleaner; proceed with small message-only SDK cohort."
    elif v3_n >= 20 and v3_cont > 40:
        decision = "B"
        rationale = "Still contaminated; tighten discovery further."
    elif v3_n < 20:
        decision = "C"
        rationale = (
            f"Much cleaner than v2 (contam {v2_cont}%→{v3_cont}%) but tiny (n={v3_n}). "
            "Broaden buyer-signal recall / communities — do not loosen scorer."
        )
    else:
        decision = "B"
        rationale = "Need another discovery tighten pass before SDK."
    if v3_cont < v2_cont - 30 and v3_n >= 25 and v3_cont <= 20:
        decision = "A"
        rationale = (
            f"Contamination {v2_cont}%→{v3_cont}%; n={v3_n}. "
            "Proceed message-only AI on small cohort."
        )

    summary["decision"] = decision
    summary["decision_rationale"] = rationale
    summary["sdk_ab_run"] = False  # never auto-run SDK in this script

    out_json = ROOT / "docs/audit/evidence/94-commercial-discovery-v3-population.json"
    out_txt = ROOT / "docs/audit/evidence/94-commercial-discovery-v3-population.txt"
    out_json.write_text(json.dumps(summary, indent=2, default=str) + "\n")

    lines = [
        "=== 94 commercial discovery v3 population (deterministic) ===",
        f"generated_at={summary['generated_at']}",
        f"msgs_7d={msgs_7d} low_tier_7d={low_7d} examined={examined}",
        f"runtime_sec={summary['runtime_sec']}",
        "",
        "--- V2 ---",
        f"candidates={summary['v2']['candidates']} density/1000={summary['v2']['density_per_1000_examined']}",
        f"contamination%={summary['v2']['non_buyer_contamination_pct']}",
        f"buckets={dict(v2_buckets)}",
        f"directions={dict(v2_dirs)}",
        "",
        "--- V3 ---",
        f"candidates={summary['v3']['candidates']} density/1000={summary['v3']['density_per_1000_examined']}",
        f"contamination%={summary['v3']['non_buyer_contamination_pct']}",
        f"buckets={dict(v3_buckets)}",
        f"directions={dict(v3_dirs)}",
        f"triggers={dict(v3_triggers)}",
        f"top_vetoes={dict(v3_vetoes.most_common(12))}",
        "",
        "Population table (V3 eligible buckets):",
    ]
    for name in [
        "Direct buyer/RFQ candidate",
        "Corporate/job-board hiring",
        "Job seeker/resume",
        "Recruiter",
        "Provider/self-promotion",
        "Support/question",
        "News/editorial",
        "Marketing/promo",
        "Other",
    ]:
        c = v3_buckets.get(name, 0)
        pct = round(100.0 * c / max(v3_n, 1), 1)
        lines.append(f"  {name}: {c} ({pct}%)")

    lines += [
        "",
        "--- EXPLAIN (prefilter) ---",
        *explain_lines[:18],
        "",
        f"DECISION={decision} — {rationale}",
        "NO SDK in this artifact. ctx_v1 default. NO OUTREACH.",
        "Sample strongest previews:",
    ]
    for s in sample_report["strongest"][:8]:
        lines.append(
            f"  id={s['message_id']} dir={s['buyer_direction']} "
            f"score={s['discovery_score']} {_preview(s['preview'], 90)}"
        )
    out_txt.write_text("\n".join(lines) + "\n")
    print(out_txt.read_text())
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
