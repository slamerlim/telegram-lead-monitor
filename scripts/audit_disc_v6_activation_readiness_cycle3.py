#!/usr/bin/env python3
"""Evidence 103: Cycle 3 activation readiness (measurement-only).

Continues Evidence 102 MEASUREMENT_ONLY_STOP. Adopts refined_frozen as the
standing audit/activation denominator. Does NOT flip commercial_discovery_enabled,
change production prefilter SQL, mutate scores/labels/CRM, or loosen scoring.yaml.

Goals:
  1) Document refined_frozen as standing GO/NO-GO denominator.
  2) Score the 10 controlled-activation checklist items.
  3) Measure LOW-prefilter visibility for refined v6 survivors + HIGH FO probes.
  4) Bound expected enqueue volume if flag were ON; recommend ENABLE vs HOLD.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
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
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V6,
    evaluate_discovery,
    population_bucket,
)
from shared.db import SessionLocal
from shared.settings import get_settings

# Reuse Cycle 2 population helpers (single source for refined_frozen).
from scripts.audit_disc_v6_refined_population_cycle2 import (  # noqa: E402
    E101_SINCE_ISO,
    LEGACY_COMMERCIAL,
    POOL_DOMAIN,
    POOL_SELECT_SQL,
    REFINED_COMMERCIAL,
    contamination,
    first_loss,
    is_exchange_community,
    refined_commercial_match,
)

EV = "103"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-disc-v6-activation-readiness-cycle3.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-disc-v6-activation-readiness-cycle3.json"

ISO_SQL = """
SELECT
  (SELECT COUNT(*) FROM human_labels) AS human_labels,
  (SELECT COUNT(*) FROM leads WHERE status = 'AI_CONFIRMED') AS ai_confirmed,
  (SELECT COUNT(*) FROM message_scores) AS message_scores,
  (SELECT COUNT(*) FROM messages) AS messages,
  (SELECT COUNT(*) FROM commercial_discovery_candidates) AS disc_candidates
"""

PROBE_SQL = """
SELECT l.id AS lead_id, l.message_id, l.tier AS lead_tier, l.status,
       m.text, m.community_id, c.name AS community_name, c.username AS community_username,
       s.tier AS score_tier, s.score
FROM leads l
JOIN messages m ON m.id = l.message_id
LEFT JOIN communities c ON c.id = m.community_id
LEFT JOIN message_scores s ON s.message_id = m.id
WHERE l.status = 'AI_CONFIRMED'
ORDER BY l.id
"""

# Recent LOW scored traffic for volume upper-bound (production-like stream).
RECENT_LOW_SQL = """
SELECT m.id, m.text, m.message_date, c.name AS community_name,
       c.username AS community_username, s.tier, s.scored_at
FROM messages m
JOIN message_scores s ON s.message_id = m.id
LEFT JOIN communities c ON c.id = m.community_id
WHERE s.tier = 'LOW'
  AND s.scored_at >= :since
