#!/usr/bin/env python3
"""Evidence 118 / WP1 — Unbiased offline corpus loss attribution (Mode 1).

READ-ONLY against production Postgres. Does NOT write message_scores, candidates,
labels, flip flags, reopen path_b/c, enable shadow/ML, or loosen scoring.yaml.

Hypothesis (pre-registered BEFORE counts):
  Live disc_v6 NEW=0 under RESIDUAL_SCARCITY may reflect (a) true source absence
  in the incoming stream and/or (b) measurement bias from low score coverage +
  LOW-tier gate. An unbiased messages-only stratified sample can distinguish
  SOURCE_ABSENT vs UPSTREAM_LOSS@G1|G2|G3 with selection-corrected rates.

Decision rule (pre-registered):
  - SOURCE_ABSENT if sample shows ~0 disc_v6-eligible and ~0 near-miss buyer
    RFQs across strata (not global market proof).
  - Else UPSTREAM_LOSS@G<n> naming the dominant gate with frozen samples.
  - Do not claim absence for unscored history without stratified evidence.

Run:
  docker compose run --rm --no-deps -v \"$PWD:/app\" -w /app analyzer \\
    sh -c 'PYTHONPATH=/app python scripts/audit_offline_corpus_loss_attribution_360d.py'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import DISCOVERY_VERSION_V6, evaluate_discovery
from shared.db import SessionLocal
from shared.settings import get_settings

from scripts.audit_disc_v6_refined_population_cycle2 import (  # noqa: E402
    REFINED_COMMERCIAL,
    first_loss,
    is_exchange_community,
)
from scripts.audit_disc_v6_residual_scarcity_cycle6 import (  # noqa: E402
    TRUE_BUYER_NEAR_MISS_RX,
)

EV = "118"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-offline-corpus-loss-attribution.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-offline-corpus-loss-attribution.json"
OUT_FREEZE = ROOT / f"docs/audit/evidence/{EV}-offline-freeze-samples.json"

PATH_B_AT = datetime.fromisoformat("2026-09-26T06:53:16+00:00")
EXPECT_HL = 1524
EXPECT_AC = 4

AGE_BUCKETS = (
    ("0_7d", 0, 7),
    ("8_30d", 8, 30),
    ("31_90d", 31, 90),
    ("91d_plus", 91, 360),
)

ISO_SQL = """
SELECT
  (SELECT COUNT(*) FROM human_labels) AS human_labels,
  (SELECT COUNT(*) FROM leads WHERE status = 'AI_CONFIRMED') AS ai_confirmed,
  (SELECT COUNT(*) FROM message_scores) AS message_scores,
  (SELECT COUNT(*) FROM messages) AS messages,
  (SELECT COUNT(*) FROM commercial_discovery_candidates
     WHERE discovery_version = 'disc_v6'
       AND created_at >= :path_b) AS disc_v6_new
"""

BUCKET_COUNTS_SQL = """
SELECT
  COUNT(*) AS n_messages,
  COUNT(*) FILTER (
    WHERE EXISTS (SELECT 1 FROM message_scores s WHERE s.message_id = m.id)
  ) AS n_scored
FROM messages m
WHERE m.message_date >= :lo AND m.message_date < :hi
  AND m.text IS NOT NULL AND length(m.text) >= 20
"""

# Modular sampling avoids ORDER BY random() on multi-million row ranges.
SAMPLE_SQL = """
SELECT m.id, m.text, m.message_date, m.community_id,
       c.name AS community_name, c.username AS community_username,
       s.tier AS persisted_tier, s.score AS persisted_score
FROM messages m
LEFT JOIN communities c ON c.id = m.community_id
LEFT JOIN message_scores s ON s.message_id = m.id
WHERE m.message_date >= :lo AND m.message_date < :hi
  AND m.text IS NOT NULL AND length(m.text) >= 20
  AND (m.id % :stride) = :offset
