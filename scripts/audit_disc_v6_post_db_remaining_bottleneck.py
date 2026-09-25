#!/usr/bin/env python3
"""Evidence 101: disc_v6 post-DB remaining bottleneck (Cycle 1, measurement-first).

Read-only. Freezes the evidence-96/97/100 commercial∧domain population and
attributes disc_v6 losses vs disc_v4 on the same set. Does NOT flip enable flag,
mutate scores/labels/CRM, or change scoring.yaml.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
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
    DISCOVERY_VERSION,
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V6,
    evaluate_discovery,
    population_bucket,
)
from shared.db import SessionLocal
from shared.settings import get_settings

EV = "101"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-disc-v6-post-db-remaining-bottleneck.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-disc-v6-post-db-remaining-bottleneck.json"

# Evidence-96/97/100 byte-compatible pool regexes (FROZEN population definition).
POOL_COMMERCIAL = (
    r"(budget|fixed price|commission|need someone|"
    r"looking for (a )?(developer|contractor|freelancer)|"
    r"нужен разработчик|починить|Freelance Opportunity)"
)
POOL_DOMAIN = (
    r"(trading bot|bybit|binance|okx|pybit|arbitrage|futures|strategy|"
    r"торговый бот|Crypto Trading Bot)"
)

POOL_SELECT_SQL = """
SELECT m.id, m.text, m.author_id, m.community_id, m.message_date,
       c.name AS community_name, c.username AS community_username,
       s.score, s.commercial_score, s.technical_score, s.tier, s.scored_at
FROM messages m
JOIN message_scores s ON s.message_id = m.id
LEFT JOIN communities c ON c.id = m.community_id
WHERE m.message_date >= :since
  AND m.text ~* :comm
  AND m.text ~* :dom
ORDER BY m.message_date DESC, m.id DESC
"""

POOL_COUNT_SQL = """
SELECT
  COUNT(*) AS pool_n,
  COUNT(s.id) AS scored_n,
  COUNT(*) FILTER (WHERE s.id IS NULL) AS unscored_n,
  COUNT(*) FILTER (WHERE s.tier = 'LOW') AS low_n,
  COUNT(*) FILTER (WHERE s.tier = 'MEDIUM') AS med_n,
  COUNT(*) FILTER (WHERE s.tier = 'HIGH') AS high_n
FROM messages m
LEFT JOIN message_scores s ON s.message_id = m.id
WHERE m.message_date >= :since
  AND m.text ~* :comm
  AND m.text ~* :dom
"""

ISO_SQL = """
SELECT
  (SELECT COUNT(*) FROM human_labels) AS human_labels,
  (SELECT COUNT(*) FROM leads WHERE status = 'AI_CONFIRMED') AS ai_confirmed,
  (SELECT COUNT(*) FROM message_scores) AS scores,
  (SELECT COUNT(*) FROM messages) AS messages,
  (SELECT COUNT(*) FROM commercial_discovery_candidates) AS disc_candidates
"""

PROBE_SQL = """
SELECT l.id AS lead_id, l.message_id, l.tier AS lead_tier, l.status,
       m.text, m.community_id, c.name AS community_name, c.username,
       s.tier AS score_tier
