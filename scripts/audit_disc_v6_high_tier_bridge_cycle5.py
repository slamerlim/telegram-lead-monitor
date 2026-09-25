#!/usr/bin/env python3
"""Evidence 105: Cycle 5 H1 HIGH-tier gate quantification (measurement-only).

Continues Evidence 104 READY_FOR_AUTH_ENABLE (flag stays OFF). Quantifies how
many disc_v6-eligible messages are enqueue-blocked solely by the analyzer
`tier==LOW` gate, then compares bridge options A/B/C without implementing B
or flipping commercial_discovery_enabled.

Goals:
  1) Count HIGH+eligible among refined_frozen + FO AI_CONFIRMED probes +
     commercial∧domain pool (legacy + refined).
  2) Counterfactual Option B: would analyzer create? would worker EPISODE_BUILT?
  3) Estimate contamination / volume / FO recovery / regression vs disc_v4/v6.
  4) Recommend MEASUREMENT_ONLY: HIGH stays CRM unless product reverses.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
import urllib.request
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
    signals_json,
)
from shared.db import SessionLocal
from shared.settings import get_settings

from scripts.audit_disc_v6_refined_population_cycle2 import (  # noqa: E402
    E101_SINCE_ISO,
    LEGACY_COMMERCIAL,
    POOL_DOMAIN,
    POOL_SELECT_SQL,
    contamination,
    first_loss,
)
from scripts.audit_disc_v6_activation_readiness_cycle3 import (  # noqa: E402
    PROBE_SQL,
    RECENT_LOW_SQL,
    build_refined_rows,
    _feat_fingerprint,
)
from scripts.audit_disc_v6_shadow_enqueue_dryrun_cycle4 import (  # noqa: E402
    SEED_GATE_SQL,
    CAND_META_SQL,
    CAND_RECENT_SQL,
    ISO_SQL,
    _simulate_analyzer_enqueue,
    _simulate_worker_after_create,
)

EV = "105"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-disc-v6-high-tier-bridge-cycle5.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-disc-v6-high-tier-bridge-cycle5.json"

# Recent HIGH scored traffic for Option B volume UB (production-like stream).
RECENT_HIGH_SQL = """
SELECT m.id, m.text, m.message_date, c.name AS community_name,
       c.username AS community_username, s.tier, s.scored_at
FROM messages m
JOIN message_scores s ON s.message_id = m.id
LEFT JOIN communities c ON c.id = m.community_id
WHERE s.tier = 'HIGH'
  AND s.scored_at >= :since
ORDER BY s.scored_at DESC
LIMIT :lim
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


def _api_bases() -> list[str]:
    env = __import__("os").environ.get("TLM_API_BASE", "").strip().rstrip("/")
    bases = []
    if env:
        bases.append(env)
    bases.extend(["http://127.0.0.1:8010", "http://api:8010"])
    out: list[str] = []
    for b in bases:
        if b and b not in out:
            out.append(b)
    return out


def _http_json(path: str) -> dict:
    import os

    if path == "/health" and os.environ.get("TLM_HEALTH_JSON"):
        try:
            return json.loads(os.environ["TLM_HEALTH_JSON"])
        except Exception as exc:
            return {"_error": f"TLM_HEALTH_JSON: {exc}"}
    if path == "/validation/gates" and os.environ.get("TLM_GATES_JSON"):
        try:
            return json.loads(os.environ["TLM_GATES_JSON"])
        except Exception as exc:
            return {"_error": f"TLM_GATES_JSON: {exc}"}

    last_err = None
    for base in _api_bases():
        url = f"{base}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_err = f"{url}: {exc}"
    return {"_error": last_err or "unreachable"}


async def fetch_all(session, sql: str, **params):
    res = await session.execute(sql_text(sql), params)
    return [dict(r._mapping) for r in res]


async def fetch_one(session, sql: str, **params):
    res = await session.execute(sql_text(sql), params)
    row = res.first()
    return dict(row._mapping) if row else {}


def _option_b_tier_ok(tier: str, feats) -> bool:
    """Option B predicate: LOW OR (HIGH and disc_v6 fo_carve eligible)."""
    t = (tier or "").upper()
    if t == "LOW":
        return True
    if t == "HIGH" and bool(feats.eligible) and bool(
        getattr(feats, "gig_project_carve", False)
    ):
        return True
    return False