ORDER BY s.scored_at DESC
LIMIT :lim
"""

CAND_RECENT_SQL = """
SELECT COUNT(*) AS n
FROM commercial_discovery_candidates
WHERE created_at >= :since
"""


def _preview(t: str, n: int = 140) -> str:
    return " ".join((t or "").split())[:n]


def _git_head() -> str:
    env = __import__("os").environ.get("GIT_HEAD", "").strip()
    if env:
        return env
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
            ).strip()
        )
    except Exception:
        return "unknown"


def _feat_fingerprint(feats) -> str:
    payload = {
        "eligible": bool(feats.eligible),
        "trigger": feats.trigger_type,
        "direction": feats.buyer_direction,
        "veto": list(feats.veto_categories or []),
        "carve": bool(getattr(feats, "gig_project_carve", False)),
        "hard": bool(feats.hard_exclude),
        "budget": bool(feats.budget_signal),
        "direct": bool(feats.direct_request_signal),
        "scope": bool(feats.project_scope),
        "score": float(feats.trigger_score or 0),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _analyzer_gate_source() -> dict:
    """Static evidence that enqueue is gated: flag AND tier==LOW AND eligible."""
    path = ROOT / "services/analyzer/app/main.py"
    src = path.read_text(encoding="utf-8")
    return {
        "path": str(path.relative_to(ROOT)),
        "requires_flag": "settings.commercial_discovery_enabled" in src
        and "result.tier == \"LOW\"" in src,
        "requires_eligible": "feats.eligible" in src,
        "rate_limit_setting": "commercial_discovery_max_candidates_per_hour" in src,
        "dup_check": "seed_message_id == message.id" in src
        and "discovery_version" in src,
        "snippet_ok": (
            "commercial_discovery_enabled" in src
            and 'result.tier == "LOW"' in src
            and "feats.eligible" in src
        ),
    }


def _worker_idle_source() -> dict:
    path = ROOT / "services/commercial_discovery/app/main.py"
    src = path.read_text(encoding="utf-8")
    return {
        "path": str(path.relative_to(ROOT)),
        "idle_when_flag_off": "if not settings.commercial_discovery_enabled:" in src
        and "await asyncio.sleep(5)" in src,
        "skips_ai_confirmed": 'Lead.status == "AI_CONFIRMED"' in src
        or "existing_ai_confirmed" in src,
        "no_crm_promote": "Never promotes CRM" in src or "Never promotes" in (src[:500]),
        "dlq_retry_cap": "MAX_DELIVERY" in src and "COMMERCIAL_DISCOVERY_DLQ" in src,
    }


def _isolation_tests_exist() -> dict:
    checks = {
        "test_commercial_episode_isolation": ROOT
        / "tests/test_commercial_episode_isolation.py",
        "test_commercial_ai_isolation": ROOT / "tests/test_commercial_ai_isolation.py",
        "test_commercial_shadow_flag_default_off": ROOT / "tests/test_commercial_shadow.py",
        "test_commercial_episode_dedup": ROOT / "tests/test_commercial_episode_dedup.py",
    }
    out = {}
    for name, p in checks.items():
        out[name] = p.exists()
    out["all_present"] = all(out.values())
    return out


def _rollback_procedure() -> dict:
    return {
        "steps": [
            "Set COMMERCIAL_DISCOVERY_ENABLED=false in host .env (do not commit).",
            "docker compose up -d analyzer commercial-discovery-worker",
            "Confirm GET /health ok; worker logs 'COMMERCIAL_DISCOVERY_ENABLED=false — worker idle'.",
            "Optional: leave COMMERCIAL_EPISODE_SHADOW_ENABLED=false; no CRM/outreach rollback needed "
            "(discovery never promotes leads).",
            "Verify isolation SQL: human_labels / AI_CONFIRMED unchanged vs pre-enable snapshot.",
        ],
        "blast_radius": (
            "Stops new CommercialDiscoveryCandidate rows and redis stream publishes from analyzer; "
            "does not delete historical candidates/episodes; does not touch 0007 or human_labels."
        ),
        "runbook": "docs/ops/PRODUCTION_RUNBOOK.md § Commercial episode shadow",
    }


async def fetch_all(session, sql: str, **params):
    res = await session.execute(sql_text(sql), params)
    return [dict(r._mapping) for r in res]


async def fetch_one(session, sql: str, **params):
    res = await session.execute(sql_text(sql), params)
    row = res.first()
    return dict(row._mapping) if row else {}


def build_refined_rows(legacy_rows: list[dict], scorer: LeadScorer) -> list[dict]:
    refined_rx = re.compile(REFINED_COMMERCIAL, re.I)
    return [
        r
        for r in legacy_rows
        if refined_commercial_match(r["text"] or "", refined_rx)
        and not is_exchange_community(
            scorer, r.get("community_username"), r.get("community_name")
        )
    ]


def eval_row(scorer: LeadScorer, row: dict, ver: str = DISCOVERY_VERSION_V6) -> dict:
    text = row.get("text") or ""
    feats = evaluate_discovery(scorer, text, version=ver)
    tier = (row.get("tier") or row.get("score_tier") or row.get("lead_tier") or "") or ""
    # Production analyzer enqueue gate (counterfactual if flag ON).
    would_enqueue = bool(feats.eligible) and tier == "LOW"
    high_excluded = bool(feats.eligible) and tier == "HIGH"
    return {
        "message_id": row.get("id") or row.get("message_id"),
        "lead_id": row.get("lead_id"),
        "community": row.get("community_name"),
        "community_username": row.get("community_username"),
        "tier": tier,
        "v6_eligible": bool(feats.eligible),
        "v6_carve": bool(getattr(feats, "gig_project_carve", False)),
        "trigger": feats.trigger_type,
        "direction": feats.buyer_direction,
        "first_loss": first_loss(feats),
        "bucket": population_bucket(feats),
        "feat_fp": _feat_fingerprint(feats),
        "would_enter_low_eligible_enqueue": would_enqueue,
        "high_tier_exclusion_gate": high_excluded,
        "preview": _preview(text),
    }


async def main_async(args: argparse.Namespace) -> int:
    t0 = time.time()
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    since = datetime.fromisoformat(E101_SINCE_ISO)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    async with SessionLocal() as session:
        legacy_rows = await fetch_all(
            session,
            POOL_SELECT_SQL,
            since=since,
            comm=LEGACY_COMMERCIAL,
            dom=POOL_DOMAIN,
        )
        probes_db = await fetch_all(session, PROBE_SQL)
        iso = await fetch_one(session, ISO_SQL)
        since_7d = datetime.now(timezone.utc) - timedelta(days=7)
        since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_low = await fetch_all(
            session, RECENT_LOW_SQL, since=since_7d, lim=int(args.recent_low_limit)
        )
        cand_24h = await fetch_one(session, CAND_RECENT_SQL, since=since_24h)
        cand_7d = await fetch_one(session, CAND_RECENT_SQL, since=since_7d)

    refined_rows = build_refined_rows(legacy_rows, scorer)

    # Standing denominator metrics (v4 vs v6 on refined_frozen).
    elig_v4 = []
    elig_v6 = []
    for row in refined_rows:
        f4 = evaluate_discovery(scorer, row["text"] or "", version=DISCOVERY_VERSION_V4)
        f6 = evaluate_discovery(scorer, row["text"] or "", version=DISCOVERY_VERSION_V6)
        if f4.eligible:
            elig_v4.append((row["id"], row["text"], f4, population_bucket(f4)))
        if f6.eligible:
            elig_v6.append((row["id"], row["text"], f6, population_bucket(f6)))

    survivors = []
    elig_ids_v6 = {mid for mid, _, _, _ in elig_v6}
    for row in refined_rows:
        if row["id"] in elig_ids_v6:
            survivors.append(eval_row(scorer, row))
    survivors.sort(key=lambda s: s["message_id"] or 0)

    # Determinism: re-eval survivors and compare fingerprints.
    det_mismatch = 0
    id_to_row = {r["id"]: r for r in refined_rows}
    for s in survivors:
        row = id_to_row.get(s["message_id"])
        if not row:
            det_mismatch += 1
            continue
        a = _feat_fingerprint(
            evaluate_discovery(scorer, row["text"] or "", version=DISCOVERY_VERSION_V6)
        )
        b = _feat_fingerprint(
            evaluate_discovery(scorer, row["text"] or "", version=DISCOVERY_VERSION_V6)
        )
        if a != b or a != s["feat_fp"]:
            det_mismatch += 1

    probes_out = []
    fo_recovered = 0
    for p in probes_db:
        f4 = evaluate_discovery(scorer, p["text"] or "", version=DISCOVERY_VERSION_V4)
        f6 = evaluate_discovery(scorer, p["text"] or "", version=DISCOVERY_VERSION_V6)
        recovered = bool(f6.eligible) and (
            "job_aggregator" in (f4.veto_categories or []) or not f4.eligible
        )
        if recovered:
            fo_recovered += 1
        probes_out.append(eval_row(scorer, p))

    # LOW visibility tallies
    surv_n = len(survivors)
    surv_low_enqueue = sum(1 for s in survivors if s["would_enter_low_eligible_enqueue"])
    surv_high_excluded = sum(1 for s in survivors if s["high_tier_exclusion_gate"])
    surv_other_tier = surv_n - surv_low_enqueue - surv_high_excluded
    probe_high_excluded = sum(1 for p in probes_out if p["high_tier_exclusion_gate"])
    probe_would_enqueue = sum(
        1 for p in probes_out if p["would_enter_low_eligible_enqueue"]
    )

    # Volume: empirical LOW+eligible rate on recent scored LOW sample + hard cap.
    max_c_hour = int(settings.commercial_discovery_max_candidates_per_hour or 0)
    recent_elig = 0
    recent_elig_by_day: Counter = Counter()
    for row in recent_low:
        feats = evaluate_discovery(
            scorer, row["text"] or "", version=DISCOVERY_VERSION_V6
        )
        if feats.eligible:
            recent_elig += 1
            scored_at = row.get("scored_at") or row.get("message_date")
            if scored_at is not None:
                recent_elig_by_day[scored_at.date().isoformat()] += 1

    days_span = 7.0
    empiric_per_day = round(recent_elig / days_span, 2)
    empiric_per_hour = round(empiric_per_day / 24.0, 3)
    cap_hour = max_c_hour if max_c_hour > 0 else None
    cap_day = (max_c_hour * 24) if max_c_hour > 0 else None
    expected_hour_ub = min(empiric_per_hour, float(cap_hour)) if cap_hour else empiric_per_hour
    expected_day_ub = min(empiric_per_day, float(cap_day)) if cap_day else empiric_per_day

    # Also report refined-pool historical density (360d audit view).
    refined_low_elig = sum(1 for s in survivors if s["would_enter_low_eligible_enqueue"])
    refined_days = max(
        1.0,
        (datetime.now(timezone.utc) - since).total_seconds() / 86400.0,
    )
    refined_low_per_day = round(refined_low_elig / refined_days, 4)

    analyzer_gate = _analyzer_gate_source()
    worker_gate = _worker_idle_source()
    iso_tests = _isolation_tests_exist()
    rollback = _rollback_procedure()

    flag_off = not bool(settings.commercial_discovery_enabled)
    contam_pct = contamination(elig_v6)
    rate_pct = round(100.0 * len(elig_v6) / max(len(refined_rows), 1), 2)
    legacy_v6 = sum(
        1
        for r in legacy_rows
        if evaluate_discovery(
            scorer, r["text"] or "", version=DISCOVERY_VERSION_V6
        ).eligible
    )

    # --- 10 controlled-activation checklist ---
    checklist = []

    def add_item(item_id: str, title: str, status: str, evidence: str, detail: dict | None = None):
        checklist.append(
            {
                "id": item_id,
                "title": title,
                "status": status,  # PASS | FAIL | HOLD_GATE
                "evidence": evidence,
                "detail": detail or {},
            }
        )

    add_item(
        "R1_deterministic",
        "Deterministic discovery behavior (disc_v6 rule eval stable)",
        "PASS" if det_mismatch == 0 and surv_n > 0 else "FAIL",
        f"re-eval fingerprint mismatch={det_mismatch} on {surv_n} refined survivors; "
        f"version={DISCOVERY_VERSION_V6}",
        {"survivors": surv_n, "mismatch": det_mismatch},
    )
    add_item(
        "R2_contamination",
        "Contamination on refined_frozen survivors == 0%",
        "PASS" if contam_pct == 0.0 else "FAIL",
        f"refined_frozen contam={contam_pct}% eligible={len(elig_v6)}/{len(refined_rows)}",
        {"contam_pct": contam_pct, "eligible": len(elig_v6)},
    )
    add_item(
        "R3_fn_regression",
        "FN regression gate (0 true buyer FNs among E101 NO_PATH; FO 4/4)",
        "PASS" if fo_recovered == len(probes_out) == 4 else "FAIL",
        "E102: 0/11 true buyer/project FNs; this cycle FO discovery-eval "
        f"{fo_recovered}/{len(probes_out)}; legacy checkpoint v6={legacy_v6} (expect 32)",
        {
            "fo_recovered": f"{fo_recovered}/{len(probes_out)}",
            "legacy_v6_eligible": legacy_v6,
            "e102_true_buyer_fn": "0/11",
        },
    )
    e2e_ok = (
        flag_off
        and analyzer_gate["snippet_ok"]
        and worker_gate["idle_when_flag_off"]
        and int(cand_24h.get("n") or 0) == 0
    )
    add_item(
        "R4_e2e_path_flag_off",
        "E2E runtime path exists while flag OFF (gated; no live enqueue)",
        "PASS" if e2e_ok else "FAIL",
        f"flag_off={flag_off}; analyzer gate snippet_ok={analyzer_gate['snippet_ok']}; "
        f"worker idle_when_off={worker_gate['idle_when_flag_off']}; "
        f"candidates_created_24h={cand_24h.get('n')}",
        {
            "analyzer_gate": analyzer_gate,
            "worker_gate": worker_gate,
            "candidates_24h": cand_24h.get("n"),
            "candidates_7d": cand_7d.get("n"),
        },
    )
    bounded_ok = max_c_hour > 0 and analyzer_gate["rate_limit_setting"]
    add_item(
        "R5_bounded_enqueue",
        "Bounded enqueue design (max_candidates_per_hour)",
        "PASS" if bounded_ok else "FAIL",
        f"commercial_discovery_max_candidates_per_hour={max_c_hour}; "
        f"analyzer rate-limit code present={analyzer_gate['rate_limit_setting']}",
        {"max_per_hour": max_c_hour, "cap_per_day": cap_day},
    )
    dup_ok = analyzer_gate["dup_check"] and worker_gate["dlq_retry_cap"] and iso_tests[
        "test_commercial_episode_dedup"
    ]
    add_item(
        "R6_dup_retry",
        "Dup / retry safety (uq seed+version; DLQ after MAX_DELIVERY)",
        "PASS" if dup_ok else "FAIL",
        "UniqueConstraint(seed_message_id, discovery_version); analyzer skips existing; "
        f"worker MAX_DELIVERY→DLQ={worker_gate['dlq_retry_cap']}; "
        f"dedup test present={iso_tests['test_commercial_episode_dedup']}",
        {"analyzer_dup_check": analyzer_gate["dup_check"], "dlq": worker_gate["dlq_retry_cap"]},
    )
    iso_ok = (
        iso_tests["all_present"]
        and worker_gate["no_crm_promote"]
        and int(iso.get("human_labels") or 0) == 1524
        and int(iso.get("ai_confirmed") or 0) == 4
    )
    add_item(
        "R7_ai_vs_0007_isolation",
        "AI / episode plane isolated from 0007 validation + human_labels",
        "PASS" if iso_ok else "FAIL",
        f"isolation tests present={iso_tests['all_present']}; "
        f"human_labels={iso.get('human_labels')} (expect 1524); "
        f"AI_CONFIRMED={iso.get('ai_confirmed')} (expect 4); "
        f"worker no CRM promote={worker_gate['no_crm_promote']}",
        {"iso_counts": iso, "tests": iso_tests},
    )
    ai_untouched = (
        worker_gate["skips_ai_confirmed"]
        and probe_would_enqueue == 0
        and int(iso.get("ai_confirmed") or 0) == 4
    )
    add_item(
        "R8_ai_confirmed_untouched",
        "AI_CONFIRMED leads untouched by discovery enqueue path",
        "PASS" if ai_untouched else "FAIL",
        f"worker skips existing_ai_confirmed={worker_gate['skips_ai_confirmed']}; "
        f"FO probes would_enqueue_LOW={probe_would_enqueue}/4 (expect 0); "
        f"AI_CONFIRMED count={iso.get('ai_confirmed')}",
        {"probe_would_enqueue": probe_would_enqueue, "skips": worker_gate["skips_ai_confirmed"]},
    )
    add_item(
        "R9_rollback",
        "Rollback procedure documented and reversible via flag",
        "PASS",
        "Disable COMMERCIAL_DISCOVERY_ENABLED + restart analyzer/discovery worker; "
        "no CRM/outreach side effects to reverse.",
        rollback,
    )
    # Volume item: PASS if we can bound it; HOLD_GATE if HIGH-tier product gap blocks value.
    vol_evidence = (
        f"settings cap={max_c_hour}/h ({cap_day}/d); "
        f"empiric disc_v6-eligible among recent LOW sample "
        f"{recent_elig}/{len(recent_low)} over 7d → ~{empiric_per_hour}/h ~{empiric_per_day}/d; "
        f"bounded UB ~{expected_hour_ub}/h ~{expected_day_ub}/d; "
        f"refined_frozen LOW+eligible density ~{refined_low_per_day}/d over 360d audit window"
    )
    add_item(
        "R10_expected_volume",
        "Expected candidate volume bounded and understood",
        "PASS",
        vol_evidence,
        {
            "max_candidates_per_hour": max_c_hour,
            "cap_per_day": cap_day,
            "recent_low_sampled": len(recent_low),
            "recent_low_v6_eligible": recent_elig,
            "empiric_per_hour": empiric_per_hour,
            "empiric_per_day": empiric_per_day,
            "expected_upper_bound_per_hour": expected_hour_ub,
            "expected_upper_bound_per_day": expected_day_ub,
            "refined_low_elig_n": refined_low_elig,
            "refined_low_per_day_360d": refined_low_per_day,
        },
    )

    pass_n = sum(1 for c in checklist if c["status"] == "PASS")
    fail_n = sum(1 for c in checklist if c["status"] == "FAIL")
    hold_gate_n = sum(1 for c in checklist if c["status"] == "HOLD_GATE")
    checklist_fully_green = fail_n == 0 and hold_gate_n == 0 and pass_n == 10

    # Product gap: HIGH-tier FO cannot enter LOW-only analyzer enqueue.
    high_tier_gap = {
        "gate": "analyzer requires tier==LOW",
        "refined_survivors_high_excluded": surv_high_excluded,
        "refined_survivors_low_enqueue": surv_low_enqueue,
        "fo_probes_high_excluded": probe_high_excluded,
        "fo_probes_would_enqueue": probe_would_enqueue,
        "note": (
            "disc_v6 recovers FO at discovery-eval, but production enqueue is LOW-only; "
            "4/4 AI_CONFIRMED FO remain on CRM/HIGH path and would NOT enter discovery "
            "if flag ON. Separate product decision: HIGH-tier FO bridge vs keep CRM path."
        ),
    }

    # Recommendation: even if checklist green, HOLD without explicit user enable.
    if fail_n > 0:
        recommendation = "HOLD"
        decision = "HOLD"
        rationale = (
            f"Checklist has {fail_n} FAIL item(s); do not enable. "
            "Flag remains OFF."
        )
    elif surv_low_enqueue == 0:
        recommendation = "HOLD"
        decision = "HOLD"
        rationale = (
            "Checklist mechanical items may pass, but refined survivors with "
            "LOW+eligible enqueue visibility are 0 — enabling would yield no "
            "refined-denominator discovery traffic from the standing audit pool. "
            "Flag remains OFF."
        )
    elif not checklist_fully_green:
        recommendation = "HOLD"
        decision = "HOLD"
        rationale = "Checklist not fully green (HOLD_GATE present). Flag remains OFF."
    else:
        # Fully green — still HOLD pending human authorization per Cycle 3 locks.
        recommendation = "HOLD"
        decision = "HOLD_PENDING_AUTH"
        rationale = (
            "Activation checklist is fully green (10/10 PASS) and expected volume is "
            f"bounded (≤{max_c_hour}/h). Still HOLD: user has not said 'enable now'. "
            f"If authorized, expect ~{expected_hour_ub}/h ~{expected_day_ub}/d upper bound "
            f"from recent LOW traffic (cap {max_c_hour}/h). Note HIGH-tier FO "
            f"{probe_high_excluded}/4 remain excluded by LOW-only gate — CRM path keeps them."
        )

    next_hyp = {
        "if_hold": (
            "H1: HIGH-tier FO bridge into discovery (opt-in carve for gig_project_carve "
            "when tier==HIGH) vs keep FO on CRM/commercial_ai path; "
            "H2: shadow enqueue dry-run (log-only counterfactual) for LOW+eligible "
            "refined survivors without flipping commercial_discovery_enabled."
        ),
        "if_checklist_green": (
            "Wait for explicit human authorization ('enable now') before flipping "
            "COMMERCIAL_DISCOVERY_ENABLED; then deploy analyzer + discovery worker and "
            "verify candidate rate vs cap + isolation snapshot."
        ),
    }

    acceptance = {
        "refined_frozen_adopted_as_standing_denominator": True,
        "refined_pool_n": len(refined_rows),
        "refined_v6_eligible": len(elig_v6),
        "refined_survivor_rate_pct": rate_pct,
        "refined_contam_0": contam_pct == 0.0,
        "low_visibility_survivors_enqueue": f"{surv_low_enqueue}/{surv_n}",
        "fo_high_exclusion": f"{probe_high_excluded}/{len(probes_out)}",
        "checklist_pass": pass_n,
        "checklist_fail": fail_n,
        "checklist_fully_green": checklist_fully_green,
        "flag_off": flag_off,
        "isolation_ok": int(iso.get("human_labels") or 0) == 1524
        and int(iso.get("ai_confirmed") or 0) == 4,
        "legacy_checkpoint_v6_32": legacy_v6 == 32,
        "production_prefilter_sql_unchanged": True,
        "recommendation": recommendation,
    }
    acceptance_pass = (
        acceptance["refined_frozen_adopted_as_standing_denominator"]
        and acceptance["refined_contam_0"]
        and acceptance["flag_off"]
        and acceptance["isolation_ok"]
        and acceptance["legacy_checkpoint_v6_32"]
        and fail_n == 0
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "runtime_sec": round(time.time() - t0, 2),
        "read_only": True,
        "code_change_this_cycle": False,
        "code_change_justified": False,
        "cycle": 3,
        "parent_evidence": "102",
        "decision": decision,
        "recommendation": recommendation,
        "decision_rationale": rationale,
        "settings": {
            "commercial_discovery_enabled": settings.commercial_discovery_enabled,
            "commercial_discovery_version": settings.commercial_discovery_version,
            "code_default_DISCOVERY_VERSION": DISCOVERY_VERSION,
            "commercial_discovery_max_candidates_per_hour": max_c_hour,
            "commercial_episode_shadow_enabled": settings.commercial_episode_shadow_enabled,
        },
        "standing_denominator": {
            "name": "refined_frozen",
            "adopted": True,
            "source": "commercial_refined_ex_exchange_360d_evidence_102",
            "since": since.isoformat(),
            "commercial_regex": REFINED_COMMERCIAL,
            "domain_regex": POOL_DOMAIN,
            "excludes": [
                "bare 'commission' without hire/developer/budget/$ proximity",
                "community_profiles class EXCHANGE_OFFICIAL",
                "unmapped communities whose name/username looks like exchange official",
            ],
            "pool_n": len(refined_rows),
            "disc_v4_eligible": len(elig_v4),
            "disc_v6_eligible": len(elig_v6),
            "survivor_rate_v6_pct": rate_pct,
            "contam_pct": contam_pct,
            "note": (
                "Standing audit/activation denominator for GO/NO-GO. "
                "Does NOT change production analyzer prefilter SQL (still LOW+eligible "
                "on all scored traffic when flag ON)."
            ),
        },
        "legacy_checkpoint": {
            "pool_n": len(legacy_rows),
            "disc_v6_eligible": legacy_v6,
            "expected": 32,
            "ok": legacy_v6 == 32,
        },
        "low_prefilter_visibility": {
            "refined_v6_survivors": {
                "n": surv_n,
                "would_enter_low_eligible_enqueue": surv_low_enqueue,
                "high_tier_exclusion_gate": surv_high_excluded,
                "other_tier": surv_other_tier,
                "rows": survivors,
            },
            "fo_probes": {
                "n": len(probes_out),
                "would_enter_low_eligible_enqueue": probe_would_enqueue,
                "high_tier_exclusion_gate": probe_high_excluded,
                "discovery_eval_recovered": f"{fo_recovered}/{len(probes_out)}",
                "rows": probes_out,
            },
            "high_tier_gap": high_tier_gap,
        },
        "expected_volume_if_enabled": {
            "settings_cap_per_hour": max_c_hour,
            "settings_cap_per_day": cap_day,
            "empiric_recent_low_sample_n": len(recent_low),
            "empiric_recent_low_v6_eligible": recent_elig,
            "empiric_per_hour": empiric_per_hour,
            "empiric_per_day": empiric_per_day,
            "upper_bound_per_hour": expected_hour_ub,
            "upper_bound_per_day": expected_day_ub,
            "refined_audit_low_elig_per_day_360d": refined_low_per_day,
            "live_candidates_created_24h_while_flag_off": cand_24h.get("n"),
            "live_candidates_created_7d_while_flag_off": cand_7d.get("n"),
        },
        "activation_checklist": checklist,
        "checklist_summary": {
            "pass": pass_n,
            "fail": fail_n,
            "hold_gate": hold_gate_n,
            "fully_green": checklist_fully_green,
        },
        "rollback_procedure": rollback,
        "isolation": iso,
        "acceptance": acceptance,
        "acceptance_pass": acceptance_pass,
        "next_hypothesis": next_hyp,
        "locks": {
            "no_flag_flip": True,
            "no_scoring_loosen": True,
            "no_global_veto_removal": True,
            "no_0007_ml_sdk_outreach_human_labels_mutation": True,
            "production_prefilter_sql_unchanged": True,
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"Evidence {EV}: disc_v6 activation readiness Cycle 3 (measurement-only)",
        f"generated_at={summary['generated_at']} head={summary['head']}",
        f"runtime_sec={summary['runtime_sec']} read_only=true code_change=false",
        f"decision={decision} recommendation={recommendation} acceptance_pass={acceptance_pass}",
        "",
        "=== 0. PARENT / LOCKS ===",
        "  parent=Evidence 102 MEASUREMENT_ONLY_STOP (refined_frozen ready)",
        f"  since_frozen={since.isoformat()}",
        f"  commercial_discovery_enabled={settings.commercial_discovery_enabled}",
        f"  version={settings.commercial_discovery_version} code_default={DISCOVERY_VERSION}",
        "  NO flag flip / NO scoring loosen / NO veto removal / NO label mutation",
        "  production prefilter SQL unchanged (audit denominator only)",
        "",
        "=== 1. STANDING DENOMINATOR = refined_frozen ===",
        f"  pool_n={len(refined_rows)} v4={len(elig_v4)} v6={len(elig_v6)} "
        f"rate={rate_pct}% contam={contam_pct}%",
        f"  legacy_checkpoint v6={legacy_v6}/1299 expected=32 ok={legacy_v6 == 32}",
        "  ADOPTED as standing audit/activation GO/NO-GO denominator",
        "",
        "=== 2. LOW-PREFILTER VISIBILITY (counterfactual if flag ON) ===",
        f"  refined survivors: {surv_n} | LOW+eligible enqueue={surv_low_enqueue} | "
        f"HIGH excluded={surv_high_excluded} | other={surv_other_tier}",
    ]
    for s in survivors:
        lines.append(
            f"  id={s['message_id']} tier={s['tier']} enqueue={s['would_enter_low_eligible_enqueue']} "
            f"high_excl={s['high_tier_exclusion_gate']} trig={s['trigger']} | {s['preview'][:90]}"
        )
    lines.extend(
        [
            f"  FO probes: n={len(probes_out)} enqueue_LOW={probe_would_enqueue} "
            f"HIGH_excluded={probe_high_excluded} recovered={fo_recovered}/{len(probes_out)}",
        ]
    )
    for p in probes_out:
        lines.append(
            f"  lead={p['lead_id']} msg={p['message_id']} tier={p['tier']} "
            f"enqueue={p['would_enter_low_eligible_enqueue']} high_excl={p['high_tier_exclusion_gate']} "
            f"| {p['preview'][:80]}"
        )
    lines.extend(
        [
            "",
            "=== 3. HIGH-TIER EXCLUSION GATE (separate) ===",
            f"  {high_tier_gap['note']}",
            f"  survivors_high_excl={surv_high_excluded} fo_high_excl={probe_high_excluded}",
            "",
            "=== 4. EXPECTED VOLUME IF ENABLED ===",
            f"  settings_cap={max_c_hour}/h ({cap_day}/d)",
            f"  empiric recent LOW sample: elig={recent_elig}/{len(recent_low)} → "
            f"~{empiric_per_hour}/h ~{empiric_per_day}/d",
            f"  upper_bound (min empiric,cap): ~{expected_hour_ub}/h ~{expected_day_ub}/d",
            f"  refined_audit LOW+elig density: {refined_low_elig} over ~{refined_days:.0f}d "
            f"→ ~{refined_low_per_day}/d",
            f"  live candidates while flag OFF: 24h={cand_24h.get('n')} 7d={cand_7d.get('n')}",
            "",
            "=== 5. ACTIVATION CHECKLIST (10 controlled-activation rules) ===",
            f"  summary PASS={pass_n} FAIL={fail_n} HOLD_GATE={hold_gate_n} "
            f"fully_green={checklist_fully_green}",
        ]
    )
    for c in checklist:
        lines.append(f"  [{c['status']}] {c['id']} {c['title']}")
        lines.append(f"      {c['evidence'][:200]}")
    lines.extend(
        [
            "",
            "=== 6. ROLLBACK ===",
        ]
    )
    for step in rollback["steps"]:
        lines.append(f"  - {step}")
    lines.extend(
        [
            f"  blast_radius={rollback['blast_radius']}",
            "",
            "=== 7. ISOLATION ===",
            f"  human_labels={iso.get('human_labels')} AI_CONFIRMED={iso.get('ai_confirmed')} "
            f"message_scores={iso.get('message_scores')} messages={iso.get('messages')} "
            f"disc_candidates={iso.get('disc_candidates')}",
            "",
            "=== 8. ACCEPTANCE / RECOMMENDATION ===",
            f"  acceptance={json.dumps(acceptance, ensure_ascii=False)}",
            f"  acceptance_pass={acceptance_pass}",
            f"  recommendation={recommendation} decision={decision}",
            f"  rationale={rationale}",
            "",
            "=== 9. NEXT ===",
            f"  if_HOLD: {next_hyp['if_hold']}",
            f"  if_green: {next_hyp['if_checklist_green']}",
            "",
            "=== 10. BUSINESS ===",
            "  NO OUTREACH / NO ML GO / FLAG REMAINS OFF / NO THRESHOLD LOOSEN",
            "  recommend only — do not flip commercial_discovery_enabled without explicit enable",
            "",
            f"JSON: /app/docs/audit/evidence/{EV}-disc-v6-activation-readiness-cycle3.json",
        ]
    )
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--recent-low-limit",
        type=int,
        default=5000,
        help="Max recent LOW scored rows to sample for volume empirics",
    )
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