FROM leads l
JOIN messages m ON m.id = l.message_id
LEFT JOIN communities c ON c.id = m.community_id
LEFT JOIN message_scores s ON s.message_id = m.id
WHERE l.status = 'AI_CONFIRMED'
ORDER BY l.id
"""

# Coarse semantic families for first-loss → bottleneck attribution.
LOSS_FAMILY = {
    "HARD_EXCLUDE_EARLY": "hard_veto",
    "VETO_job_aggregator": "hard_veto",
    "VETO_corporate_employment": "hard_veto",
    "VETO_generic_recruiter": "hard_veto",
    "VETO_job_seeker": "hard_veto",
    "VETO_service_provider": "hard_veto",
    "VETO_support_question": "hard_veto",
    "VETO_marketing_broadcast": "hard_veto",
    "VETO_news_digest": "hard_veto",
    "DIRECTION_SEEKER": "direction",
    "DIRECTION_PROVIDER": "direction",
    "DIRECTION_EMPLOYER": "direction",
    "DIRECTION_RECRUITER": "direction",
    "NO_PATH_MATCH": "semantic_miss",
    "RETRIEVED": "survivor",
    "NOT_SCORED": "score_gap",
    "NOT_LOW_PREFILTER_SHAPE": "tier_shape",
}


def _preview(t: str, n: int = 140) -> str:
    return " ".join((t or "").split())[:n]


def _git_head() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
            ).strip()
        )
    except Exception:
        return "unknown"


def first_loss(feats) -> str:
    if feats.hard_exclude and not (
        feats.project_scope
        or feats.soft_direction
        or feats.repair_signal
        or getattr(feats, "gig_project_carve", False)
    ):
        return "HARD_EXCLUDE_EARLY"
    if feats.veto_categories:
        return "VETO_" + feats.veto_categories[0]
    if not feats.eligible:
        if feats.buyer_direction in ("SEEKER", "PROVIDER", "EMPLOYER", "RECRUITER"):
            return f"DIRECTION_{feats.buyer_direction}"
        return "NO_PATH_MATCH"
    return "RETRIEVED"


def contamination(cands: list) -> float:
    non_buyer = 0
    for _mid, _text, feats, bucket in cands:
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
            or getattr(feats, "gig_project_carve", False)
        ):
            continue
        non_buyer += 1
    n = len(cands) or 1
    return round(100.0 * non_buyer / n, 1)


def quality_counts(eligible: list) -> dict:
    """Heuristic contamination labels among eligible survivors (audit plane)."""
    c = Counter()
    for _mid, text, feats, bucket in eligible:
        clean = (text or "").lower()
        if bucket == "Direct buyer/RFQ candidate" or feats.project_scope:
            c["buyer_project"] += 1
        if feats.recruiter_signal or feats.buyer_direction == "RECRUITER":
            if not getattr(feats, "gig_project_carve", False):
                c["recruiter"] += 1
        if feats.employment_signal and not feats.project_scope:
            c["employment"] += 1
        if feats.aggregator_signal and not getattr(feats, "gig_project_carve", False):
            c["aggregator"] += 1
        if feats.provider_signal and not feats.project_scope:
            c["provider"] += 1
        if feats.support_signal and not feats.project_scope:
            c["support"] += 1
        if feats.marketing_signal:
            c["marketing"] += 1
        if "freelance opportunity" in clean and getattr(feats, "gig_project_carve", False):
            c["fo_carve"] += 1
    return dict(c)


def duplicate_stats(eligible: list) -> dict:
    by_author = Counter()
    by_fp = Counter()
    for mid, text, feats, _bucket in eligible:
        by_author[feats.buyer_direction + ":" + str(mid)]  # noop keep structure
        aid = None
        # author not on feats; use preview fingerprint
        fp = _preview(text, 80).lower()
        by_fp[fp] += 1
    dup_previews = sum(1 for v in by_fp.values() if v > 1)
    dup_msgs = sum(v for v in by_fp.values() if v > 1)
    return {
        "eligible_n": len(eligible),
        "unique_preview80": len(by_fp),
        "duplicate_preview_groups": dup_previews,
        "messages_in_dup_groups": dup_msgs,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=360)
    parser.add_argument(
        "--since-iso",
        default=None,
        help="Optional fixed since ISO timestamp to freeze rolling window",
    )
    args = parser.parse_args()

    t0 = time.time()
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    if args.since_iso:
        since = datetime.fromisoformat(args.since_iso.replace("Z", "+00:00"))
    else:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)

    async with SessionLocal() as session:
        pool_counts = dict(
            (
                await session.execute(
                    sql_text(POOL_COUNT_SQL),
                    {"since": since, "comm": POOL_COMMERCIAL, "dom": POOL_DOMAIN},
                )
            )
            .mappings()
            .one()
        )
        rows = [
            dict(x)
            for x in (
                await session.execute(
                    sql_text(POOL_SELECT_SQL),
                    {"since": since, "comm": POOL_COMMERCIAL, "dom": POOL_DOMAIN},
                )
            )
            .mappings()
            .all()
        ]
        iso = dict((await session.execute(sql_text(ISO_SQL))).mappings().one())
        probes_db = [
            dict(x) for x in (await session.execute(sql_text(PROBE_SQL))).mappings().all()
        ]

    # Tier / prefilter-shape note for discovery SQL (LOW-only path).
    low_shape_n = 0
    for row in rows:
        if (row.get("tier") or "") != "LOW":
            continue
        text = (row.get("text") or "").lower()
        cs = float(row.get("commercial_score") or 0)
        ts = float(row.get("technical_score") or 0)
        if (cs > 0 and ts > 0) or any(
            k in text
            for k in (
                "trading bot",
                "торговый бот",
                "bybit",
                "binance api",
                "pybit",
                "freelance opportunity",
            )
        ):
            low_shape_n += 1

    version_stats: dict[str, dict] = {}
    eligible_by_ver: dict[str, list] = {}
    samples_by_loss: dict[str, dict] = {}

    # Single pass per version; keep feats for fair compare without re-eval.
    feats_by_ver: dict[str, dict[int, object]] = {
        DISCOVERY_VERSION_V4: {},
        DISCOVERY_VERSION_V6: {},
    }

    for ver in (DISCOVERY_VERSION_V4, DISCOVERY_VERSION_V6):
        eligible = []
        first_losses: Counter = Counter()
        loss_family: Counter = Counter()
        veto_rates: Counter = Counter()
        carve_n = 0
        eligible_low = 0
        samples: dict[str, list] = defaultdict(list)

        for row in rows:
            text = row["text"] or ""
            feats = evaluate_discovery(scorer, text, version=ver)
            feats_by_ver[ver][row["id"]] = feats
            fl = first_loss(feats)
            first_losses[fl] += 1
            fam = LOSS_FAMILY.get(fl, "other")
            loss_family[fam] += 1
            for v in feats.veto_categories:
                veto_rates[v] += 1
            if getattr(feats, "gig_project_carve", False):
                carve_n += 1
            bucket = population_bucket(feats)
            if feats.eligible:
                eligible.append((row["id"], text, feats, bucket))
                if (row.get("tier") or "") == "LOW":
                    eligible_low += 1
            if len(samples[fl]) < 5:
                samples[fl].append(
                    {
                        "message_id": row["id"],
                        "community": row.get("community_name"),
                        "tier": row.get("tier"),
                        "direction": feats.buyer_direction,
                        "trigger": feats.trigger_type,
                        "paths": list(feats.matched_paths or []),
                        "carve": bool(getattr(feats, "gig_project_carve", False)),
                        "veto": list(feats.veto_categories or []),
                        "bucket": bucket,
                        "preview": _preview(text),
                    }
                )

        n_exam = len(rows)
        n_elig = len(eligible)
        version_stats[ver] = {
            "examined": n_exam,
            "eligible": n_elig,
            "eligible_pct": round(100.0 * n_elig / max(n_exam, 1), 2),
            "eligible_LOW": eligible_low,
            "no_path_match": first_losses.get("NO_PATH_MATCH", 0),
            "veto_job_aggregator_first_loss": first_losses.get("VETO_job_aggregator", 0),
            "contamination_pct": contamination(eligible),
            "first_loss": dict(first_losses.most_common()),
            "loss_family": dict(loss_family.most_common()),
            "veto_rates": dict(veto_rates.most_common()),
            "carve_true_count": carve_n,
            "eligible_triggers": dict(
                Counter(f.trigger_type for _, _, f, _ in eligible if f.trigger_type)
            ),
            "eligible_buckets": dict(Counter(b for _, _, _, b in eligible)),
            "eligible_directions": dict(
                Counter(f.buyer_direction for _, _, f, _ in eligible)
            ),
            "quality": quality_counts(eligible),
            "duplicates": duplicate_stats(eligible),
        }
        eligible_by_ver[ver] = eligible
        samples_by_loss[ver] = dict(samples)

    v4 = version_stats[DISCOVERY_VERSION_V4]
    v6 = version_stats[DISCOVERY_VERSION_V6]

    newly: list[dict] = []
    lost_v6: list[dict] = []
    for row in rows:
        text = row["text"] or ""
        f4 = feats_by_ver[DISCOVERY_VERSION_V4][row["id"]]
        f6 = feats_by_ver[DISCOVERY_VERSION_V6][row["id"]]
        if f6.eligible and not f4.eligible:
            newly.append(
                {
                    "message_id": row["id"],
                    "community": row.get("community_name"),
                    "tier": row.get("tier"),
                    "carve": f6.gig_project_carve,
                    "trigger": f6.trigger_type,
                    "paths": list(f6.matched_paths or []),
                    "direction": f6.buyer_direction,
                    "v4_first_loss": first_loss(f4),
                    "preview": _preview(text),
                }
            )
        if f4.eligible and not f6.eligible:
            lost_v6.append(
                {
                    "message_id": row["id"],
                    "community": row.get("community_name"),
                    "v4_fl": first_loss(f4),
                    "v6_fl": first_loss(f6),
                    "preview": _preview(text),
                }
            )

    probes_out = []
    fo_recovered = 0
    for p in probes_db:
        text = p["text"] or ""
        f4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
        f6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        score_tier = p.get("score_tier") or p.get("lead_tier")
        recovered = bool(f6.eligible) and (
            "job_aggregator" in (f4.veto_categories or []) or not f4.eligible
        )
        if recovered:
            fo_recovered += 1
        in_pool = any(r["id"] == p["message_id"] for r in rows)
        probes_out.append(
            {
                "lead_id": p["lead_id"],
                "message_id": p["message_id"],
                "score_tier": score_tier,
                "in_commercial_domain_pool": in_pool,
                "community": p.get("community_name"),
                "username": p.get("username"),
                "v4": {
                    "eligible": f4.eligible,
                    "first_loss": first_loss(f4),
                    "veto": list(f4.veto_categories),
                    "carve": f4.gig_project_carve,
                },
                "v6": {
                    "eligible": f6.eligible,
                    "first_loss": first_loss(f6),
                    "veto": list(f6.veto_categories),
                    "carve": f6.gig_project_carve,
                    "paths": list(f6.matched_paths or []),
                    "trigger": f6.trigger_type,
                    "direction": f6.buyer_direction,
                },
                "discovery_eval_recovered": recovered,
                "note_high_tier_prefilter_separate": score_tier in ("HIGH", "MEDIUM"),
                "preview": _preview(text),
            }
        )

    # Largest remaining bottleneck among NON-survivors on the pool under disc_v6.
    fl6 = Counter(v6["first_loss"])
    fl6.pop("RETRIEVED", None)
    largest_loss, largest_n = fl6.most_common(1)[0] if fl6 else ("NONE", 0)
    fam6 = Counter(v6["loss_family"])
    fam6.pop("survivor", None)
    largest_fam, largest_fam_n = fam6.most_common(1)[0] if fam6 else ("NONE", 0)

    # Hypothesis selection (measurement-only). Raw first-loss counts alone are insufficient:
    # commercial∧domain regex often floods with exchange "commission"/"strategy" noise.
    # Callers should run NO_PATH buyerish inventory before treating semantic_miss as actionable.
    no_path = v6["first_loss"].get("NO_PATH_MATCH", 0)
    hard_veto_n = v6["loss_family"].get("hard_veto", 0)
    survivors = v6["eligible"]
    pool_n = int(pool_counts["pool_n"] or 0)

    if largest_loss == "NO_PATH_MATCH" and no_path > survivors:
        bottleneck = "raw_NO_PATH_MATCH_needs_buyerish_inventory"
        hypothesis = (
            "Dominant raw first-loss is NO_PATH_MATCH. Before any disc_v6 path change, "
            "inventory buyerish-clean vs commission/strategy regex noise; if buyerish_clean "
            "is tiny, the bottleneck is population definition (pool regex / community class), "
            "not discovery semantics."
        )
        acceptance = {
            "metric": "buyerish_clean_nopath_and_refined_survivor_rate",
            "baseline": survivors,
            "target_delta": (
                "complete buyerish inventory; if buyerish_clean<<NO_PATH, refine pool "
                f"(survivor_rate >=10% without losing survivors>={survivors})"
            ),
            "regression": [
                "AI_CONFIRMED FO probes remain 4/4 discovery-eval recovered",
                "contamination_pct does not rise > +2pp vs baseline",
                "human_labels=1524 AI_CONFIRMED=4 unchanged",
                "commercial_discovery_enabled stays false",
            ],
        }
    elif hard_veto_n > survivors and largest_fam == "hard_veto":
        bottleneck = f"hard_veto_dominated:{largest_loss}"
        hypothesis = (
            f"Dominant first-loss is {largest_loss} (family={largest_fam}, n={largest_n}). "
            "Next: measure how many hard-vetoed rows are true buyer/project false negatives "
            "vs correctly vetoed employment/aggregator; only then consider a narrow carve."
        )
        acceptance = {
            "metric": "false_negative_recovery_under_veto",
            "baseline": survivors,
            "target_delta": "recover >=10 verified buyer/project FNs without contamination > baseline+2pp",
            "regression": [
                "FO probes 4/4",
                "no global veto removal",
                "isolation invariants",
            ],
        }
    else:
        bottleneck = "recall_near_ceiling_or_mixed"
        hypothesis = (
            "Survivors are not dwarfed by a single loss family; next cycle should quantify "
            "LOW-prefilter visibility / enable-flag activation readiness rather than semantics."
        )
        acceptance = {
            "metric": "disc_v6_eligible_LOW_visible",
            "baseline": v6.get("eligible_LOW"),
            "target_delta": "document activation checklist; do not flip enable without auth",
            "regression": ["flag remains OFF until authorized"],
        }

    fair_compare = {
        "population": "commercial∧domain 360d scored (frozen regex)",
        "disc_v4": {
            "eligible": v4["eligible"],
            "eligible_LOW": v4["eligible_LOW"],
            "contamination_pct": v4["contamination_pct"],
            "no_path_match": v4["no_path_match"],
            "veto_job_aggregator_first_loss": v4["veto_job_aggregator_first_loss"],
            "loss_family": v4["loss_family"],
        },
        "disc_v6": {
            "eligible": v6["eligible"],
            "eligible_LOW": v6["eligible_LOW"],
            "contamination_pct": v6["contamination_pct"],
            "no_path_match": v6["no_path_match"],
            "veto_job_aggregator_first_loss": v6["veto_job_aggregator_first_loss"],
            "carve_true_count": v6["carve_true_count"],
            "loss_family": v6["loss_family"],
        },
        "delta_eligible_v6_minus_v4": v6["eligible"] - v4["eligible"],
        "newly_eligible_rows": len(newly),
        "lost_vs_v4_rows": len(lost_v6),
        "carve_among_newly": sum(1 for n in newly if n["carve"]),
    }

    # Baselines from Evidence 99/100 (reconstructed constants for report).
    baselines = {
        "evidence_99": {
            "cap_7d_examined": 2500,
            "disc_v4_eligible": 4,
            "disc_v6_eligible": 14,
            "contamination_v6": 0.0,
            "fo_probes": "4/4",
            "decision": "SHIP_DEFAULT_V6",
            "enabled_flag": False,
            "version_at_evidence": "disc_v4→flip default disc_v6",
        },
        "evidence_100": {
            "pool_before": {"n": 1300, "scored": 45},
            "pool_after": {"n": 1300, "scored": 1300},
            "backfill": 1255,
            "disc_v6_eligible_any_tier_after": 32,
            "disc_v6_eligible_LOW_after": 26,
            "disc_v4_eligible_any_tier_after": 19,
            "disc_v4_eligible_LOW_after": 19,
            "human_labels": 1524,
            "ai_confirmed": 4,
        },
        "head_expected": "c553f52",
    }

    code_change_justified = False
    # Cycle-1 policy: measurement only unless a smallest deterministic fix is obvious
    # AND acceptance metrics are clear. Semantic inventory is required first → no code.
    decision = "MEASUREMENT_ONLY_STOP"
    decision_rationale = (
        f"Largest bottleneck={bottleneck} (n={largest_n} first_loss={largest_loss}; "
        f"family={largest_fam} n={largest_fam_n}). Survivors disc_v6={survivors} vs "
        f"disc_v4={v4['eligible']}. A concrete template inventory + FN verification is "
        "required before any path/carve change; no smallest safe code change this cycle."
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "runtime_sec": round(time.time() - t0, 2),
        "read_only": True,
        "code_change_this_cycle": False,
        "code_change_justified": code_change_justified,
        "decision": decision,
        "decision_rationale": decision_rationale,
        "cycle": 1,
        "settings": {
            "commercial_discovery_enabled": settings.commercial_discovery_enabled,
            "commercial_discovery_version": settings.commercial_discovery_version,
            "code_default_DISCOVERY_VERSION": DISCOVERY_VERSION,
        },
        "population_definition_frozen": {
            "name": "commercial_and_domain_360d_evidence_96_97_100",
            "days": args.days,
            "since": since.isoformat(),
            "commercial_regex": POOL_COMMERCIAL,
            "domain_regex": POOL_DOMAIN,
            "join": "message_scores INNER (scored only for attribution)",
            "evidence_100_pool_n": 1300,
            "observed_pool_n": int(pool_counts["pool_n"] or 0),
            "observed_scored_n": int(pool_counts["scored_n"] or 0),
            "observed_unscored_n": int(pool_counts["unscored_n"] or 0),
            "tier_breakdown": {
                "LOW": int(pool_counts["low_n"] or 0),
                "MEDIUM": int(pool_counts["med_n"] or 0),
                "HIGH": int(pool_counts["high_n"] or 0),
            },
            "low_prefilter_shape_approx": low_shape_n,
            "note_vs_evidence_100": (
                "Rolling 360d window may drift ±1 from evidence-100 pool_n=1300; "
                "regex bytes identical."
            ),
        },
        "baselines_reconstructed": baselines,
        "isolation": iso,
        "version_stats": version_stats,
        "fair_compare_v4_vs_v6": fair_compare,
        "newly_eligible_vs_v4": {
            "count": len(newly),
            "carve_true": sum(1 for n in newly if n["carve"]),
            "samples": newly[:30],
        },
        "lost_vs_v4": {"count": len(lost_v6), "samples": lost_v6[:10]},
        "loss_samples_v6": {
            k: samples_by_loss[DISCOVERY_VERSION_V6].get(k, [])[:5]
            for k in list(dict(v6["first_loss"]).keys())[:8]
        },
        "probes": probes_out,
        "fo_false_negative_recovery": f"{fo_recovered}/{len(probes_out)}",
        "largest_remaining_bottleneck": {
            "first_loss_label": largest_loss,
            "first_loss_n": largest_n,
            "loss_family": largest_fam,
            "loss_family_n": largest_fam_n,
            "bottleneck_id": bottleneck,
            "survivors_v6": survivors,
            "pool_n": pool_n,
            "survivor_rate_pct": round(100.0 * survivors / max(pool_n, 1), 2),
        },
        "next_hypothesis": {
            "statement": hypothesis,
            "acceptance": acceptance,
            "regression_metrics": acceptance.get("regression", []),
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"Evidence {EV}: disc_v6 post-DB remaining bottleneck (Cycle 1)",
        f"generated_at={summary['generated_at']} head={summary['head']}",
        f"runtime_sec={summary['runtime_sec']} read_only=true code_change=false",
        f"decision={decision}",
        "",
        "=== 0. POPULATION FROZEN ===",
        f"  days={args.days} since={since.isoformat()}",
        f"  commercial_regex={POOL_COMMERCIAL}",
        f"  domain_regex={POOL_DOMAIN}",
        f"  pool_n={pool_counts['pool_n']} scored={pool_counts['scored_n']} "
        f"unscored={pool_counts['unscored_n']} "
        f"(evidence_100 pool was 1300/1300)",
        f"  tiers LOW/MED/HIGH={pool_counts['low_n']}/{pool_counts['med_n']}/{pool_counts['high_n']}",
        f"  low_prefilter_shape_approx={low_shape_n}",
        "",
        "=== 1. BASELINES (Evidence 99/100 reconstructed) ===",
        f"  E99: v4_elig=4 v6_elig=14 contam_v6=0% FO=4/4 SHIP_DEFAULT_V6 enabled=false",
        f"  E100: backfill 1255 → pool 1300/1300; v6 any/LOW=32/26; v4 any/LOW=19/19",
        f"  HEAD expected c553f52 actual={summary['head']}",
        "",
        "=== 2. FAIR COMPARE disc_v4 vs disc_v6 (same frozen pool) ===",
        f"  disc_v4: eligible={v4['eligible']} LOW={v4['eligible_LOW']} "
        f"contam={v4['contamination_pct']}% NO_PATH={v4['no_path_match']} "
        f"VETO_job_agg={v4['veto_job_aggregator_first_loss']}",
        f"  disc_v6: eligible={v6['eligible']} LOW={v6['eligible_LOW']} "
        f"contam={v6['contamination_pct']}% NO_PATH={v6['no_path_match']} "
        f"VETO_job_agg={v6['veto_job_aggregator_first_loss']} carve={v6['carve_true_count']}",
        f"  delta eligible v6-v4={v6['eligible'] - v4['eligible']} "
        f"newly_rows={len(newly)} lost_rows={len(lost_v6)} "
        f"carve_among_new={sum(1 for n in newly if n['carve'])}",
        f"  loss_family v4={v4['loss_family']}",
        f"  loss_family v6={v6['loss_family']}",
        "",
        "=== 3. disc_v6 FIRST-LOSS ATTRIBUTION ===",
        f"  first_loss={v6['first_loss']}",
        f"  quality(eligible)={v6['quality']}",
        f"  duplicates={v6['duplicates']}",
        f"  eligible_buckets={v6['eligible_buckets']}",
        f"  eligible_directions={v6['eligible_directions']}",
        f"  eligible_triggers={v6['eligible_triggers']}",
        "",
        "=== 4. AI_CONFIRMED PROBES ===",
    ]
    for p in probes_out:
        lines.append(
            f"  lead={p['lead_id']} msg={p['message_id']} tier={p['score_tier']} "
            f"in_pool={p['in_commercial_domain_pool']} "
            f"v4={p['v4']['first_loss']} v6_elig={p['v6']['eligible']} "
            f"carve={p['v6']['carve']} recovered={p['discovery_eval_recovered']}"
        )
    lines.extend(
        [
            f"  FO discovery-eval recovery: {fo_recovered}/{len(probes_out)}",
            "",
            "=== 5. LARGEST REMAINING BOTTLENECK ===",
            f"  id={bottleneck}",
            f"  first_loss={largest_loss} n={largest_n}",
            f"  family={largest_fam} n={largest_fam_n}",
            f"  survivors_v6={survivors}/{pool_n} "
            f"({summary['largest_remaining_bottleneck']['survivor_rate_pct']}%)",
            "",
            "=== 6. NEXT HYPOTHESIS ===",
            f"  {hypothesis}",
            f"  acceptance={json.dumps(acceptance, ensure_ascii=False)}",
            "",
            "=== 7. LOSS SAMPLES (disc_v6, up to 5 each top losses) ===",
        ]
    )
    for fl_label, _n in list(Counter(v6["first_loss"]).most_common(5)):
        if fl_label == "RETRIEVED":
            continue
        for s in samples_by_loss[DISCOVERY_VERSION_V6].get(fl_label, [])[:3]:
            lines.append(
                f"  [{fl_label}] id={s['message_id']} {s['community']} | {s['preview']}"
            )

    lines.extend(
        [
            "",
            "=== 8. ISOLATION / SETTINGS ===",
            f"  human_labels={iso['human_labels']} AI_CONFIRMED={iso['ai_confirmed']} "
            f"scores={iso['scores']} messages={iso['messages']} "
            f"disc_candidates={iso['disc_candidates']}",
            f"  commercial_discovery_enabled={settings.commercial_discovery_enabled}",
            f"  commercial_discovery_version={settings.commercial_discovery_version}",
            f"  DISCOVERY_VERSION={DISCOVERY_VERSION}",
            "",
            "=== 9. BUSINESS ===",
            "  NO OUTREACH / NO ML GO / FLAG REMAINS OFF / NO THRESHOLD LOOSEN",
            f"  rationale: {decision_rationale}",
            "",
            f"JSON: {OUT_JSON}",
        ]
    )
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
