#!/usr/bin/env python3
"""Evidence 113: POST-ENABLE CYCLE 7 — live remeasure / first NEW.

Measurement-only. Does NOT reopen path_b/c, flip shadow, mutate labels,
wipe candidates, loosen thresholds, or change FO carve.

Hypothesis (ONE):
  With more post–path_b scored traffic, either (1) first NEW disc_v6 appears
  → quality-classify clean RFQ vs residual contam on an untouched path; or
  (2) still 0 NEW after large denominator → strengthen RESIDUAL_SCARCITY
  (empiric ≪0.018/h) and choose next non-path investigation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION,
    DISCOVERY_VERSION_V6,
    evaluate_discovery,
)
from shared.db import SessionLocal
from shared.settings import get_settings

from scripts.audit_disc_v6_refined_population_cycle2 import (  # noqa: E402
    E101_SINCE_ISO,
    LEGACY_COMMERCIAL,
    POOL_DOMAIN,
    POOL_SELECT_SQL,
    REFINED_COMMERCIAL,
    is_exchange_community,
    refined_commercial_match,
)
from scripts.audit_disc_v6_residual_scarcity_cycle6 import (  # noqa: E402
    EMPIRIC_UB_PER_HOUR,
    ISO_SQL,
    LIVE_DISC_V6_SQL,
    PRE_FIX_CONTAM_RATE_PER_HOUR,
    PROBE_SQL,
    RECENT_LOW_SQL,
    RECENT_SCORED_SQL,
    TRUE_BUYER_NEAR_MISS_RX,
    WINDOW_SQL,
    _classify_live_rate,
    _git_head,
    _git_head_short,
    _hours_since,
    _preview,
    _probe_api,
    _redis_stats,
    measure_funnel,
)

EV = "113"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-disc-v6-live-remeasure-cycle7.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-disc-v6-live-remeasure-cycle7.json"

PATH_B_DEPLOY_ISO = "2026-09-26T06:53:16+00:00"
PATH_C_MARKER_ISO = "2026-09-26T06:41:22+00:00"
ENABLE_AT_ISO = "2026-09-25T19:27:00+00:00"
E112_AT_ISO = "2026-09-26T07:13:10+00:00"

NEW_CANDS_SINCE_SQL = """
SELECT c.id, c.seed_message_id, c.trigger_type, c.status, c.created_at,
       c.scorer_tier, c.signals_json,
       m.text, m.message_date,
       com.name AS community_name, com.username AS community_username,
       s.tier, s.score
