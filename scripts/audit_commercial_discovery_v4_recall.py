#!/usr/bin/env python3
"""Deterministic disc_v3 vs disc_v4 recall audit (no Cursor SDK).

Bounded 7-day LOW prefilter (same SQL shape as v3 audit), plus:
- recall-gap forensics (vetoed buyer-like msgs, provisional positives)
- community opportunity profiles
Writes evidence 95.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION_V3,
    DISCOVERY_VERSION_V4,
    evaluate_discovery,
    population_bucket,
)
from shared.db import SessionLocal
from shared.settings import get_settings

EVIDENCE_NUM = "95"
OUT_TXT = ROOT / f"docs/audit/evidence/{EVIDENCE_NUM}-commercial-discovery-v4-recall.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EVIDENCE_NUM}-commercial-discovery-v4-recall.json"

PREFILTER_SQL = """
SELECT m.id, m.text, m.author_id, m.community_id,
       c.name AS community_name, c.username AS community_username,
       s.score, s.commercial_score, s.technical_score, s.tier
FROM messages m
JOIN message_scores s ON s.message_id = m.id
LEFT JOIN communities c ON c.id = m.community_id
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
    OR m.text ~* 'looking for (a )?(developer|contractor|freelancer).{0,60}(bot|trading|bybit|strategy)'
    OR m.text ~* '(budget|бюджет|deadline|сроки).{0,80}(bot|trading|bybit|strategy|разработ)'
    OR m.text ~* '(my|our|мой|наш).{0,20}(bot|strategy|бот|стратеги).{0,60}(fix|repair|automate|implement|починить)'
  )