ORDER BY m.message_date DESC, m.id DESC
LIMIT :lim
"""


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _preview(text: str, n: int = 240) -> str:
    return re.sub(r"\s+", " ", (text or ""))[:n]


def _bucket_bounds(now: datetime, lo_days: int, hi_days: int) -> tuple[datetime, datetime]:
    hi = now - timedelta(days=lo_days)
    lo = now - timedelta(days=hi_days)
    return lo, hi


def _community_class(scorer: LeadScorer, username: str | None, name: str | None) -> str:
    if is_exchange_community(scorer, username, name):
        return "EXCHANGE_OFFICIAL"
    prof = scorer._profile(username, name)
    return str(prof.get("class") or "UNKNOWN")


def _tier_of(scorer: LeadScorer, text: str, persisted: Any) -> str:
    if persisted:
        return str(persisted).upper()
    try:
        scored = scorer.score(text)
        tier = getattr(scored, "tier", None)
        if tier is None and isinstance(scored, dict):
            tier = scored.get("tier")
        return str(tier or "UNSCORED").upper()
    except Exception:
        return "UNSCORED"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-bucket", type=int, default=2500)
    ap.add_argument("--freeze-cap", type=int, default=200)
    ap.add_argument("--stride", type=int, default=97)
    ap.add_argument("--offset", type=int, default=3)
    ap.add_argument("--statement-timeout-ms", type=int, default=180000)
    args = ap.parse_args()

    t0 = time.time()
    now = datetime.now(timezone.utc)
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    head = _git_head()
    refined_rx = re.compile(REFINED_COMMERCIAL, re.I)

    pre_registered = {
        "hypothesis": (
            "NEW=0 may be SOURCE_ABSENT and/or UPSTREAM_LOSS; score-coverage bias "
            "must be corrected via messages-only stratified sampling."
        ),
        "decision_rule": (
            "SOURCE_ABSENT if ~0 eligible and ~0 near-miss RFQs in sample; "
            "else UPSTREAM_LOSS@dominant gate with freezes. Not global market proof."
        ),
        "mode": "OFFLINE_PREP_READONLY",
        "path_freeze": "path_b+path_c",
        "ml_training_enabled": False,
    }

    async with SessionLocal() as session:
        await session.execute(
            sql_text(f"SET LOCAL statement_timeout = {int(args.statement_timeout_ms)}")
        )
        iso = dict(
            (await session.execute(sql_text(ISO_SQL), {"path_b": PATH_B_AT}))
            .mappings()
            .one()
        )
        isolation_ok = (
            int(iso["human_labels"]) == EXPECT_HL
            and int(iso["ai_confirmed"]) == EXPECT_AC
        )

        bucket_stats: dict[str, Any] = {}
        samples: list[dict[str, Any]] = []

        for name, lo_d, hi_d in AGE_BUCKETS:
            lo, hi = _bucket_bounds(now, lo_d, hi_d)
            counts = dict(
                (
                    await session.execute(
                        sql_text(BUCKET_COUNTS_SQL), {"lo": lo, "hi": hi}
                    )
                )
                .mappings()
                .one()
            )
            n_msg = int(counts["n_messages"] or 0)
            n_scored = int(counts["n_scored"] or 0)
            bucket_stats[name] = {
                "lo": lo.isoformat(),
                "hi": hi.isoformat(),
                "n_messages": n_msg,
                "n_scored": n_scored,
                "score_coverage": round(n_scored / n_msg, 6) if n_msg else None,
                "sampled": 0,
            }
            if n_msg == 0:
                continue
            rows = [
                dict(x)
                for x in (
                    await session.execute(
                        sql_text(SAMPLE_SQL),
                        {
                            "lo": lo,
                            "hi": hi,
                            "lim": int(args.per_bucket),
                            "stride": int(args.stride),
                            "offset": int(args.offset),
                        },
                    )
                )
                .mappings()
                .all()
            ]
            bucket_stats[name]["sampled"] = len(rows)
            for r in rows:
                r["_bucket"] = name
                samples.append(r)

    loss_hist: Counter[str] = Counter()
    loss_by_bucket: dict[str, Counter[str]] = defaultdict(Counter)
    gate_counts: Counter[str] = Counter()
    eligible_by_tier: Counter[str] = Counter()
    community_class_hist: Counter[str] = Counter()
    refined_hits_by_bucket: Counter[str] = Counter()
    near_hits_by_bucket: Counter[str] = Counter()
    freeze_nopath: list[dict[str, Any]] = []
    freeze_veto: list[dict[str, Any]] = []
    freeze_eligible_non_low: list[dict[str, Any]] = []

    for r in samples:
        text = r.get("text") or ""
        bucket = r["_bucket"]
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        fl = first_loss(feats)
        loss_hist[fl] += 1
        loss_by_bucket[bucket][fl] += 1
        near = bool(TRUE_BUYER_NEAR_MISS_RX.search(text))
        refined_hit = bool(refined_rx.search(text))
        if near:
            near_hits_by_bucket[bucket] += 1
        if refined_hit:
            refined_hits_by_bucket[bucket] += 1
        cclass = _community_class(
            scorer, r.get("community_username"), r.get("community_name")
        )
        community_class_hist[cclass] += 1

        # Tier only matters for enqueue (G1) when discovery-eligible.
        tier_u = "N/A"
        if feats.eligible:
            tier_u = _tier_of(scorer, text, r.get("persisted_tier"))
            eligible_by_tier[tier_u] += 1
            gate_counts["G3_path_pass"] += 1
            if tier_u != "LOW":
                gate_counts["G1_eligible_but_non_low"] += 1
                if len(freeze_eligible_non_low) < args.freeze_cap:
                    freeze_eligible_non_low.append(
                        {
                            "message_id": int(r["id"]),
                            "bucket": bucket,
                            "tier": tier_u,
                            "trigger_type": getattr(feats, "trigger_type", None),
                            "community_class": cclass,
                            "near_miss_buyer_rx": near,
                            "preview": _preview(text),
                        }
                    )
            else:
                gate_counts["G1_would_enqueue_if_live"] += 1
        elif fl.startswith("VETO_") or fl == "HARD_EXCLUDE_EARLY":
            gate_counts["G2_veto_or_hard"] += 1
            if len(freeze_veto) < args.freeze_cap and (near or refined_hit):
                freeze_veto.append(
                    {
                        "message_id": int(r["id"]),
                        "bucket": bucket,
                        "first_loss": fl,
                        "community_class": cclass,
                        "near_miss_buyer_rx": near,
                        "preview": _preview(text),
                    }
                )
        elif fl == "NO_PATH_MATCH":
            gate_counts["G3_no_path"] += 1
            if len(freeze_nopath) < args.freeze_cap and near:
                freeze_nopath.append(
                    {
                        "message_id": int(r["id"]),
                        "bucket": bucket,
                        "first_loss": fl,
                        "direction": feats.buyer_direction,
                        "community_class": cclass,
                        "preview": _preview(text),
                    }
                )
        else:
            gate_counts[f"other_{fl}"] += 1

    # Selection-corrected estimates from sample rates × population.
    est_eligible = 0.0
    est_near = 0.0
    est_refined = 0.0
    for name, _, _ in AGE_BUCKETS:
        bs = bucket_stats[name]
        n_pop = int(bs["n_messages"] or 0)
        n_s = int(bs["sampled"] or 0)
        if not n_s or not n_pop:
            continue
        elig = int(loss_by_bucket[name].get("RETRIEVED", 0))
        est_eligible += (elig / n_s) * n_pop
        est_near += (near_hits_by_bucket[name] / n_s) * n_pop
        est_refined += (refined_hits_by_bucket[name] / n_s) * n_pop
        bs["sample_eligible"] = elig
        bs["sample_near_miss_rx"] = int(near_hits_by_bucket[name])
        bs["sample_refined_rx"] = int(refined_hits_by_bucket[name])
        bs["est_eligible_in_bucket"] = round((elig / n_s) * n_pop, 2)
        bs["est_near_miss_in_bucket"] = round(
            (near_hits_by_bucket[name] / n_s) * n_pop, 2
        )

    retrieved = int(loss_hist.get("RETRIEVED", 0))
    nopath_near = len(freeze_nopath)
    g1_non_low = int(gate_counts.get("G1_eligible_but_non_low", 0))
    g2 = int(gate_counts.get("G2_veto_or_hard", 0))

    if retrieved == 0 and nopath_near == 0 and int(sum(near_hits_by_bucket.values())) == 0:
        classification = "SOURCE_ABSENT"
        first_loss_stage = "source_scarcity"
    elif retrieved > 0 and g1_non_low > 0 and g1_non_low >= max(1, retrieved // 2):
        classification = "UPSTREAM_LOSS@G1"
        first_loss_stage = "tier_gate"
    elif retrieved == 0 and nopath_near > 0:
        classification = "UPSTREAM_LOSS@G3"
        first_loss_stage = "semantic_loss"
    elif retrieved == 0 and g2 > 0 and int(sum(near_hits_by_bucket.values())) > 0:
        classification = "UPSTREAM_LOSS@G2"
        first_loss_stage = "veto"
    elif retrieved > 0:
        classification = "UPSTREAM_LOSS@LIVE_OR_CAP"
        first_loss_stage = "runtime_or_cap_or_live_path"
    else:
        classification = "RESIDUAL_SCARCITY_COMPATIBLE"
        first_loss_stage = "source_scarcity"

    payload = {
        "evidence_id": EV,
        "generated_at": now.isoformat(),
        "head": head,
        "runtime_sec": round(time.time() - t0, 2),
        "read_only": True,
        "mode": "OFFLINE_PREP_READONLY",
        "pre_registered": pre_registered,
        "isolation": {
            **{k: int(iso[k]) for k in iso},
            "expect_human_labels": EXPECT_HL,
            "expect_ai_confirmed": EXPECT_AC,
            "isolation_ok": isolation_ok,
        },
        "sample": {
            "per_bucket": args.per_bucket,
            "stride": args.stride,
            "offset": args.offset,
            "n_sampled_total": len(samples),
            "discovery_version": DISCOVERY_VERSION_V6,
        },
        "bucket_stats": bucket_stats,
        "first_loss_histogram": dict(loss_hist),
        "first_loss_by_bucket": {k: dict(v) for k, v in loss_by_bucket.items()},
        "gate_counts": dict(gate_counts),
        "eligible_by_tier": dict(eligible_by_tier),
        "community_class_hist": dict(community_class_hist),
        "selection_corrected_estimates": {
            "est_disc_v6_eligible_messages": round(est_eligible, 2),
            "est_near_miss_buyer_rx_messages": round(est_near, 2),
            "est_refined_commercial_rx_messages": round(est_refined, 2),
        },
        "classification": classification,
        "first_loss_stage": first_loss_stage,
        "freeze_counts": {
            "nopath_near_miss": len(freeze_nopath),
            "veto_near_or_refined": len(freeze_veto),
            "eligible_non_low": len(freeze_eligible_non_low),
        },
        "business": {
            "outreach": False,
            "ml_go": False,
            "threshold_loosen": False,
            "discovery_semantic_change": False,
            "path_b_path_c_reopened": False,
        },
        "next": (
            "WP2: adjudicate freeze samples into label_reviews only. "
            "If SOURCE_ABSENT: owner sourcing decision (not path reopen). "
            "Remain OBSERVE for live A/B/C."
        ),
    }
    freeze_doc = {
        "evidence_id": EV,
        "generated_at": now.isoformat(),
        "head": head,
        "nopath_near_miss": freeze_nopath,
        "veto_near_or_refined": freeze_veto,
        "eligible_non_low": freeze_eligible_non_low,
    }

    lines = [
        f"Evidence {EV}: Offline corpus loss attribution (WP1 / Mode 1)",
        "experiment_id=OFFLINE_CORPUS_LOSS_ATTRIBUTION_360D",
        f"decision=OBSERVE / {classification}",
        "path_freeze=path_b+path_c",
        "shadow=OFF ml_training_enabled=false",
        "code_change=read-only audit script only (no discovery semantics)",
        f"generated_at={now.isoformat()} head={head}",
        f"runtime_sec={payload['runtime_sec']} read_only=true",
        "",
        "=== 0. PRE-REGISTERED ===",
        f"  hypothesis={pre_registered['hypothesis']}",
        f"  decision_rule={pre_registered['decision_rule']}",
        "",
        "=== 1. ISOLATION ===",
        f"  human_labels={iso['human_labels']} AI_CONFIRMED={iso['ai_confirmed']} "
        f"isolation_ok={isolation_ok}",
        f"  messages={iso['messages']} message_scores={iso['message_scores']} "
        f"disc_v6_NEW={iso['disc_v6_new']}",
        "",
        "=== 2. BUCKET POPULATIONS (messages-only) ===",
    ]
    for name, _, _ in AGE_BUCKETS:
        bs = bucket_stats[name]
        lines.append(
            f"  {name}: messages={bs['n_messages']} scored={bs['n_scored']} "
            f"coverage={bs['score_coverage']} sampled={bs['sampled']} "
            f"sample_eligible={bs.get('sample_eligible', 0)} "
            f"est_eligible≈{bs.get('est_eligible_in_bucket')} "
            f"est_near≈{bs.get('est_near_miss_in_bucket')}"
        )
    lines += [
        "",
        "=== 3. SAMPLE FIRST-LOSS (disc_v6 in-process; no DB writes) ===",
        f"  n_sampled={len(samples)} stride={args.stride} offset={args.offset}",
        f"  histogram={dict(loss_hist)}",
        f"  gate_counts={dict(gate_counts)}",
        f"  eligible_by_tier={dict(eligible_by_tier)}",
        f"  est_eligible≈{payload['selection_corrected_estimates']['est_disc_v6_eligible_messages']}",
        f"  est_near_miss_rx≈{payload['selection_corrected_estimates']['est_near_miss_buyer_rx_messages']}",
        "",
        "=== 4. CLASSIFICATION ===",
        f"  classification={classification}",
        f"  first_loss_stage={first_loss_stage}",
        f"  freeze_nopath_near={len(freeze_nopath)} freeze_veto={len(freeze_veto)} "
        f"freeze_eligible_non_low={len(freeze_eligible_non_low)}",
        "",
        "=== 5. BUSINESS / LOCKS ===",
        f"  {payload['business']}",
        "",
        "=== 6. NEXT ===",
        f"  {payload['next']}",
        "",
    ]
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    OUT_FREEZE.write_text(
        json.dumps(freeze_doc, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(OUT_TXT)
    print(
        json.dumps(
            {
                "classification": classification,
                "isolation_ok": isolation_ok,
                "n_sampled": len(samples),
                "retrieved": retrieved,
            },
            indent=2,
        )
    )
    return 0 if isolation_ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