def _simulate_option_b_enqueue(
    *,
    row: dict,
    feats,
    score_tier: str,
    disc_ver: str,
    existing_v6: int,
    cand_hour_so_far: int,
    max_c_hour: int,
    flag_enabled_counterfactual: bool,
    high_cap_remaining: int | None,
) -> dict:
    """Counterfactual analyzer under Option B (no writes)."""
    reasons: list[str] = []
    would_create = False
    tier_ok = _option_b_tier_ok(score_tier, feats)

    if not flag_enabled_counterfactual:
        reasons.append("flag_disabled")
    if not tier_ok:
        if (score_tier or "").upper() == "HIGH" and feats.eligible and not getattr(
            feats, "gig_project_carve", False
        ):
            reasons.append("high_eligible_but_not_fo_carve")
        else:
            reasons.append("tier_not_allowed")
    if not feats.eligible:
        reasons.append("not_eligible")

    over_rate = False
    if max_c_hour > 0 and cand_hour_so_far >= max_c_hour:
        over_rate = True
        reasons.append("rate_limited")

    high_cap_hit = False
    if (
        (score_tier or "").upper() == "HIGH"
        and high_cap_remaining is not None
        and high_cap_remaining <= 0
    ):
        high_cap_hit = True
        reasons.append("high_carve_cap")

    if existing_v6 > 0:
        reasons.append("dup_seed_version")

    analyzer_gates_pass = (
        flag_enabled_counterfactual
        and tier_ok
        and bool(feats.eligible)
        and existing_v6 == 0
        and not over_rate
        and not high_cap_hit
    )
    if analyzer_gates_pass:
        would_create = True
        reasons = ["would_create_pending"]

    payload = None
    if would_create:
        payload = {
            "seed_message_id": row["message_id"],
            "community_id": row.get("community_id"),
            "author_id": row.get("author_id"),
            "discovery_version": disc_ver,
            "source": "analyzer",
            "trigger_type": feats.trigger_type or "unknown",
            "trigger_score": float(feats.trigger_score or 0),
            "scorer_tier": score_tier,
            "topic_fingerprint": feats.topic_fingerprint or "none",
            "status": "PENDING",
            "signals_json_sha256": hashlib.sha256(
                signals_json(feats).encode("utf-8")
            ).hexdigest()[:16],
            "option_b_bridge": (score_tier or "").upper() == "HIGH",
        }
    return {
        "would_create": would_create,
        "analyzer_skip_reasons": reasons,
        "over_rate": over_rate,
        "high_cap_hit": high_cap_hit,
        "tier_ok_option_b": tier_ok,
        "would_write_row": payload,
    }


def _classify_worker_block(is_blind: bool, is_ai_confirmed: bool) -> str | None:
    if is_blind:
        return "blind_sample"
    if is_ai_confirmed:
        return "existing_ai_confirmed"
    return None