ORDER BY (s.commercial_score + s.technical_score) DESC, m.message_date DESC
LIMIT :cap
"""


def _preview(t: str, n: int = 110) -> str:
    return " ".join((t or "").split())[:n]


def contamination(cands):
    non_buyer = 0
    for row in cands:
        feats = row[2]
        bucket = row[3]
        if bucket != "Direct buyer/RFQ candidate":
            non_buyer += 1
            continue
        if feats.buyer_direction == "BUYER":
            continue
        if feats.buyer_direction == "RECRUITER" and (
            feats.project_scope
            or feats.project_procurement_signal
            or feats.soft_direction
            or feats.ownership_signal
        ):
            continue
        non_buyer += 1
    n = len(cands) or 1
    return round(100.0 * non_buyer / n, 1)


def density(n_cand: int, examined: int) -> float:
    return round(1000.0 * n_cand / max(examined, 1), 3)


def pack_sample(items):
    return [
        {
            "message_id": mid,
            "discovery_score": feats.discovery_score,
            "buyer_direction": feats.buyer_direction,
            "trigger": feats.trigger_type,
            "families": feats.buyer_signal_families,
            "soft_direction": feats.soft_direction,
            "bucket": bucket,
            "preview": _preview(text),
        }
        for mid, text, feats, bucket, *_ in items
    ]


async def main() -> int:
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    since = datetime.now(timezone.utc) - timedelta(days=7)
    cap = 2500
    t0 = time.perf_counter()

    async with SessionLocal() as session:
        msgs_7d = int(
            (
                await session.scalar(
                    sql_text("SELECT COUNT(*) FROM messages WHERE message_date >= :since"),
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
                        OR m.text ~* 'trading bot|bybit|binance api'
                      )
                    ORDER BY m.message_date DESC
                    LIMIT 100
                    """
                ),
                {"since": since},
            )
        ).fetchall()
        explain_lines = [r[0] for r in explain]

        rows = (
            await session.execute(sql_text(PREFILTER_SQL), {"since": since, "cap": cap})
        ).all()

        # Population C: provisional positives (NOT ground truth)
        hl_rows = (
            await session.execute(
                sql_text(
                    """
                    SELECT m.id, m.text, hl.label
                    FROM human_labels hl
                    JOIN messages m ON m.id = hl.message_id
                    WHERE hl.label = 'TRUE_LEAD'
                    ORDER BY hl.labeled_at DESC
                    LIMIT 200
                    """
                )
            )
        ).all()
        # Fallback: any recent human_labels
        if not hl_rows:
            hl_rows = (
                await session.execute(
                    sql_text(
                        """
                        SELECT m.id, m.text, hl.label::text
                        FROM human_labels hl
                        JOIN messages m ON m.id = hl.message_id
                        ORDER BY hl.id DESC
                        LIMIT 80
                        """
                    )
                )
            ).all()

        ai_rows = (
            await session.execute(
                sql_text(
                    """
                    SELECT m.id, m.text, l.status::text
                    FROM leads l
                    JOIN messages m ON m.id = l.message_id
                    WHERE l.status = 'AI_CONFIRMED'
                    LIMIT 20
                    """
                )
            )
        ).all()

        # Community volume for opportunity profile
        community_stats = (
            await session.execute(
                sql_text(
                    """
                    SELECT c.id, c.name, c.username,
                           COUNT(*) FILTER (WHERE m.message_date >= :since) AS msgs_7d,
                           COUNT(*) FILTER (
                             WHERE s.tier='LOW' AND s.scored_at >= :since
                           ) AS low_7d
                    FROM communities c
                    LEFT JOIN messages m ON m.community_id = c.id
                    LEFT JOIN message_scores s ON s.message_id = m.id
                    WHERE c.enabled IS TRUE OR c.enabled IS NULL
                    GROUP BY c.id, c.name, c.username
                    HAVING COUNT(*) FILTER (WHERE m.message_date >= :since) > 0
                    ORDER BY msgs_7d DESC
                    LIMIT 80
                    """
                ),
                {"since": since},
            )
        ).all()

    examined = len(rows)
    v3_cands = []
    v4_cands = []
    v3_dirs = Counter()
    v4_dirs = Counter()
    v3_buckets = Counter()
    v4_buckets = Counter()
    v3_vetoes = Counter()
    v4_vetoes = Counter()
    v3_triggers = Counter()
    v4_triggers = Counter()
    v4_families = Counter()
    v4_paths = Counter()
    miss_reasons = Counter()
    recovered = []  # in v4 not v3
    lost = []  # in v3 not v4
    community_v3 = defaultdict(int)
    community_v4 = defaultdict(int)
    community_buyer_sig = defaultdict(int)
    community_veto = defaultdict(int)
    community_repair = defaultdict(int)
    community_project = defaultdict(int)
    community_names = {}

    for row in rows:
        mid, text, author_id, community_id = row[0], row[1], row[2], row[3]
        cname = row[4] or f"id:{community_id}"
        community_names[community_id] = cname
        f3 = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V3)
        f4 = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V4)
        b3 = population_bucket(f3)
        b4 = population_bucket(f4)

        if f3.eligible:
            v3_cands.append((mid, text, f3, b3, author_id, community_id))
            v3_dirs[f3.buyer_direction] += 1
            v3_buckets[b3] += 1
            v3_triggers[f3.trigger_type or "none"] += 1
            community_v3[community_id] += 1
        else:
            for v in f3.veto_categories:
                v3_vetoes[v] += 1

        if f4.eligible:
            v4_cands.append((mid, text, f4, b4, author_id, community_id))
            v4_dirs[f4.buyer_direction] += 1
            v4_buckets[b4] += 1
            v4_triggers[f4.trigger_type or "none"] += 1
            community_v4[community_id] += 1
            for fam in f4.buyer_signal_families:
                v4_families[fam] += 1
            v4_paths[f4.trigger_type or "none"] += 1
            if f4.repair_signal:
                community_repair[community_id] += 1
            if f4.direct_request_signal or f4.project_procurement_signal:
                community_project[community_id] += 1
            if f4.buyer_signal_families:
                community_buyer_sig[community_id] += 1
        else:
            for v in f4.veto_categories:
                v4_vetoes[v] += 1
                community_veto[community_id] += 1

        # Miss taxonomy for v3-rejected with buyer-ish signals
        if not f3.eligible and (
            f4.direct_request_signal
            or f4.ownership_signal
            or f4.repair_signal
            or f4.project_procurement_signal
            or f4.automation_signal
        ):
            reason = "hard_veto"
            if f3.veto_categories:
                reason = f3.veto_categories[0]
            elif f3.buyer_direction in ("SEEKER", "PROVIDER", "EMPLOYER"):
                reason = f"direction_{f3.buyer_direction.lower()}"
            elif not f3.project_scope:
                reason = "insufficient_combination"
            miss_reasons[reason] += 1
            if f4.eligible:
                recovered.append(
                    {
                        "message_id": mid,
                        "v3_veto": f3.veto_categories,
                        "v3_dir": f3.buyer_direction,
                        "v4_dir": f4.buyer_direction,
                        "v4_trigger": f4.trigger_type,
                        "families": f4.buyer_signal_families,
                        "preview": _preview(text),
                    }
                )

        if f3.eligible and not f4.eligible:
            lost.append(
                {
                    "message_id": mid,
                    "v4_veto": f4.veto_categories,
                    "v3_dir": f3.buyer_direction,
                    "v4_dir": f4.buyer_direction,
                    "preview": _preview(text),
                }
            )

    # Population C coverage diagnostic
    def coverage(rows_in, label):
        hit_v3 = hit_v4 = 0
        miss_detail = []
        for mid, text, lab in rows_in:
            f3 = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V3)
            f4 = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V4)
            if f3.eligible:
                hit_v3 += 1
            if f4.eligible:
                hit_v4 += 1
            if not f4.eligible:
                miss_detail.append(
                    {
                        "message_id": mid,
                        "label": lab,
                        "v3": f3.eligible,
                        "v4_veto": f4.veto_categories,
                        "v4_dir": f4.buyer_direction,
                        "preview": _preview(text),
                    }
                )
        return {
            "population": label,
            "n": len(rows_in),
            "retrieved_v3": hit_v3,
            "retrieved_v4": hit_v4,
            "missed_v4": len(rows_in) - hit_v4,
            "miss_samples": miss_detail[:15],
        }

    diag_hl = coverage(hl_rows, "human_labels_provisional")
    diag_ai = coverage(ai_rows, "AI_CONFIRMED_source_msgs")

    # Community opportunity profiles
    community_profiles = []
    for cid, name, uname, msgs, low in community_stats:
        m7 = int(msgs or 0)
        l7 = int(low or 0)
        cv3 = community_v3.get(cid, 0)
        cv4 = community_v4.get(cid, 0)
        buyer_sig = community_buyer_sig.get(cid, 0)
        community_profiles.append(
            {
                "community_id": cid,
                "community": name or uname or str(cid),
                "username": uname,
                "messages_7d": m7,
                "low_messages_7d": l7,
                "disc_v3_candidates": cv3,
                "disc_v4_candidates": cv4,
                "buyer_signal_candidates": buyer_sig,
                "hard_veto_hits_in_prefilter": community_veto.get(cid, 0),
                "buyer_signal_density": round(1000.0 * buyer_sig / max(m7, 1), 3),
                "project_request_density": round(
                    1000.0 * community_project.get(cid, 0) / max(m7, 1), 3
                ),
                "repair_request_density": round(
                    1000.0 * community_repair.get(cid, 0) / max(m7, 1), 3
                ),
            }
        )
    community_profiles.sort(
        key=lambda x: (-x["buyer_signal_density"], -x["disc_v4_candidates"], -x["messages_7d"])
    )

    sample_report = {"strongest": [], "medium": [], "ambiguous": []}
    if v4_cands:
        ranked = sorted(v4_cands, key=lambda x: -x[2].discovery_score)
        n = len(ranked)
        sample_report["strongest"] = pack_sample(ranked[:10])
        mid_start = max(0, n // 3)
        sample_report["medium"] = pack_sample(ranked[mid_start : mid_start + 10])
        amb = [
            c
            for c in ranked
            if c[2].buyer_direction in ("UNKNOWN", "RECRUITER") or c[2].discovery_score < 2.5
        ]
        sample_report["ambiguous"] = pack_sample(amb[:10] if amb else ranked[-10:])

    elapsed = time.perf_counter() - t0
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "discovery_versions": {"v3": DISCOVERY_VERSION_V3, "v4": DISCOVERY_VERSION_V4},
        "baseline": {
            "messages_7d": msgs_7d,
            "low_tier_scores_7d": low_7d,
            "prefilter_examined": examined,
            "prefilter_cap": cap,
        },
        "v3": {
            "candidates": len(v3_cands),
            "unique_authors": len({a for *_, a, _c in v3_cands}),
            "unique_communities": len({c for *_, _a, c in v3_cands}),
            "density_per_1000_examined": density(len(v3_cands), examined),
            "non_buyer_contamination_pct": contamination(v3_cands),
            "population_buckets": dict(v3_buckets),
            "buyer_direction": dict(v3_dirs),
            "triggers": dict(v3_triggers),
            "veto_counts_on_examined": dict(v3_vetoes.most_common(25)),
        },
        "v4": {
            "candidates": len(v4_cands),
            "unique_authors": len({a for *_, a, _c in v4_cands}),
            "unique_communities": len({c for *_, _a, c in v4_cands}),
            "density_per_1000_examined": density(len(v4_cands), examined),
            "non_buyer_contamination_pct": contamination(v4_cands),
            "population_buckets": dict(v4_buckets),
            "buyer_direction": dict(v4_dirs),
            "triggers": dict(v4_triggers),
            "buyer_signal_families": dict(v4_families.most_common(30)),
            "veto_counts_on_examined": dict(v4_vetoes.most_common(25)),
            "recovered_from_v3_rejects": len(recovered),
            "lost_vs_v3": len(lost),
        },
        "recall_gap": {
            "v3_reject_buyerish_miss_reasons": dict(miss_reasons.most_common(20)),
            "recovered_samples": recovered[:30],
            "lost_samples": lost[:20],
        },
        "retrieval_coverage_diagnostic": [diag_hl, diag_ai],
        "community_profiles_top": community_profiles[:40],
        "sample_report": sample_report,
        "sdk_ab_run": False,
        "runtime_sec": round(elapsed, 2),
        "explain_prefilter": explain_lines,
        "isolation_note": "no SDK; no CRM; ctx_v1 remains default; scoring.yaml untouched",
    }

    v4_n = summary["v4"]["candidates"]
    v4_cont = summary["v4"]["non_buyer_contamination_pct"]
    v3_n = summary["v3"]["candidates"]
    if v4_n >= 20 and v4_cont <= 30:
        decision = "A"
        rationale = "Clean population >=20; message-only SDK shadow allowed."
    elif 10 <= v4_n <= 19:
        decision = "B"
        rationale = "Population 10–19; improve discovery further; no SDK."
    elif v4_n < 10:
        decision = "C"
        rationale = "Population <10; investigate community/source coverage."
    elif v4_cont > 40:
        decision = "D"
        rationale = "Contamination rose; tighten positive conjunctions."
    else:
        decision = "E"
        rationale = "Clean but AI confirmation may stay tiny — investigate AI later."
    if v4_n >= 20 and v4_cont > 40:
        decision = "D"
        rationale = "Contamination rose materially vs disc_v3 cleanliness."

    summary["decision"] = decision
    summary["decision_rationale"] = rationale
    summary["delta_vs_v3"] = {
        "candidate_delta": v4_n - v3_n,
        "contamination_delta_pp": round(
            v4_cont - summary["v3"]["non_buyer_contamination_pct"], 1
        ),
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Commercial discovery v4 recall audit ({EVIDENCE_NUM})",
        f"generated_at: {summary['generated_at']}",
        f"examined: {examined}  msgs_7d={msgs_7d}  low_7d={low_7d}",
        "",
        "## disc_v3 baseline",
        f"candidates={v3_n}  density={summary['v3']['density_per_1000_examined']}/1000  "
        f"contamination={summary['v3']['non_buyer_contamination_pct']}%",
        f"dirs={dict(v3_dirs)}",
        f"vetoes_top={dict(v3_vetoes.most_common(8))}",
        "",
        "## disc_v4",
        f"candidates={v4_n}  density={summary['v4']['density_per_1000_examined']}/1000  "
        f"contamination={v4_cont}%",
        f"dirs={dict(v4_dirs)}",
        f"triggers={dict(v4_triggers)}",
        f"families={dict(v4_families.most_common(12))}",
        f"recovered_from_v3={len(recovered)}  lost_vs_v3={len(lost)}",
        f"vetoes_top={dict(v4_vetoes.most_common(8))}",
        "",
        "## decision",
        f"{decision}: {rationale}",
        "",
        "## community top by buyer_signal_density",
    ]
    for p in community_profiles[:15]:
        lines.append(
            f"  {p['community'][:40]:40} msgs={p['messages_7d']:5} "
            f"v3={p['disc_v3_candidates']:3} v4={p['disc_v4_candidates']:3} "
            f"buyer_dens={p['buyer_signal_density']}"
        )
    lines.extend(
        [
            "",
            "## retrieval coverage diagnostic",
            f"  human_labels: n={diag_hl['n']} v3={diag_hl['retrieved_v3']} v4={diag_hl['retrieved_v4']}",
            f"  AI_CONFIRMED: n={diag_ai['n']} v3={diag_ai['retrieved_v3']} v4={diag_ai['retrieved_v4']}",
            "",
            f"runtime_sec={elapsed:.2f}",
            "NO OUTREACH / NO SDK in this script",
        ]
    )
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_TXT.read_text(encoding="utf-8"))
    print(f"Wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