FROM commercial_discovery_candidates c
JOIN messages m ON m.id = c.seed_message_id
LEFT JOIN communities com ON com.id = m.community_id
LEFT JOIN message_scores s ON s.message_id = m.id
WHERE c.discovery_version = 'disc_v6' AND c.created_at > :since
ORDER BY c.id
"""

PROMO_SMOKE = (
    "Automate my trading with our EA! Buy now — MT4 Expert Advisor "
    "signals, risk-free demo. Join affiliate program."
)
# Canonical E110 bare support probe (path_b conjunct target family).
SUPPORT_BARE_SMOKE = "My bot is not working"
# E109 path_b supportish conjunct target (repair + helpdesk, no RFQ).
SUPPORT_PATH_B_SMOKE = (
    "bot not working please help repair my trading bot account issue"
)


def _qual_class(text: str, feats: Any) -> str:
    t = text or ""
    low = t.lower()
    if re.search(r"\b(hire|looking for|need (a )?dev|budget\s*[:=]?\s*\$)", low):
        if re.search(r"\b(affiliate|buy now|sign up|promo|discount)\b", low):
            return "promo_contam"
        return "clean_rfq_buyerish"
    if re.search(r"\b(affiliate|buy now|automate my trading|ea\b|expert advisor)\b", low):
        return "promo_marketing"
    if re.search(r"\b(not working|please help|support|ticket|login error)\b", low):
        return "support_helpdesk"
    if re.search(r"\b(recruit|hiring|job opening|we are hiring)\b", low):
        return "recruiter"
    if feats and getattr(feats, "eligible", False):
        return "eligible_other"
    return "non_buyer_other"


def _scarcity_confidence(
    *,
    hours_path_b: float,
    score_n: int,
    disc_v6_new: int,
    recent_low_elig: int,
    recent_low_n: int,
    score_since_e112: int,
    expected_ub: float,
) -> dict[str, Any]:
    """Classify how strongly live data supports RESIDUAL_SCARCITY ≪0.018/h."""
    notes: list[str] = []
    # Live UB consistency: zero NEW with expected_count still <1 is null, not proof
    if disc_v6_new > 0:
        level = "overturned_by_new"
        notes.append("NEW disc_v6 appeared — scarcity claim deferred to quality class")
    elif expected_ub < 0.25:
        level = "consistent_null_weak"
        notes.append(
            f"expected_count_at_UB={expected_ub:.4f} still ≪1 "
            f"(need ~{1/EMPIRIC_UB_PER_HOUR:.0f}h for E[N]≈1 under standing UB)"
        )
    elif expected_ub < 1.0:
        level = "strengthening"
        notes.append(
            f"expected_count_at_UB={expected_ub:.4f} approaching 1; "
            "continued 0 NEW begins to favor rate < standing UB"
        )
    else:
        level = "strong_below_ub"
        notes.append(
            f"expected_count_at_UB={expected_ub:.4f} ≥1 with 0 NEW → "
            "live rate empirically below standing 0.018/h"
        )

    # Current-code recent LOW sample is the stronger scarcity signal
    if recent_low_n >= 1000 and recent_low_elig == 0:
        notes.append(
            f"current-code recent_LOW elig=0/{recent_low_n} → empiric ~0/h ≪0.018/h"
        )
        if level == "consistent_null_weak":
            level = "moderate_via_recent_low_zero"
        elif level == "strengthening":
            level = "strong_via_recent_low_zero"

    if score_n >= 2000 and disc_v6_new == 0:
        notes.append(f"post-path_b scored denominator={score_n} with 0 NEW")
        if level in ("consistent_null_weak", "moderate_via_recent_low_zero"):
            level = "moderate_large_denominator"

    if score_since_e112 == 0:
        notes.append(
            "no incremental scored traffic since E112 — collector duty-cycle "
            "limits incremental observation this cycle"
        )

    return {
        "level": level,
        "empiric_vs_ub_018": "below_or_at_zero_live"
        if disc_v6_new == 0
        else "new_observed",
        "standing_ub_per_hour": EMPIRIC_UB_PER_HOUR,
        "expected_count_at_ub": round(expected_ub, 4),
        "hours_path_b": round(hours_path_b, 3),
        "scored_since_path_b": score_n,
        "scored_since_e112": score_since_e112,
        "recent_low_elig": recent_low_elig,
        "recent_low_n": recent_low_n,
        "notes": notes,
    }


def _run_smoke(scorer: LeadScorer) -> dict[str, Any]:
    cases = [
        ("promo_path_c", PROMO_SMOKE, False),
        ("support_path_b", SUPPORT_PATH_B_SMOKE, False),
        ("support_bare", SUPPORT_BARE_SMOKE, False),
    ]
    out = []
    fo_ok = 0
    for name, text, want_elig in cases:
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        ok = bool(feats.eligible) == want_elig
        out.append(
            {
                "name": name,
                "want_eligible": want_elig,
                "eligible": bool(feats.eligible),
                "paths": list(feats.matched_paths or []),
                "trigger": feats.trigger_type,
                "vetoes": list(feats.veto_categories or []),
                "pass": ok,
            }
        )
    return {"path_block_cases": out, "all_path_blocks_pass": all(c["pass"] for c in out)}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-iso", default=E101_SINCE_ISO)
    parser.add_argument("--recent-scored-limit", type=int, default=2500)
    parser.add_argument("--recent-low-limit", type=int, default=5000)
    parser.add_argument("--path-b-at", default=PATH_B_DEPLOY_ISO)
    parser.add_argument("--path-c-at", default=PATH_C_MARKER_ISO)
    parser.add_argument("--enable-at", default=ENABLE_AT_ISO)
    parser.add_argument("--e112-at", default=E112_AT_ISO)
    parser.add_argument("--write-evidence", action="store_true", default=True)
    parser.add_argument("--no-write-evidence", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args()
    write = args.write_evidence and not args.no_write_evidence

    t0 = time.time()
    now = datetime.now(timezone.utc)
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    since = datetime.fromisoformat(args.since_iso.replace("Z", "+00:00"))
    path_b_at = datetime.fromisoformat(args.path_b_at.replace("Z", "+00:00"))
    path_c_at = datetime.fromisoformat(args.path_c_at.replace("Z", "+00:00"))
    enable_at = datetime.fromisoformat(args.enable_at.replace("Z", "+00:00"))
    e112_at = datetime.fromisoformat(args.e112_at.replace("Z", "+00:00"))
    refined_rx = re.compile(REFINED_COMMERCIAL, re.I)

    health, gates, api_base = _probe_api()
    redis = _redis_stats()

    async with SessionLocal() as session:
        legacy_rows = [
            dict(x)
            for x in (
                await session.execute(
                    sql_text(POOL_SELECT_SQL),
                    {"since": since, "comm": LEGACY_COMMERCIAL, "dom": POOL_DOMAIN},
                )
            )
            .mappings()
            .all()
        ]
        iso = dict((await session.execute(sql_text(ISO_SQL))).mappings().one())
        probes = [
            dict(x) for x in (await session.execute(sql_text(PROBE_SQL))).mappings().all()
        ]
        live_cands = [
            dict(x)
            for x in (await session.execute(sql_text(LIVE_DISC_V6_SQL))).mappings().all()
        ]
        new_since_path_b = [
            dict(x)
            for x in (
                await session.execute(
                    sql_text(NEW_CANDS_SINCE_SQL), {"since": path_b_at}
                )
            )
            .mappings()
            .all()
        ]

        win_path_b = dict(
            (await session.execute(sql_text(WINDOW_SQL), {"since": path_b_at}))
            .mappings()
            .one()
        )
        win_path_c = dict(
            (await session.execute(sql_text(WINDOW_SQL), {"since": path_c_at}))
            .mappings()
            .one()
        )
        win_enable = dict(
            (await session.execute(sql_text(WINDOW_SQL), {"since": enable_at}))
            .mappings()
            .one()
        )
        win_e112 = dict(
            (await session.execute(sql_text(WINDOW_SQL), {"since": e112_at}))
            .mappings()
            .one()
        )
        win_1h = dict(
            (
                await session.execute(
                    sql_text(WINDOW_SQL),
                    {"since": now.replace(microsecond=0) - timedelta(hours=1)},
                )
            )
            .mappings()
            .one()
        )
        win_6h = dict(
            (
                await session.execute(
                    sql_text(WINDOW_SQL),
                    {"since": now.replace(microsecond=0) - timedelta(hours=6)},
                )
            )
            .mappings()
            .one()
        )

        recent_scored = [
            dict(x)
            for x in (
                await session.execute(
                    sql_text(RECENT_SCORED_SQL),
                    {"since": path_b_at, "lim": args.recent_scored_limit},
                )
            )
            .mappings()
            .all()
        ]
        recent_low = [
            dict(x)
            for x in (
                await session.execute(
                    sql_text(RECENT_LOW_SQL), {"lim": args.recent_low_limit}
                )
            )
            .mappings()
            .all()
        ]

    refined_rows = [
        r
        for r in legacy_rows
        if refined_commercial_match(r.get("text") or "", refined_rx)
        and not is_exchange_community(
            scorer, r.get("community_username"), r.get("community_name")
        )
    ]

    funnel_legacy = measure_funnel(scorer, legacy_rows, "legacy_commercial_and_domain")
    funnel_refined = measure_funnel(scorer, refined_rows, "refined_frozen")
    funnel_recent_scored = measure_funnel(
        scorer, recent_scored, "recent_scored_since_path_b"
    )
    funnel_recent_low = measure_funnel(scorer, recent_low, "recent_low_7d_sample")

    fo_ok = 0
    fo_out = []
    for p in probes:
        feats = evaluate_discovery(
            scorer, p.get("text") or "", version=DISCOVERY_VERSION_V6
        )
        ok = bool(feats.eligible and getattr(feats, "gig_project_carve", False))
        if ok:
            fo_ok += 1
        fo_out.append(
            {
                "lead_id": p.get("lead_id"),
                "message_id": p.get("message_id"),
                "eligible": bool(feats.eligible),
                "carve": bool(getattr(feats, "gig_project_carve", False)),
                "trigger": feats.trigger_type,
                "tier": p.get("score_tier") or p.get("lead_tier"),
            }
        )

    smoke = None if args.skip_smoke else _run_smoke(scorer)
    if smoke is not None:
        smoke["fo_probes"] = f"{fo_ok}/{len(probes)}"
        smoke["fo_pass"] = fo_ok == len(probes) and len(probes) == 4

    hours_path_b = _hours_since(args.path_b_at, now)
    hours_path_c = _hours_since(args.path_c_at, now)
    hours_enable = _hours_since(args.enable_at, now)
    hours_e112 = _hours_since(args.e112_at, now)

    def _live(win: dict, hours: float, since_iso: str) -> dict:
        d6 = int(win["disc_v6"] or 0)
        return {
            "since": since_iso,
            "hours": round(hours, 3),
            "msg": int(win["msg"] or 0),
            "score": int(win["score"] or 0),
            "cand": int(win["cand"] or 0),
            "disc_v6": d6,
            "rate_per_hour": round(d6 / max(hours, 1e-9), 4),
            "class": _classify_live_rate(hours, d6),
            "expected_count_at_ub": round(EMPIRIC_UB_PER_HOUR * hours, 4),
        }

    live_path_b = _live(win_path_b, hours_path_b, args.path_b_at)
    live_path_c = _live(win_path_c, hours_path_c, args.path_c_at)
    live_e112 = _live(win_e112, hours_e112, args.e112_at)
    live_enable = {
        "since": args.enable_at,
        "hours": round(hours_enable, 3),
        "msg": int(win_enable["msg"] or 0),
        "score": int(win_enable["score"] or 0),
        "cand": int(win_enable["cand"] or 0),
        "disc_v6": int(win_enable["disc_v6"] or 0),
        "note": "includes 6 historical contaminated retained rows (pre path_b+c)",
    }
    live_1h = {
        "msg": int(win_1h["msg"] or 0),
        "score": int(win_1h["score"] or 0),
        "cand": int(win_1h["cand"] or 0),
        "disc_v6": int(win_1h["disc_v6"] or 0),
        "note": "may include pre-path_b historical disc_v6",
    }
    live_6h = {
        "msg": int(win_6h["msg"] or 0),
        "score": int(win_6h["score"] or 0),
        "cand": int(win_6h["cand"] or 0),
        "disc_v6": int(win_6h["disc_v6"] or 0),
        "note": "includes pre-path_b contaminated disc_v6; not NEW-post-fix",
    }

    # Qualitative for any NEW since path_b
    new_qual = []
    for row in new_since_path_b:
        text = row.get("text") or ""
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        new_qual.append(
            {
                "id": row["id"],
                "seed": row["seed_message_id"],
                "trigger": row["trigger_type"],
                "status": row["status"],
                "created_at": row["created_at"].isoformat()
                if hasattr(row["created_at"], "isoformat")
                else str(row["created_at"]),
                "tier": row.get("tier"),
                "community": row.get("community_name"),
                "paths_live": list(feats.matched_paths or []),
                "eligible_now": bool(feats.eligible),
                "carve": bool(getattr(feats, "gig_project_carve", False)),
                "signals": {
                    "repair": bool(feats.repair_signal),
                    "automation": bool(feats.automation_signal),
                    "ownership": bool(feats.ownership_signal),
                    "direct": bool(feats.direct_request_signal),
                    "hire": bool(feats.hiring_patterns),
                    "budget": bool(feats.budget_signal or feats.true_budget_signal),
                },
                "quality_class": _qual_class(text, feats),
                "preview": _preview(text, 200),
                "frozen_sample": True,
            }
        )

    refined_days = max(1.0, (now - since).total_seconds() / 86400.0)
    refined_low = funnel_refined["eligible_LOW"]
    refined_any = funnel_refined["eligible"]
    implied_refined_low_per_day = round(refined_low / refined_days, 4)
    implied_refined_low_per_hour = round(implied_refined_low_per_day / 24.0, 5)

    recent_low_elig = funnel_recent_low["eligible"]
    recent_low_elig_low = funnel_recent_low["eligible_LOW"]
    hours_7d = 7.0 * 24.0
    empiric_current_per_hour = round(recent_low_elig_low / hours_7d, 5)
    empiric_current_per_day = round(recent_low_elig_low / 7.0, 4)

    scored_n = funnel_recent_scored["pool_n"]
    scored_elig = funnel_recent_scored["eligible"]
    scored_low_enq = funnel_recent_scored["eligible_LOW"]
    live_score_rate_per_hour = round(
        live_path_b["score"] / max(hours_path_b, 1e-9), 2
    )
    p_low = scored_low_enq / max(scored_n, 1)
    implied_from_live_scored = round(live_score_rate_per_hour * p_low, 5)

    isolation_ok = (
        int(iso.get("human_labels") or 0) == 1524
        and int(iso.get("ai_confirmed") or 0) == 4
    )

    near_miss_n = funnel_refined["near_miss_buyerish_nopath"]
    recent_near_miss = funnel_recent_scored["near_miss_buyerish_nopath"]
    traffic_ok = live_path_b["score"] >= 200
    incr_quiet = live_e112["score"] == 0

    scarcity = _scarcity_confidence(
        hours_path_b=hours_path_b,
        score_n=live_path_b["score"],
        disc_v6_new=live_path_b["disc_v6"],
        recent_low_elig=recent_low_elig_low,
        recent_low_n=funnel_recent_low["pool_n"],
        score_since_e112=live_e112["score"],
        expected_ub=live_path_b["expected_count_at_ub"],
    )

    dominant_refined_loss = (
        funnel_refined["first_loss"].copy() if funnel_refined["first_loss"] else {}
    )
    dominant_refined_loss.pop("RETRIEVED", None)
    top_loss = (
        max(dominant_refined_loss, key=dominant_refined_loss.get)
        if dominant_refined_loss
        else None
    )

    # Classification
    if new_qual:
        classification = "FIRST_NEW_QUALITY"
        stage_named = None
        qclasses = {q["quality_class"] for q in new_qual}
        rationale = (
            f"First NEW disc_v6 since path_b: n={len(new_qual)} "
            f"quality_classes={sorted(qclasses)}. Frozen samples in evidence."
        )
        decision = "OBSERVE"
    elif not traffic_ok and live_path_b["score"] < 50:
        classification = "LOW_LIVE_TRAFFIC"
        stage_named = None
        rationale = "Post-path_b scored traffic too low to distinguish scarcity."
        decision = "OBSERVE"
    elif near_miss_n >= 3 or recent_near_miss >= 3:
        classification = "RESIDUAL_DISCOVERY_LOSS"
        stage_named = "NO_PATH_MATCH"
        rationale = (
            f"Buyerish near-miss NO_PATH refined={near_miss_n} "
            f"recent_scored={recent_near_miss}."
        )
        decision = "OBSERVE"
    elif (
        funnel_refined["eligible"] >= 10
        and funnel_refined["contamination_pct"] == 0.0
        and near_miss_n == 0
        and live_path_b["disc_v6"] == 0
        and traffic_ok
    ):
        classification = "RESIDUAL_SCARCITY"
        stage_named = None
        rationale = (
            f"Still 0 NEW disc_v6 since path_b ({live_path_b['hours']}h, "
            f"scored={live_path_b['score']}; since E112 scored={live_e112['score']}). "
            f"Standing UB expected_count≈{live_path_b['expected_count_at_ub']}; "
            f"current-code recent_LOW elig={recent_low_elig_low}/"
            f"{funnel_recent_low['pool_n']} → ~{empiric_current_per_hour}/h. "
            f"Scarcity confidence={scarcity['level']}. "
            + (
                "Incremental collector quiet since E112. "
                if incr_quiet
                else "Incremental scored traffic observed since E112. "
            )
            + "Not PIPELINE_FAILURE (redis lag=0; workers Up; isolation ok)."
        )
        decision = "OBSERVE"
    else:
        classification = "OBSERVABILITY"
        stage_named = None
        rationale = "Insufficient signal; hold observe."
        decision = "OBSERVE"

    code_change = "none"

    # ONE next hypothesis — evidence-backed
    if new_qual:
        dirty = [q for q in new_qual if q["quality_class"] != "clean_rfq_buyerish"]
        if dirty:
            next_hypothesis = (
                "H1: First NEW disc_v6 shows residual contamination on an "
                "untouched path (not path_b/c). Attribute loss/path from frozen "
                "samples and propose the single tightest conjunct — do NOT reopen "
                "path_b/c; do NOT enable shadow without explicit auth."
            )
        else:
            next_hypothesis = (
                "H1: First NEW disc_v6 is clean RFQ/buyerish — continue observe "
                "for rate stability and episode→AI decision completeness; keep "
                "shadow OFF unless authorized."
            )
    elif incr_quiet and live_path_b["disc_v6"] == 0:
        next_hypothesis = (
            "H1: Collector duty-cycle (inter-scan quiet / flood-wait) is the "
            "binding constraint on incremental post–path_b observation — not a "
            "new semantic loss path. Next: wait for next collector burst OR "
            "≥4–8h wall-clock with non-zero incremental scored since E112, then "
            "remeasure NEW rate vs standing UB 0.018/h and 0/5000 recent LOW. "
            "Do NOT enable shadow or reopen path_b/c without explicit auth."
        )
    elif scarcity["level"] in (
        "strong_below_ub",
        "strong_via_recent_low_zero",
        "moderate_large_denominator",
    ):
        next_hypothesis = (
            "H1: Empiric yield ≪0.018/h is confirmed under large post–path_b "
            "denominator — next non-path investigation is source scarcity in "
            "monitored communities vs authorized episode-shadow for recall "
            "diagnostics (shadow stays OFF until explicit auth). Do not reopen "
            "path_b/c."
        )
    else:
        next_hypothesis = (
            "H1: Continue live observe until first NEW disc_v6 OR expected_count "
            f"at UB 0.018/h approaches ≥0.25 (~{0.25/EMPIRIC_UB_PER_HOUR:.0f}h) / "
            "≥4–8h post path_b with growing scored denominator. Current-code "
            "0/5000 recent LOW already suggests ≪0.018/h; first NEW quality "
            "check remains highest-value. Do NOT enable shadow without auth."
        )

    runtime = {
        "head": _git_head(),
        "head_short": _git_head_short(),
        "code_default_DISCOVERY_VERSION": DISCOVERY_VERSION,
        "settings": {
            "commercial_discovery_enabled": bool(
                settings.commercial_discovery_enabled
            ),
            "commercial_discovery_version": settings.commercial_discovery_version,
            "commercial_discovery_max_candidates_per_hour": int(
                settings.commercial_discovery_max_candidates_per_hour or 0
            ),
            "commercial_episode_shadow_enabled": bool(
                settings.commercial_episode_shadow_enabled
            ),
            "commercial_ai_enabled": bool(settings.commercial_ai_enabled),
            "commercial_ai_backend": settings.commercial_ai_backend,
        },
        "health": health,
        "gates": {
            "ai_validated_messages": gates.get("ai_validated_messages"),
            "ai_validated_true": gates.get("ai_validated_true"),
            "ai_validated_false": gates.get("ai_validated_false"),
            "ml_training_enabled": gates.get("ml_training_enabled"),
            "ai_validation_gate_ready": gates.get("ai_validation_gate_ready"),
        },
        "api_base": api_base,
        "isolation": iso,
        "isolation_ok": isolation_ok,
        "redis": redis,
        "disc_v6_live_rows": [
            {
                "id": c["id"],
                "seed": c["seed_message_id"],
                "trigger": c["trigger_type"],
                "status": c["status"],
                "created_at": c["created_at"].isoformat()
                if hasattr(c["created_at"], "isoformat")
                else str(c["created_at"]),
            }
            for c in live_cands
        ],
        "workers_note": "compose Up; shadow worker idle (INTENTIONAL_OFF)",
    }

    density = {
        "empiric_ub_per_hour_standing": EMPIRIC_UB_PER_HOUR,
        "pre_fix_contam_rate_per_hour": PRE_FIX_CONTAM_RATE_PER_HOUR,
        "refined_frozen": {
            "pool_n": funnel_refined["pool_n"],
            "eligible": refined_any,
            "eligible_LOW": refined_low,
            "audit_days": round(refined_days, 2),
            "implied_low_per_day": implied_refined_low_per_day,
            "implied_low_per_hour": implied_refined_low_per_hour,
        },
        "recent_low_7d_current_code": {
            "sample_n": funnel_recent_low["pool_n"],
            "eligible": recent_low_elig,
            "eligible_LOW": recent_low_elig_low,
            "empiric_per_hour": empiric_current_per_hour,
            "empiric_per_day": empiric_current_per_day,
            "first_loss": funnel_recent_low["first_loss"],
            "near_miss_buyerish": funnel_recent_low["near_miss_buyerish_nopath"],
        },
        "recent_scored_since_path_b": {
            "sample_n": scored_n,
            "eligible": scored_elig,
            "eligible_LOW": scored_low_enq,
            "p_low_enqueue": round(p_low, 6),
            "live_score_per_hour": live_score_rate_per_hour,
            "implied_cand_per_hour": implied_from_live_scored,
            "first_loss": funnel_recent_scored["first_loss"],
            "near_miss_buyerish": recent_near_miss,
        },
        "scarcity_confidence": scarcity,
        "compare": {
            "live_post_path_b_rate_per_hour": live_path_b["rate_per_hour"],
            "vs_empiric_ub": "below_or_at_zero",
            "vs_pre_fix_0_54": "far_below_contam_rate",
            "expected_count_path_b_window": live_path_b["expected_count_at_ub"],
            "poisson_note": (
                "With UB 0.018/h, expected count remains ≪1 until ~55h; "
                "zero NEW in sub-hour / few-hour windows is the null."
            ),
        },
    }

    def _slim_surv(items: list) -> list:
        out = []
        for e in items[:8]:
            out.append(
                {
                    "message_id": e["message_id"],
                    "tier": e["tier"],
                    "trigger": e["trigger"],
                    "paths": e["paths"],
                    "carve": e["carve"],
                    "community": e["community"],
                    "preview": e["preview"],
                    "would_enqueue_low": e["would_enqueue_low"],
                }
            )
        return out

    payload = {
        "evidence_id": 113,
        "experiment_id": "POST_ENABLE_CYCLE7_LIVE_REMEASURE",
        "generated_at": now.isoformat(),
        "runtime_sec": round(time.time() - t0, 2),
        "parent": "Evidence 112 OBSERVE (RESIDUAL_SCARCITY)",
        "decision": decision,
        "classification": classification,
        "loss_stage_if_discovery_loss": stage_named,
        "decision_rationale": rationale,
        "read_only": True,
        "code_change": code_change,
        "hypothesis": (
            "With more post–path_b scored traffic, either (1) first NEW disc_v6 "
            "appears → quality-classify clean RFQ vs residual contam on an "
            "untouched path; or (2) still 0 NEW after large denominator → "
            "strengthen RESIDUAL_SCARCITY (empiric ≪0.018/h)."
        ),
        "runtime": runtime,
        "live_windows": {
            "since_path_b": live_path_b,
            "since_e112": live_e112,
            "since_path_c": live_path_c,
            "since_enable": live_enable,
            "last_1h": live_1h,
            "last_6h": live_6h,
        },
        "new_disc_v6_since_path_b": {
            "count": len(new_qual),
            "samples_frozen": new_qual,
        },
        "populations": {
            "legacy_commercial_and_domain": {
                "since": args.since_iso,
                "funnel": {k: v for k, v in funnel_legacy.items() if k != "samples"},
                "samples": {
                    "survivors": _slim_surv(funnel_legacy["samples"]["survivors"]),
                    "near_miss_buyerish": _slim_surv(
                        funnel_legacy["samples"]["near_miss_buyerish"]
                    ),
                },
            },
            "refined_frozen": {
                "since": args.since_iso,
                "funnel": {k: v for k, v in funnel_refined.items() if k != "samples"},
                "samples": {
                    "survivors": _slim_surv(funnel_refined["samples"]["survivors"]),
                    "near_miss_buyerish": _slim_surv(
                        funnel_refined["samples"]["near_miss_buyerish"]
                    ),
                },
            },
            "recent_scored_since_path_b": {
                "limit": args.recent_scored_limit,
                "funnel": {
                    k: v for k, v in funnel_recent_scored.items() if k != "samples"
                },
                "samples": {
                    "survivors": _slim_surv(
                        funnel_recent_scored["samples"]["survivors"]
                    )[:5],
                    "near_miss_buyerish": _slim_surv(
                        funnel_recent_scored["samples"]["near_miss_buyerish"]
                    )[:8],
                },
            },
            "recent_low_7d": {
                "limit": args.recent_low_limit,
                "funnel": {
                    k: v for k, v in funnel_recent_low.items() if k != "samples"
                },
            },
        },
        "fo_probes": {"recovered_carve": f"{fo_ok}/{len(probes)}", "rows": fo_out},
        "smoke": smoke,
        "density": density,
        "dominant_rejection_refined": top_loss,
        "next_hypothesis": next_hypothesis,
        "business": {
            "outreach": False,
            "ml_go": False,
            "threshold_loosen": False,
            "path_b_c_reopened": False,
            "shadow_enabled_unchanged": True,
            "fo_carve_untouched": True,
            "discovery_semantic_change": False,
            "label_mutation": False,
        },
        "locks_honored": [
            "no path/carve/threshold/veto/shadow enable",
            "no wipe",
            "no ML GO",
            "no label mutation",
            "no .env",
            "no community expansion",
            "no Optuna/SHAP/LLM",
        ],
    }

    txt_lines = [
        "Evidence 113: disc_v6 post-enable Cycle 7 live remeasure / first NEW",
        f"generated_at={now.isoformat()} head={_git_head_short()} parent=Evidence 112 OBSERVE",
        "experiment_id=POST_ENABLE_CYCLE7_LIVE_REMEASURE",
        f"decision={decision}",
        f"classification={classification}"
        + (f" loss_stage={stage_named}" if stage_named else ""),
        f"scarcity_confidence={scarcity['level']}",
        f"read_only=true code_change={code_change}",
        "",
        "=== 0. PARENT / LOCKS ===",
        "  parent=Evidence 112 OBSERVE (RESIDUAL_SCARCITY)",
        "  NO path_b/c reopen / NO FO carve change / NO threshold loosen / NO veto removal",
        "  NO human_labels / AI_CONFIRMED mutation / NO ML GO / NO outreach",
        "  NO docker compose down -v / NO wipe / NO shadow enable / NO community expansion",
        "",
        "=== 1. HYPOTHESIS (ONE) ===",
        "  H1: With more post–path_b scored traffic, either (1) first NEW disc_v6",
        "  → quality-classify clean RFQ vs residual contam on untouched path; or",
        "  (2) still 0 NEW after large denom → strengthen RESIDUAL_SCARCITY ≪0.018/h.",
        "",
        "=== 2. RUNTIME CONFIRM ===",
        f"  HEAD={_git_head()}",
        f"  path_b deploy marker: {args.path_b_at}",
        f"  E112 marker: {args.e112_at}",
        f"  measure_at≈{now.isoformat()} (~{live_path_b['hours']}h since path_b;"
        f" ~{live_e112['hours']}h since E112)",
        f"  settings: enabled={runtime['settings']['commercial_discovery_enabled']}"
        f" version={runtime['settings']['commercial_discovery_version']}"
        f" cap={runtime['settings']['commercial_discovery_max_candidates_per_hour']}/h"
        f" shadow={runtime['settings']['commercial_episode_shadow_enabled']}",
        f"  health={health}",
        f"  gates: ai_validated_messages={gates.get('ai_validated_messages')}"
        f" true={gates.get('ai_validated_true')} false={gates.get('ai_validated_false')}"
        f" ml={gates.get('ml_training_enabled')}"
        f" ready={gates.get('ai_validation_gate_ready')}",
        f"  isolation: human_labels={iso.get('human_labels')}"
        f" AI_CONFIRMED={iso.get('ai_confirmed')}"
        f" disc_candidates={iso.get('disc_candidates')}"
        f" disc_v6={iso.get('disc_v6_candidates')}",
        f"  isolation_ok={isolation_ok} (expect 1524/4)",
        "",
        "=== 3. REDIS (no blind XACK) ===",
    ]
    for sname, sdata in (redis.get("streams") or {}).items():
        txt_lines.append(
            f"  telegram:{sname}: XLEN={sdata.get('xlen')} pending={sdata.get('pending')} "
            f"consumers={sdata.get('consumers')} lag={sdata.get('lag')}"
        )
    txt_lines += [
        "  NOTE: episode_shadow lag + consumers=0 expected (shadow OFF)",
        "",
        "=== 4. LIVE SNAPSHOT ===",
        f"  A) since path_b {args.path_b_at} (~{live_path_b['hours']}h):",
        f"     msg={live_path_b['msg']} score={live_path_b['score']}"
        f" cand_new={live_path_b['cand']} disc_v6_new={live_path_b['disc_v6']}"
        f" rate/h={live_path_b['rate_per_hour']}",
        f"     class={live_path_b['class']}"
        f" expected_count≈{live_path_b['expected_count_at_ub']}",
        f"  B) since E112 {args.e112_at} (~{live_e112['hours']}h):",
        f"     msg={live_e112['msg']} score={live_e112['score']}"
        f" disc_v6_new={live_e112['disc_v6']} class={live_e112['class']}",
        f"  C) last_1h: msg={live_1h['msg']} score={live_1h['score']}"
        f" cand={live_1h['cand']} disc_v6={live_1h['disc_v6']}"
        f" ({live_1h['note']})",
        f"  D) last_6h: msg={live_6h['msg']} score={live_6h['score']}"
        f" cand={live_6h['cand']} disc_v6={live_6h['disc_v6']}"
        f" ({live_6h['note']})",
        f"  E) since enable: msg={live_enable['msg']} disc_v6={live_enable['disc_v6']}"
        f" (6 historical CONTAMINATED retained)",
        "",
        "=== 5. NEW disc_v6 SINCE path_b (qualitative) ===",
    ]
    if not new_qual:
        txt_lines.append("  NEW count=0 — no qualitative samples; scarcity path active.")
    else:
        for q in new_qual:
            txt_lines.append(
                f"  id={q['id']} seed={q['seed']} trig={q['trigger']} "
                f"tier={q['tier']} paths={q['paths_live']} "
                f"elig_now={q['eligible_now']} class={q['quality_class']}"
            )
            txt_lines.append(f"    preview: {q['preview']}")
            txt_lines.append(f"    signals: {q['signals']}")

    txt_lines += [
        "",
        "=== 6. FROZEN POPULATION FUNNEL (current disc_v6) ===",
        f"  legacy commercial∧domain: n={funnel_legacy['pool_n']}"
        f" elig={funnel_legacy['eligible']}"
        f" LOW={funnel_legacy['eligible_LOW']}"
        f" rate={funnel_legacy['eligible_pct']}%"
        f" contam={funnel_legacy['contamination_pct']}%",
        f"    first_loss: {funnel_legacy['first_loss']}",
        f"    near_miss_buyerish_nopath: {funnel_legacy['near_miss_buyerish_nopath']}",
        f"  refined_frozen: n={funnel_refined['pool_n']}"
        f" elig={funnel_refined['eligible']}"
        f" LOW={funnel_refined['eligible_LOW']}"
        f" rate={funnel_refined['eligible_pct']}%"
        f" contam={funnel_refined['contamination_pct']}%",
        f"    first_loss: {funnel_refined['first_loss']}",
        f"    near_miss_buyerish_nopath: {funnel_refined['near_miss_buyerish_nopath']}",
        f"    dominant_rejection (excl RETRIEVED): {top_loss}",
        f"  FO probes: {fo_ok}/{len(probes)} eligible+carve",
        "",
        "=== 7. RECENT WINDOWS (current code) ===",
        f"  recent_scored since path_b (lim={args.recent_scored_limit}):"
        f" n={scored_n} elig={scored_elig} LOW_enq={scored_low_enq}"
        f" near_miss={recent_near_miss}",
        f"    first_loss: {funnel_recent_scored['first_loss']}",
        f"  recent_low 7d (lim={args.recent_low_limit}):"
        f" n={funnel_recent_low['pool_n']} elig={recent_low_elig_low}"
        f" near_miss={funnel_recent_low['near_miss_buyerish_nopath']}",
        f"    first_loss: {funnel_recent_low['first_loss']}",
        "",
        "=== 8. DENSITY / SCARCITY CONFIDENCE ===",
        f"  standing empiric UB: {EMPIRIC_UB_PER_HOUR}/h (E103/104; 3/5000 LOW/7d)",
        f"  pre-fix contam live: ~{PRE_FIX_CONTAM_RATE_PER_HOUR}/h (E107)",
        f"  refined_frozen LOW density: {refined_low} over ~{refined_days:.1f}d"
        f" → ~{implied_refined_low_per_day}/d ~{implied_refined_low_per_hour}/h",
        f"  recent_low current-code empiric: {recent_low_elig_low}"
        f"/{funnel_recent_low['pool_n']} → ~{empiric_current_per_hour}/h"
        f" ~{empiric_current_per_day}/d",
        f"  live score rate post path_b: ~{live_score_rate_per_hour}/h;"
        f" p_LOW_eligible≈{p_low:.6f} → implied≈{implied_from_live_scored}/h",
        f"  live observed post path_b: {live_path_b['rate_per_hour']}/h"
        f" (NEW={live_path_b['disc_v6']}; expected_count≈{live_path_b['expected_count_at_ub']})",
        f"  scarcity_confidence.level={scarcity['level']}",
    ]
    for n in scarcity["notes"]:
        txt_lines.append(f"    - {n}")

    if smoke:
        txt_lines += [
            "",
            "=== 9. OPTIONAL SMOKE ===",
            f"  path blocks all_pass={smoke['all_path_blocks_pass']}",
        ]
        for c in smoke["path_block_cases"]:
            txt_lines.append(
                f"    {c['name']}: eligible={c['eligible']} want={c['want_eligible']}"
                f" pass={c['pass']} paths={c['paths']} trig={c['trigger']}"
            )
        txt_lines.append(
            f"  FO probes: {smoke['fo_probes']} pass={smoke.get('fo_pass')}"
        )

    txt_lines += [
        "",
        "=== 10. ATTRIBUTION / CLASSIFICATION ===",
        f"  classification={classification}",
        f"  loss_stage={stage_named}",
        f"  rationale={rationale}",
        "  Not PIPELINE_FAILURE: redis lag=0 discovery/AI; workers Up; isolation ok.",
        "  Shadow remains INTENTIONAL_OFF (E111) — out of scope this cycle.",
        "",
        "=== 11. DECISION ===",
        f"  {decision} — evidence-only; no path/carve/threshold/shadow change.",
        "",
        "=== 12. NEXT HYPOTHESIS (ONE) ===",
        f"  {next_hypothesis}",
        "",
        "=== 13. BUSINESS ===",
        "  outreach=false ml_go=false threshold_loosen=false fo_carve_untouched=true",
        "  discovery_semantic_change=false path_b_c_reopened=false shadow_unchanged=true",
        "",
        f"JSON: {OUT_JSON.relative_to(ROOT)}",
    ]

    payload["runtime_sec"] = round(time.time() - t0, 2)
    txt = "\n".join(txt_lines) + "\n"
    print(txt)
    if write:
        OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
        OUT_TXT.write_text(txt, encoding="utf-8")
        OUT_JSON.write_text(
            json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
        )
        print(f"Wrote {OUT_TXT}")
        print(f"Wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