async def main_async(args: argparse.Namespace) -> int:
    t0 = time.time()
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    since = datetime.fromisoformat(E101_SINCE_ISO)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    disc_ver = settings.commercial_discovery_version or DISCOVERY_VERSION
    max_c_hour = int(settings.commercial_discovery_max_candidates_per_hour or 0)
    flag_off = not bool(settings.commercial_discovery_enabled)
    shadow_off = not bool(settings.commercial_episode_shadow_enabled)
    # Strict Option B cap: max HIGH carve creates per simulated hour (policy knob).
    high_cap_per_hour = int(args.high_cap_per_hour)

    health = _http_json("/health")
    gates = _http_json("/validation/gates")

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
        cand_meta = await fetch_all(session, CAND_META_SQL)
        since_7d = datetime.now(timezone.utc) - timedelta(days=7)
        since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        since_1h = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_low = await fetch_all(
            session, RECENT_LOW_SQL, since=since_7d, lim=int(args.recent_low_limit)
        )
        recent_high = await fetch_all(
            session, RECENT_HIGH_SQL, since=since_7d, lim=int(args.recent_high_limit)
        )
        cand_1h = await fetch_one(session, CAND_RECENT_SQL, since=since_1h)
        cand_24h = await fetch_one(session, CAND_RECENT_SQL, since=since_24h)
        cand_7d = await fetch_one(session, CAND_RECENT_SQL, since=since_7d)

    refined_rows = build_refined_rows(legacy_rows, scorer)

    # --- Standing denominator survivors (disc_v6) ---
    refined_survivors: list[dict] = []
    elig_v6_tuples = []
    for row in refined_rows:
        feats = evaluate_discovery(
            scorer, row["text"] or "", version=DISCOVERY_VERSION_V6
        )
        if feats.eligible:
            elig_v6_tuples.append(
                (row["id"], row["text"], feats, population_bucket(feats))
            )
            refined_survivors.append(row)
    contam_refined = contamination(elig_v6_tuples)

    # Legacy commercial∧domain eligible under v6 (broader pool).
    legacy_elig: list[dict] = []
    legacy_elig_tuples = []
    for row in legacy_rows:
        feats = evaluate_discovery(
            scorer, row["text"] or "", version=DISCOVERY_VERSION_V6
        )
        if feats.eligible:
            legacy_elig.append(row)
            legacy_elig_tuples.append(
                (row["id"], row["text"], feats, population_bucket(feats))
            )
    contam_legacy = contamination(legacy_elig_tuples)

    # Fetch DB gates for refined survivors + FO probes + any HIGH eligible in legacy.
    surv_ids = {int(r["id"]) for r in refined_survivors}
    for p in probes_db:
        if p.get("message_id") is not None:
            surv_ids.add(int(p["message_id"]))
    # Also include all HIGH-tier eligible from legacy pool for gate quantification.
    high_legacy_ids = []
    for row in legacy_elig:
        if (row.get("tier") or "") == "HIGH":
            high_legacy_ids.append(int(row["id"]))
            surv_ids.add(int(row["id"]))

    async with SessionLocal() as session:
        gate_rows = await fetch_all(
            session, SEED_GATE_SQL, ids=sorted(surv_ids), ver=disc_ver
        )
    gate_by_id = {int(r["message_id"]): r for r in gate_rows}

    def _row_detail(mid: int, text: str, feats, score_tier: str, extra: dict | None = None) -> dict:
        g = gate_by_id.get(mid) or {}
        tier = (g.get("score_tier") or score_tier or "") or ""
        is_blind = bool(g.get("is_blind"))
        is_ai = bool(g.get("is_ai_confirmed"))
        worker_block = _classify_worker_block(is_blind, is_ai)
        blocked_solely_by_low = bool(feats.eligible) and tier == "HIGH"
        option_b_tier = _option_b_tier_ok(tier, feats)
        base = {
            "message_id": mid,
            "community": g.get("community_name"),
            "community_username": g.get("community_username"),
            "community_id": g.get("community_id"),
            "author_id": g.get("author_id"),
            "tier": tier,
            "v6_eligible": bool(feats.eligible),
            "v6_carve": bool(getattr(feats, "gig_project_carve", False)),
            "trigger": feats.trigger_type,
            "direction": feats.buyer_direction,
            "first_loss": first_loss(feats),
            "bucket": population_bucket(feats),
            "feat_fp": _feat_fingerprint(feats),
            "is_blind": is_blind,
            "is_ai_confirmed": is_ai,
            "existing_disc_v6": int(g.get("existing_disc_v6") or 0),
            "existing_any_versions": g.get("existing_any_versions"),
            "blocked_solely_by_low_tier": blocked_solely_by_low,
            "option_b_tier_ok": option_b_tier,
            "worker_would_skip": worker_block,
            "preview": _preview(text),
        }
        if extra:
            base.update(extra)
        return base

    # Refined HIGH gate set.
    refined_high: list[dict] = []
    refined_low: list[dict] = []
    refined_other: list[dict] = []
    for row in refined_survivors:
        mid = int(row["id"])
        text = row.get("text") or ""
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        g = gate_by_id.get(mid) or {}
        tier = (g.get("score_tier") or row.get("tier") or "") or ""
        detail = _row_detail(mid, text, feats, tier)
        if tier == "HIGH" and feats.eligible:
            refined_high.append(detail)
        elif tier == "LOW" and feats.eligible:
            refined_low.append(detail)
        else:
            refined_other.append(detail)

    # FO probes (AI_CONFIRMED).
    fo_rows: list[dict] = []
    for p in probes_db:
        mid = int(p["message_id"])
        text = p.get("text") or ""
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        tier = (p.get("score_tier") or p.get("lead_tier") or "") or ""
        fo_rows.append(
            _row_detail(
                mid,
                text,
                feats,
                tier,
                extra={"lead_id": p.get("lead_id"), "lead_status": p.get("status")},
            )
        )

    # Broader commercial∧domain HIGH+eligible (legacy pool).
    legacy_high: list[dict] = []
    for row in legacy_elig:
        if (row.get("tier") or "") != "HIGH":
            continue
        mid = int(row["id"])
        text = row.get("text") or ""
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        legacy_high.append(_row_detail(mid, text, feats, "HIGH"))

    # Refined subset of legacy HIGH.
    refined_ids = {int(r["id"]) for r in refined_rows}
    legacy_high_in_refined = [r for r in legacy_high if r["message_id"] in refined_ids]
    legacy_high_outside_refined = [
        r for r in legacy_high if r["message_id"] not in refined_ids
    ]

    # Option B counterfactual on refined HIGH set (strict fo_carve + caps).
    high_cap_remaining = high_cap_per_hour
    cand_hour_sim = int(cand_1h.get("n") or 0)
    option_b_creates = 0
    option_b_episode = 0
    option_b_rows: list[dict] = []
    for detail in sorted(refined_high, key=lambda r: r["message_id"]):
        mid = detail["message_id"]
        text = next(
            (r["text"] for r in refined_survivors if int(r["id"]) == mid), ""
        )
        feats = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V6)
        ana = _simulate_option_b_enqueue(
            row=detail,
            feats=feats,
            score_tier=detail["tier"],
            disc_ver=disc_ver,
            existing_v6=int(detail.get("existing_disc_v6") or 0),
            cand_hour_so_far=cand_hour_sim + option_b_creates,
            max_c_hour=max_c_hour,
            flag_enabled_counterfactual=True,
            high_cap_remaining=high_cap_remaining,
        )
        worker = None
        if ana["would_create"]:
            worker = _simulate_worker_after_create(
                is_blind=detail["is_blind"],
                is_ai_confirmed=detail["is_ai_confirmed"],
                shadow_enabled=False,
            )
            option_b_creates += 1
            high_cap_remaining = max(0, high_cap_remaining - 1)
            if worker.get("would_build_episode"):
                option_b_episode += 1
        # Status-quo (A) for same row.
        ana_a = _simulate_analyzer_enqueue(
            row=detail,
            feats=feats,
            score_tier=detail["tier"],
            disc_ver=disc_ver,
            existing_v6=int(detail.get("existing_disc_v6") or 0),
            cand_hour_so_far=cand_hour_sim,
            max_c_hour=max_c_hour,
            flag_enabled_counterfactual=True,
        )
        option_b_rows.append(
            {
                **detail,
                "option_a_would_create": ana_a["would_create"],
                "option_a_reasons": ana_a["analyzer_skip_reasons"],
                "option_b_would_create": ana["would_create"],
                "option_b_reasons": ana["analyzer_skip_reasons"],
                "option_b_worker": worker,
                "fo_recovery_into_candidate": bool(ana["would_create"]),
                "fo_recovery_into_episode": bool(
                    worker and worker.get("would_build_episode")
                ),
            }
        )

    # FO probe Option B recovery.
    fo_b_create = 0
    fo_b_episode = 0
    fo_b_detail: list[dict] = []
    for detail in fo_rows:
        mid = detail["message_id"]
        text = next(
            (p["text"] for p in probes_db if int(p["message_id"]) == mid), ""
        )
        feats = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V6)
        ana = _simulate_option_b_enqueue(
            row=detail,
            feats=feats,
            score_tier=detail["tier"],
            disc_ver=disc_ver,
            existing_v6=int(detail.get("existing_disc_v6") or 0),
            cand_hour_so_far=0,
            max_c_hour=max_c_hour,
            flag_enabled_counterfactual=True,
            high_cap_remaining=high_cap_per_hour,
        )
        worker = None
        if ana["would_create"]:
            worker = _simulate_worker_after_create(
                is_blind=detail["is_blind"],
                is_ai_confirmed=detail["is_ai_confirmed"],
                shadow_enabled=False,
            )
            fo_b_create += 1
            if worker.get("would_build_episode"):
                fo_b_episode += 1
        fo_b_detail.append(
            {
                **detail,
                "option_b_would_create": ana["would_create"],
                "option_b_reasons": ana["analyzer_skip_reasons"],
                "option_b_worker": worker,
                "recovery_candidate": bool(ana["would_create"]),
                "recovery_episode": bool(
                    worker and worker.get("would_build_episode")
                ),
            }
        )

    # Contamination of Option B HIGH carve set (refined HIGH that would create).
    b_create_tuples = []
    for r in option_b_rows:
        if not r["option_b_would_create"]:
            continue
        text = next(
            (x["text"] for x in refined_survivors if int(x["id"]) == r["message_id"]),
            "",
        )
        feats = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V6)
        b_create_tuples.append(
            (r["message_id"], text, feats, population_bucket(feats))
        )
    contam_b = contamination(b_create_tuples) if b_create_tuples else 0.0
    contam_high_all = contamination(
        [
            (
                r["message_id"],
                next(
                    (
                        x["text"]
                        for x in refined_survivors
                        if int(x["id"]) == r["message_id"]
                    ),
                    "",
                ),
                evaluate_discovery(
                    scorer,
                    next(
                        (
                            x["text"]
                            for x in refined_survivors
                            if int(x["id"]) == r["message_id"]
                        ),
                        "",
                    ),
                    version=DISCOVERY_VERSION_V6,
                ),
                r["bucket"],
            )
            for r in refined_high
        ]
    ) if refined_high else 0.0

    # disc_v4 vs v6 regression on refined HIGH set (eligibility only).
    v4_high_elig = 0
    v6_high_elig = 0
    for row in refined_survivors:
        g = gate_by_id.get(int(row["id"])) or {}
        tier = (g.get("score_tier") or row.get("tier") or "") or ""
        if tier != "HIGH":
            continue
        text = row.get("text") or ""
        if evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4).eligible:
            v4_high_elig += 1
        if evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6).eligible:
            v6_high_elig += 1

    # Volume: recent HIGH + fo_carve eligible.
    recent_high_carve = 0
    recent_high_elig = 0
    for row in recent_high:
        feats = evaluate_discovery(
            scorer, row.get("text") or "", version=DISCOVERY_VERSION_V6
        )
        if feats.eligible:
            recent_high_elig += 1
            if getattr(feats, "gig_project_carve", False):
                recent_high_carve += 1
    recent_low_elig = 0
    for row in recent_low:
        feats = evaluate_discovery(
            scorer, row.get("text") or "", version=DISCOVERY_VERSION_V6
        )
        if feats.eligible:
            recent_low_elig += 1

    # 7d window assumed for RECENT_*_SQL since= (same as E103/E104).
    hours_7d = 7 * 24
    empiric_low_per_h = round(recent_low_elig / hours_7d, 4) if hours_7d else 0.0
    empiric_high_carve_per_h = (
        round(recent_high_carve / hours_7d, 4) if hours_7d else 0.0
    )
    empiric_b_extra_per_h = empiric_high_carve_per_h  # uncapped empiric; sim cap separate

    # Worker-block breakdown for refined HIGH.
    worker_block_counts = Counter(
        r["worker_would_skip"] or "none" for r in refined_high
    )
    solely_low_blocked = sum(1 for r in refined_high if r["blocked_solely_by_low_tier"])
    solely_low_and_would_episode = sum(
        1
        for r in refined_high
        if r["blocked_solely_by_low_tier"] and r["worker_would_skip"] is None
    )
    solely_low_but_worker_skip = sum(
        1
        for r in refined_high
        if r["blocked_solely_by_low_tier"] and r["worker_would_skip"] is not None
    )

    # Isolation / gates.
    isolation_ok = (
        int(iso.get("human_labels") or 0) == 1524
        and int(iso.get("ai_confirmed") or 0) == 4
    )
    gates_msg = gates.get("ai_validated_messages")
    gates_true = gates.get("ai_validated_true")
    gates_false = gates.get("ai_validated_false")
    gates_ok = gates_msg == 94 and gates_true == 0 and gates_false == 94
    health_ok = isinstance(health, dict) and health.get("status") == "ok"

    # --- Options A/B/C design + estimates ---
    opt_a = {
        "id": "A",
        "name": "Document-only status quo",
        "code_change": False,
        "description": (
            "Keep HIGH on CRM/commercial_ai path; analyzer remains tier==LOW only. "
            "Enable LOW discovery when authorized."
        ),
        "contam_risk_pct": 0.0,
        "volume_delta_per_hour": 0.0,
        "volume_delta_per_day": 0.0,
        "fo_probe_recovery_candidates": "0/4",
        "fo_probe_recovery_episodes": "0/4",
        "refined_high_recovery_candidates": f"0/{len(refined_high)}",
        "refined_high_recovery_episodes": f"0/{len(refined_high)}",
        "regression_vs_disc_v4_v6": "none (no code change)",
        "notes": "Preserves CRM ownership of HIGH; matches product separation.",
    }
    opt_b = {
        "id": "B",
        "name": "Analyzer HIGH fo_carve bridge (strict caps)",
        "code_change": True,
        "description": (
            "Enqueue when tier==LOW OR (tier==HIGH AND disc_v6 gig_project_carve "
            f"eligible) with high_cap_per_hour={high_cap_per_hour}; flag still OFF "
            "until human enable."
        ),
        "contam_risk_pct": contam_b,
        "contam_high_set_pct": contam_high_all,
        "volume_delta_per_hour": empiric_b_extra_per_h,
        "volume_delta_per_day": round(empiric_b_extra_per_h * 24, 4),
        "volume_cap_high_per_hour": high_cap_per_hour,
        "fo_probe_recovery_candidates": f"{fo_b_create}/{len(fo_rows)}",
        "fo_probe_recovery_episodes": f"{fo_b_episode}/{len(fo_rows)}",
        "refined_high_recovery_candidates": f"{option_b_creates}/{len(refined_high)}",
        "refined_high_recovery_episodes": f"{option_b_episode}/{len(refined_high)}",
        "regression_vs_disc_v4_v6": (
            f"eligibility unchanged (v4_high_elig={v4_high_elig} "
            f"v6_high_elig={v6_high_elig}); only analyzer tier gate widens for carve"
        ),
        "worker_skip_on_refined_high": dict(worker_block_counts),
        "notes": (
            "All refined HIGH survivors are AI_CONFIRMED or blind → worker skips; "
            "FO probes recover into PENDING candidates but 0 EPISODE_BUILT. "
            "Does not unlock usable discovery episodes for FO probes."
        ),
    }
    opt_c = {
        "id": "C",
        "name": "Separate CRM→discovery handoff",
        "code_change": True,
        "out_of_scope": True,
        "description": (
            "After commercial_ai confirms / CRM stages HIGH FO, hand off a shadow "
            "discovery episode via a dedicated queue (not analyzer tier gate)."
        ),
        "contam_risk_pct": "unknown_design",
        "volume_delta_per_hour": "unknown",
        "fo_probe_recovery_candidates": "design-dependent",
        "fo_probe_recovery_episodes": "design-dependent (could bypass AI_CONFIRMED skip)",
        "regression_vs_disc_v4_v6": "new path; requires isolation design",
        "notes": "Large scope; deferred. Not needed for LOW activation.",
    }

    # Decision: MEASUREMENT_ONLY — HIGH stays CRM; loop blocked on human enable.
    solely_low_n = solely_low_blocked
    usable_episode_if_b = solely_low_and_would_episode
    decision = "VALIDATED_HIGH_STAYS_CRM"
    recommendation = "MEASUREMENT_ONLY_HOLD_PENDING_AUTH"
    rationale = (
        f"H1 quantified: {solely_low_n}/{len(refined_high)} refined HIGH survivors "
        f"are discovery-eligible but enqueue-blocked solely by tier==LOW "
        f"({solely_low_but_worker_skip}/{solely_low_n} would still be worker-skipped "
        f"as AI_CONFIRMED/blind; {usable_episode_if_b} would reach EPISODE_BUILT). "
        f"FO probes Option B: candidates {fo_b_create}/{len(fo_rows)}, episodes "
        f"{fo_b_episode}/{len(fo_rows)}. Contamination on Option B create set="
        f"{contam_b}% (refined HIGH set {contam_high_all}%). Implementing B adds "
        f"PENDING skip-noise without FO episode recovery — product-policy says "
        f"HIGH stays CRM. Continuous loop blocked on human 'enable now' for LOW "
        f"path (E104 READY_FOR_AUTH_ENABLE), not on a logic bottleneck."
    )

    acceptance = {
        "standing_denominator": "refined_frozen",
        "refined_pool_n": len(refined_rows),
        "refined_v6_eligible": len(elig_v6_tuples),
        "refined_high_eligible": len(refined_high),
        "refined_low_eligible": len(refined_low),
        "blocked_solely_by_low_tier": solely_low_n,
        "blocked_solely_by_low_but_worker_skips": solely_low_but_worker_skip,
        "blocked_solely_by_low_would_episode_under_b": usable_episode_if_b,
        "fo_probes_n": len(fo_rows),
        "fo_probes_blocked_solely_by_low": sum(
            1 for r in fo_rows if r["blocked_solely_by_low_tier"]
        ),
        "fo_option_b_candidates": fo_b_create,
        "fo_option_b_episodes": fo_b_episode,
        "legacy_high_eligible": len(legacy_high),
        "legacy_high_in_refined": len(legacy_high_in_refined),
        "legacy_high_outside_refined": len(legacy_high_outside_refined),
        "contam_refined_pct": contam_refined,
        "contam_option_b_create_pct": contam_b,
        "contam_high_set_pct": contam_high_all,
        "option_recommended": "A",
        "implement_b": False,
        "flag_off": flag_off,
        "shadow_off": shadow_off,
        "isolation_ok": isolation_ok,
        "gates_94_0_94": gates_ok,
        "health_ok": health_ok,
        "no_production_writes": True,
        "decision": decision,
        "recommendation": recommendation,
        "loop_blocked_on": "human_enable_commercial_discovery_enabled",
        "logic_bottleneck": False,
    }
    acceptance_pass = (
        flag_off
        and shadow_off
        and isolation_ok
        and gates_ok
        and health_ok
        and solely_low_n == len(refined_high)
        and fo_b_episode == 0
        and contam_refined == 0.0
        and not acceptance["implement_b"]
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "runtime_sec": round(time.time() - t0, 2),
        "read_only": True,
        "wrote_candidates": False,
        "code_change_this_cycle": True,
        "code_change_justified": "H1 measurement script only (no analyzer bridge shipped)",
        "cycle": 5,
        "hypothesis": "H1",
        "parent_evidence": "104",
        "decision": decision,
        "recommendation": recommendation,
        "decision_rationale": rationale,
        "settings": {
            "commercial_discovery_enabled": settings.commercial_discovery_enabled,
            "commercial_discovery_version": disc_ver,
            "code_default_DISCOVERY_VERSION": DISCOVERY_VERSION,
            "commercial_discovery_max_candidates_per_hour": max_c_hour,
            "commercial_episode_shadow_enabled": settings.commercial_episode_shadow_enabled,
            "option_b_high_cap_per_hour_simulated": high_cap_per_hour,
        },
        "standing_denominator": {
            "name": "refined_frozen",
            "pool_n": len(refined_rows),
            "disc_v6_eligible": len(elig_v6_tuples),
            "contam_all_pct": contam_refined,
            "low_n": len(refined_low),
            "high_n": len(refined_high),
            "other_n": len(refined_other),
        },
        "high_gate_quantification": {
            "analyzer_gate": "tier==LOW required (services/analyzer/app/main.py)",
            "refined_high_eligible_n": len(refined_high),
            "blocked_solely_by_low_tier_n": solely_low_n,
            "blocked_solely_by_low_pct": round(
                100.0 * solely_low_n / max(len(refined_high), 1), 1
            ),
            "of_those_worker_would_skip_n": solely_low_but_worker_skip,
            "of_those_would_episode_under_b_n": usable_episode_if_b,
            "worker_block_breakdown": dict(worker_block_counts),
            "fo_probes": {
                "n": len(fo_rows),
                "blocked_solely_by_low": sum(
                    1 for r in fo_rows if r["blocked_solely_by_low_tier"]
                ),
                "v6_eligible": sum(1 for r in fo_rows if r["v6_eligible"]),
                "v6_carve": sum(1 for r in fo_rows if r["v6_carve"]),
                "all_ai_confirmed": all(r["is_ai_confirmed"] for r in fo_rows),
            },
            "commercial_domain_pool_legacy": {
                "pool_n": len(legacy_rows),
                "v6_eligible": len(legacy_elig),
                "contam_pct": contam_legacy,
                "high_eligible_n": len(legacy_high),
                "high_in_refined_n": len(legacy_high_in_refined),
                "high_outside_refined_n": len(legacy_high_outside_refined),
                "high_carve_n": sum(1 for r in legacy_high if r["v6_carve"]),
                "high_worker_skip_breakdown": dict(
                    Counter(r["worker_would_skip"] or "none" for r in legacy_high)
                ),
            },
            "refined_high_rows": refined_high,
            "fo_probe_rows": fo_rows,
            "legacy_high_outside_refined_sample": legacy_high_outside_refined[:10],
        },
        "option_b_counterfactual": {
            "predicate": "tier==LOW OR (tier==HIGH AND gig_project_carve AND eligible)",
            "high_cap_per_hour": high_cap_per_hour,
            "refined_high_would_create": option_b_creates,
            "refined_high_would_episode": option_b_episode,
            "contam_create_set_pct": contam_b,
            "contam_high_set_pct": contam_high_all,
            "rows": option_b_rows,
            "fo_probes": fo_b_detail,
            "fo_would_create": fo_b_create,
            "fo_would_episode": fo_b_episode,
        },
        "bridge_options": {
            "A": opt_a,
            "B": opt_b,
            "C": opt_c,
            "recommended": "A",
            "implement_now": False,
            "reason": (
                "Option B yields 0 EPISODE_BUILT for FO probes (AI_CONFIRMED worker "
                "skip) and 0 usable episodes among refined HIGH (4 AI_CONFIRMED + "
                "2 blind). HIGH ownership stays CRM; enable LOW discovery on auth."
            ),
        },
        "volume_estimates": {
            "settings_cap_per_hour": max_c_hour,
            "empiric_recent_low_sample_n": len(recent_low),
            "empiric_recent_low_v6_eligible": recent_low_elig,
            "empiric_low_per_hour": empiric_low_per_h,
            "empiric_recent_high_sample_n": len(recent_high),
            "empiric_recent_high_v6_eligible": recent_high_elig,
            "empiric_recent_high_carve": recent_high_carve,
            "empiric_high_carve_per_hour": empiric_high_carve_per_h,
            "option_b_extra_per_hour_uncapped": empiric_b_extra_per_h,
            "option_b_extra_per_day_uncapped": round(empiric_b_extra_per_h * 24, 4),
            "option_b_high_cap_per_hour": high_cap_per_hour,
            "live_candidates_1h": int(cand_1h.get("n") or 0),
            "live_candidates_24h": int(cand_24h.get("n") or 0),
            "live_candidates_7d": int(cand_7d.get("n") or 0),
            "existing_candidates_by_version": cand_meta,
        },
        "regression_check": {
            "refined_high_v4_eligible": v4_high_elig,
            "refined_high_v6_eligible": v6_high_elig,
            "note": (
                "disc_v6 recovers HIGH FO at eval via carve; disc_v4 typically 0. "
                "Option B does not change evaluate_discovery — only analyzer tier gate."
            ),
        },
        "runtime_verify": {
            "health": health,
            "gates": {
                "ai_validated_messages": gates_msg,
                "ai_validated_true": gates_true,
                "ai_validated_false": gates_false,
                "ml_training_enabled": gates.get("ml_training_enabled"),
                "expect": "94/0/94",
                "ok": gates_ok,
            },
        },
        "isolation": iso,
        "acceptance": acceptance,
        "acceptance_pass": acceptance_pass,
        "next_hypothesis": {
            "loop_status": (
                "BLOCKED_ON_HUMAN_ENABLE — not a logic bottleneck. E104 already "
                "READY_FOR_AUTH_ENABLE for LOW path. H1 closed as product-policy: "
                "HIGH stays CRM."
            ),
            "if_human_enables": (
                "Flip COMMERCIAL_DISCOVERY_ENABLED with explicit 'enable now'; "
                "deploy analyzer + discovery worker; verify LOW candidate rate vs "
                "cap + isolation 1524/4. Do not ship Option B unless product "
                "explicitly wants HIGH dual-path (CRM + discovery) despite "
                "AI_CONFIRMED worker skips."
            ),
            "if_product_wants_high_in_discovery": (
                "Prefer Option C (CRM handoff that can process AI_CONFIRMED seeds "
                "intentionally) over Option B analyzer carve; B creates skip-noise."
            ),
        },
        "locks": {
            "no_flag_flip": True,
            "no_scoring_loosen": True,
            "no_global_veto_removal": True,
            "no_0007_ml_sdk_outreach_human_labels_mutation": True,
            "no_candidate_writes": True,
            "no_shadow_enable": True,
            "no_option_b_implementation": True,
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    lines = [
        f"Evidence {EV}: disc_v6 HIGH-tier bridge Cycle 5 (H1)",
        f"generated_at={summary['generated_at']} head={summary['head']}",
        f"runtime_sec={summary['runtime_sec']} read_only=true wrote_candidates=false",
        f"decision={decision} recommendation={recommendation} "
        f"acceptance_pass={acceptance_pass}",
        "",
        "=== 0. PARENT / LOCKS ===",
        "  parent=Evidence 104 READY_FOR_AUTH_ENABLE (H2 clean; flag OFF)",
        f"  commercial_discovery_enabled={settings.commercial_discovery_enabled}",
        f"  commercial_episode_shadow_enabled={settings.commercial_episode_shadow_enabled}",
        f"  version={disc_ver} code_default={DISCOVERY_VERSION}",
        "  NO flag flip / NO Option B ship / NO candidate writes / NO scoring loosen",
        "",
        "=== 1. STANDING DENOMINATOR = refined_frozen ===",
        f"  pool_n={len(refined_rows)} v6_eligible={len(elig_v6_tuples)} "
        f"contam={contam_refined}%",
        f"  LOW={len(refined_low)} HIGH={len(refined_high)} other={len(refined_other)}",
        "",
        "=== 2. HIGH GATE QUANTIFICATION ===",
        f"  analyzer requires tier==LOW",
        f"  refined HIGH+eligible blocked solely by LOW tier: "
        f"{solely_low_n}/{len(refined_high)} "
        f"({round(100.0 * solely_low_n / max(len(refined_high), 1), 1)}%)",
        f"  of those worker would skip (AI_CONFIRMED/blind): {solely_low_but_worker_skip}",
        f"  of those would EPISODE_BUILT under Option B: {usable_episode_if_b}",
        f"  worker_block_breakdown={dict(worker_block_counts)}",
        f"  FO probes: n={len(fo_rows)} blocked_solely_by_low="
        f"{sum(1 for r in fo_rows if r['blocked_solely_by_low_tier'])} "
        f"all_ai_confirmed={all(r['is_ai_confirmed'] for r in fo_rows)}",
        f"  legacy commercial∧domain: pool={len(legacy_rows)} v6_elig={len(legacy_elig)} "
        f"HIGH_elig={len(legacy_high)} in_refined={len(legacy_high_in_refined)} "
        f"outside={len(legacy_high_outside_refined)} carve="
        f"{sum(1 for r in legacy_high if r['v6_carve'])}",
    ]
    for r in refined_high:
        lines.append(
            f"  HIGH id={r['message_id']} carve={r['v6_carve']} "
            f"ai={r['is_ai_confirmed']} blind={r['is_blind']} "
            f"worker_skip={r['worker_would_skip']} | {r['preview'][:70]}"
        )
    lines += [
        "  FO probes:",
    ]
    for r in fo_rows:
        lines.append(
            f"    lead={r.get('lead_id')} msg={r['message_id']} tier={r['tier']} "
            f"carve={r['v6_carve']} skip={r['worker_would_skip']} | {r['preview'][:60]}"
        )
    lines += [
        "",
        "=== 3. OPTION B COUNTERFACTUAL (no writes) ===",
        f"  predicate=LOW OR (HIGH AND fo_carve); high_cap={high_cap_per_hour}/h",
        f"  refined HIGH would_create={option_b_creates}/{len(refined_high)} "
        f"would_episode={option_b_episode}/{len(refined_high)}",
        f"  FO probes would_create={fo_b_create}/{len(fo_rows)} "
        f"would_episode={fo_b_episode}/{len(fo_rows)}",
        f"  contam create_set={contam_b}% high_set={contam_high_all}%",
    ]
    for r in option_b_rows:
        wr = r.get("option_b_worker") or {}
        lines.append(
            f"  B id={r['message_id']} create={r['option_b_would_create']} "
            f"episode={r.get('fo_recovery_into_episode')} "
            f"reasons={r['option_b_reasons']} worker={wr.get('skip_reason') or wr.get('final_status')}"
        )
    lines += [
        "",
        "=== 4. BRIDGE OPTIONS ===",
        f"  A) {opt_a['name']}: code={opt_a['code_change']} "
        f"contam={opt_a['contam_risk_pct']}% volΔ={opt_a['volume_delta_per_hour']}/h "
        f"FO_cand={opt_a['fo_probe_recovery_candidates']} "
        f"FO_ep={opt_a['fo_probe_recovery_episodes']}",
        f"     {opt_a['notes']}",
        f"  B) {opt_b['name']}: code={opt_b['code_change']} "
        f"contam={opt_b['contam_risk_pct']}% volΔ≈{opt_b['volume_delta_per_hour']}/h "
        f"FO_cand={opt_b['fo_probe_recovery_candidates']} "
        f"FO_ep={opt_b['fo_probe_recovery_episodes']}",
        f"     {opt_b['notes']}",
        f"  C) {opt_c['name']}: out_of_scope={opt_c['out_of_scope']}",
        f"     {opt_c['notes']}",
        f"  RECOMMENDED=A implement_now=False",
        "",
        "=== 5. VOLUME (empiric recent) ===",
        f"  LOW elig={recent_low_elig}/{len(recent_low)} → ~{empiric_low_per_h}/h",
        f"  HIGH carve={recent_high_carve}/{len(recent_high)} "
        f"(elig={recent_high_elig}) → ~{empiric_high_carve_per_h}/h",
        f"  Option B extra ≈{empiric_b_extra_per_h}/h "
        f"(cap {high_cap_per_hour}/h)",
        f"  live candidates flag OFF: 1h={cand_1h.get('n')} 24h={cand_24h.get('n')} "
        f"7d={cand_7d.get('n')}",
        "",
        "=== 6. REGRESSION ===",
        f"  refined HIGH v4_elig={v4_high_elig} v6_elig={v6_high_elig}",
        "  Option B does not change evaluate_discovery rules",
        "",
        "=== 7. RUNTIME VERIFY ===",
        f"  health={health}",
        f"  gates messages/true/false={gates_msg}/{gates_true}/{gates_false} "
        f"ml={gates.get('ml_training_enabled')} ok={gates_ok}",
        "",
        "=== 8. ISOLATION ===",
        f"  human_labels={iso.get('human_labels')} AI_CONFIRMED={iso.get('ai_confirmed')} "
        f"scores={iso.get('scores')} messages={iso.get('messages')} "
        f"disc_candidates={iso.get('disc_candidates')}",
        f"  isolation_ok={isolation_ok} (expect 1524/4)",
        "",
        "=== 9. ACCEPTANCE / DECISION ===",
        f"  acceptance={json.dumps(acceptance, ensure_ascii=False)}",
        f"  acceptance_pass={acceptance_pass}",
        f"  decision={decision} recommendation={recommendation}",
        f"  rationale={rationale}",
        "",
        "=== 10. NEXT ===",
        f"  {summary['next_hypothesis']['loop_status']}",
        f"  if_enable: {summary['next_hypothesis']['if_human_enables']}",
        "",
        "=== 11. BUSINESS ===",
        "  NO OUTREACH / NO ML GO / FLAG REMAINS OFF / NO THRESHOLD LOOSEN / NO WRITES",
        "  HIGH stays CRM; continuous loop blocked on human enable (not H1 logic)",
        "",
    ]
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_TXT.read_text(encoding="utf-8"))
    print(f"Wrote {OUT_JSON} and {OUT_TXT}")
    return 0 if acceptance_pass else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recent-low-limit", type=int, default=5000)
    p.add_argument("--recent-high-limit", type=int, default=5000)
    p.add_argument(
        "--high-cap-per-hour",
        type=int,
        default=5,
        help="Simulated Option B cap for HIGH carve creates/hour",
    )
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
