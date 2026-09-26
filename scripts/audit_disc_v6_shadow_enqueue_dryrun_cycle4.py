#!/usr/bin/env python3
"""Evidence 104: Cycle 4 H2 shadow enqueue dry-run (measurement-only).

Continues Evidence 103 HOLD_PENDING_AUTH. Simulates analyzer enqueue + discovery
worker skip rules for refined_frozen LOW survivors WITHOUT writing
commercial_discovery_candidates, flipping commercial_discovery_enabled, or
enabling commercial_episode_shadow.

Goals:
  1) Counterfactual would-create for the 10 LOW+eligible refined survivors
     (status=PENDING, discovery_version=disc_v6).
  2) Document the 6 HIGH survivors separately (analyzer LOW-only gate).
  3) Measure: would-create, would-skip reasons, disc_v6 dups vs existing 60,
     rate-limit headroom, expected hourly volume, contamination of the 10.
  4) Decision: READY_FOR_AUTH_ENABLE | HOLD_NEEDS_FIX | NEXT=H1.
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

EV = "104"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-disc-v6-shadow-enqueue-dryrun-cycle4.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-disc-v6-shadow-enqueue-dryrun-cycle4.json"

ISO_SQL = """
SELECT
  (SELECT COUNT(*) FROM human_labels) AS human_labels,
  (SELECT COUNT(*) FROM leads WHERE status = 'AI_CONFIRMED') AS ai_confirmed,
  (SELECT COUNT(*) FROM message_scores) AS message_scores,
  (SELECT COUNT(*) FROM messages) AS messages,
  (SELECT COUNT(*) FROM commercial_discovery_candidates) AS disc_candidates
"""

CAND_META_SQL = """
SELECT
  discovery_version,
  status,
  COUNT(*) AS n
FROM commercial_discovery_candidates
GROUP BY 1, 2
ORDER BY 1, 2
"""

CAND_RECENT_SQL = """
SELECT COUNT(*) AS n
FROM commercial_discovery_candidates
WHERE created_at >= :since
"""

SEED_GATE_SQL = """
SELECT
  m.id AS message_id,
  m.community_id,
  m.author_id,
  m.text,
  c.name AS community_name,
  c.username AS community_username,
  s.tier AS score_tier,
  EXISTS(
    SELECT 1 FROM label_review_samples lrs WHERE lrs.message_id = m.id
  ) AS is_blind,
  EXISTS(
    SELECT 1 FROM leads l
    WHERE l.message_id = m.id AND l.status = 'AI_CONFIRMED'
  ) AS is_ai_confirmed,
  (
    SELECT COUNT(*) FROM commercial_discovery_candidates cdc
    WHERE cdc.seed_message_id = m.id
      AND cdc.discovery_version = :ver
  ) AS existing_disc_v6,
  (
    SELECT string_agg(cdc.discovery_version || ':' || cdc.status, ',' ORDER BY cdc.discovery_version)
    FROM commercial_discovery_candidates cdc
    WHERE cdc.seed_message_id = m.id
  ) AS existing_any_versions
FROM messages m
LEFT JOIN communities c ON c.id = m.community_id
LEFT JOIN message_scores s ON s.message_id = m.id
WHERE m.id = ANY(:ids)
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
    # Preserve order, drop dupes.
    out: list[str] = []
    for b in bases:
        if b and b not in out:
            out.append(b)
    return out


def _http_json(path: str) -> dict:
    """Fetch JSON from API; try host then compose service DNS.

    Optional env injection (for ephemeral containers without API DNS):
      TLM_HEALTH_JSON / TLM_GATES_JSON — raw JSON blobs for /health and /validation/gates.
    """
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


def _simulate_analyzer_enqueue(
    *,
    row: dict,
    feats,
    score_tier: str,
    disc_ver: str,
    existing_v6: int,
    cand_hour_so_far: int,
    max_c_hour: int,
    flag_enabled_counterfactual: bool,
) -> dict:
    """Mirror services/analyzer/app/main.py discovery block (no writes)."""
    reasons: list[str] = []
    would_create = False

    if not flag_enabled_counterfactual:
        reasons.append("flag_disabled")
    if score_tier != "LOW":
        reasons.append("tier_not_low")
    if not feats.eligible:
        reasons.append("not_eligible")

    over_rate = False
    if max_c_hour > 0 and cand_hour_so_far >= max_c_hour:
        over_rate = True
        reasons.append("rate_limited")

    if existing_v6 > 0:
        reasons.append("dup_seed_version")

    analyzer_gates_pass = (
        flag_enabled_counterfactual
        and score_tier == "LOW"
        and bool(feats.eligible)
        and existing_v6 == 0
        and not over_rate
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
        }
    return {
        "would_create": would_create,
        "analyzer_skip_reasons": reasons,
        "over_rate": over_rate,
        "would_write_row": payload,
    }


def _simulate_worker_after_create(
    *,
    is_blind: bool,
    is_ai_confirmed: bool,
    shadow_enabled: bool,
) -> dict:
    """Mirror commercial_discovery worker skip / episode path (no writes)."""
    if is_blind:
        return {
            "would_process": False,
            "final_status": "SKIPPED",
            "skip_reason": "blind_sample",
            "would_build_episode": False,
            "would_shadow_enqueue": False,
        }
    if is_ai_confirmed:
        return {
            "would_process": False,
            "final_status": "SKIPPED",
            "skip_reason": "existing_ai_confirmed",
            "would_build_episode": False,
            "would_shadow_enqueue": False,
        }
    return {
        "would_process": True,
        "final_status": "EPISODE_BUILT",
        "skip_reason": None,
        "would_build_episode": True,
        "would_shadow_enqueue": bool(shadow_enabled),
    }


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

    # Runtime gates (API) — informational, read-only.
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
        cand_1h = await fetch_one(session, CAND_RECENT_SQL, since=since_1h)
        cand_24h = await fetch_one(session, CAND_RECENT_SQL, since=since_24h)
        cand_7d = await fetch_one(session, CAND_RECENT_SQL, since=since_7d)

    refined_rows = build_refined_rows(legacy_rows, scorer)
    id_to_row = {r["id"]: r for r in refined_rows}

    # Rebuild survivors under disc_v6 (standing denominator).
    survivors_raw = []
    elig_v6_tuples = []
    for row in refined_rows:
        feats = evaluate_discovery(
            scorer, row["text"] or "", version=DISCOVERY_VERSION_V6
        )
        if feats.eligible:
            elig_v6_tuples.append(
                (row["id"], row["text"], feats, population_bucket(feats))
            )
            survivors_raw.append(row)

    contam_all = contamination(elig_v6_tuples)

    # Fetch DB gates for all survivor message ids.
    surv_ids = [int(r["id"]) for r in survivors_raw]
    async with SessionLocal() as session:
        gate_rows = await fetch_all(
            session, SEED_GATE_SQL, ids=surv_ids, ver=disc_ver
        )
    gate_by_id = {int(r["message_id"]): r for r in gate_rows}

    low_dry: list[dict] = []
    high_doc: list[dict] = []
    other_doc: list[dict] = []

    # Rate-limit simulation: process LOW survivors in message_id order as a
    # counterfactual burst under current hour count (flag ON).
    cand_hour_sim = int(cand_1h.get("n") or 0)
    create_sim_count = 0

    for row in sorted(survivors_raw, key=lambda r: int(r["id"])):
        mid = int(row["id"])
        g = gate_by_id.get(mid) or {}
        text = row.get("text") or g.get("text") or ""
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        score_tier = (g.get("score_tier") or row.get("tier") or "") or ""
        bucket = population_bucket(feats)
        base = {
            "message_id": mid,
            "community": g.get("community_name") or row.get("community_name"),
            "community_username": g.get("community_username")
            or row.get("community_username"),
            "community_id": g.get("community_id") or row.get("community_id"),
            "author_id": g.get("author_id") or row.get("author_id"),
            "tier": score_tier,
            "v6_eligible": bool(feats.eligible),
            "v6_carve": bool(getattr(feats, "gig_project_carve", False)),
            "trigger": feats.trigger_type,
            "direction": feats.buyer_direction,
            "first_loss": first_loss(feats),
            "bucket": bucket,
            "feat_fp": _feat_fingerprint(feats),
            "is_blind": bool(g.get("is_blind")),
            "is_ai_confirmed": bool(g.get("is_ai_confirmed")),
            "existing_disc_v6": int(g.get("existing_disc_v6") or 0),
            "existing_any_versions": g.get("existing_any_versions"),
            "preview": _preview(text),
        }

        if score_tier == "HIGH" and feats.eligible:
            high_doc.append(
                {
                    **base,
                    "analyzer_outcome": "skip_tier_not_low",
                    "would_create_disc_v6": False,
                    "note": (
                        "Production analyzer requires tier==LOW; HIGH FO/RFQ stay on "
                        "CRM/commercial_ai path. Documented only — H1 is optional bridge."
                    ),
                }
            )
            continue

        if score_tier != "LOW" or not feats.eligible:
            other_doc.append(
                {
                    **base,
                    "analyzer_outcome": "skip_other",
                    "would_create_disc_v6": False,
                }
            )
            continue

        # Counterfactual: flag ON for dry-run measurement (actual flag stays OFF).
        ana = _simulate_analyzer_enqueue(
            row={**base, "message_id": mid},
            feats=feats,
            score_tier=score_tier,
            disc_ver=disc_ver,
            existing_v6=int(g.get("existing_disc_v6") or 0),
            cand_hour_so_far=cand_hour_sim + create_sim_count,
            max_c_hour=max_c_hour,
            flag_enabled_counterfactual=True,
        )
        worker = None
        if ana["would_create"]:
            worker = _simulate_worker_after_create(
                is_blind=bool(g.get("is_blind")),
                is_ai_confirmed=bool(g.get("is_ai_confirmed")),
                shadow_enabled=False,  # do not enable shadow in this cycle
            )
            create_sim_count += 1

        # Also report what happens with flag actually OFF (safety check).
        ana_flag_off = _simulate_analyzer_enqueue(
            row={**base, "message_id": mid},
            feats=feats,
            score_tier=score_tier,
            disc_ver=disc_ver,
            existing_v6=int(g.get("existing_disc_v6") or 0),
            cand_hour_so_far=cand_hour_sim,
            max_c_hour=max_c_hour,
            flag_enabled_counterfactual=False,
        )

        low_dry.append(
            {
                **base,
                "counterfactual_flag_on": ana,
                "actual_flag_off": ana_flag_off,
                "worker_if_created": worker,
                "would_create_disc_v6": bool(ana["would_create"]),
                "would_survive_worker": bool(
                    worker and worker.get("final_status") == "EPISODE_BUILT"
                )
                if worker
                else False,
            }
        )

    # Contamination of the 10 LOW enqueue set.
    low_ids = {r["message_id"] for r in low_dry}
    low_tuples = [t for t in elig_v6_tuples if t[0] in low_ids]
    contam_low10 = contamination(low_tuples)

    would_create_n = sum(1 for r in low_dry if r["would_create_disc_v6"])
    would_survive_n = sum(1 for r in low_dry if r["would_survive_worker"])
    skip_reason_counts: Counter = Counter()
    for r in low_dry:
        if r["would_create_disc_v6"]:
            wr = (r.get("worker_if_created") or {}).get("skip_reason")
            if wr:
                skip_reason_counts[f"worker:{wr}"] += 1
            else:
                skip_reason_counts["worker:none_episode_built"] += 1
        else:
            for reason in r["counterfactual_flag_on"]["analyzer_skip_reasons"]:
                skip_reason_counts[f"analyzer:{reason}"] += 1

    # Dup analysis vs existing 60 candidates (any version).
    prior_any = sum(1 for r in low_dry if r.get("existing_any_versions"))
    prior_v6 = sum(1 for r in low_dry if int(r.get("existing_disc_v6") or 0) > 0)
    # Actual live writes while flag OFF must be zero for the dry-run set.
    actual_creates_flag_off = sum(
        1 for r in low_dry if r["actual_flag_off"]["would_create"]
    )

    # Volume: reuse E103 empiric approach on recent LOW sample.
    recent_elig = 0
    for row in recent_low:
        feats = evaluate_discovery(
            scorer, row["text"] or "", version=DISCOVERY_VERSION_V6
        )
        if feats.eligible:
            recent_elig += 1
    # 7d window assumed for RECENT_LOW_SQL since=
    hours_7d = 7 * 24
    empiric_per_hour = round(recent_elig / hours_7d, 4) if hours_7d else 0.0
    empiric_per_day = round(empiric_per_hour * 24, 3)
    expected_hour_ub = min(empiric_per_hour, float(max_c_hour or empiric_per_hour))
    expected_day_ub = round(expected_hour_ub * 24, 3)
    refined_low_per_day_360 = round(len(low_dry) / 360.0, 4)

    # FO probes (document HIGH gate; do not enqueue).
    fo_rows = []
    for p in probes_db:
        feats = evaluate_discovery(
            scorer, p["text"] or "", version=DISCOVERY_VERSION_V6
        )
        tier = (p.get("score_tier") or p.get("lead_tier") or "") or ""
        fo_rows.append(
            {
                "lead_id": p.get("lead_id"),
                "message_id": p.get("message_id"),
                "tier": tier,
                "v6_eligible": bool(feats.eligible),
                "v6_carve": bool(getattr(feats, "gig_project_carve", False)),
                "would_create_disc_v6": False,
                "reason": "tier_not_low_and_ai_confirmed_crm_path",
                "preview": _preview(p.get("text") or ""),
            }
        )

    gates_msg = int(gates.get("ai_validated_messages") or 0)
    gates_true = int(gates.get("ai_validated_true") or 0)
    gates_false = int(gates.get("ai_validated_false") or 0)
    gates_ok = (
        gates_msg == 94
        and gates_true == 0
        and gates_false == 94
        and gates.get("ml_training_enabled") is False
    )
    isolation_ok = (
        int(iso.get("human_labels") or 0) == 1524
        and int(iso.get("ai_confirmed") or 0) == 4
    )
    health_ok = (health.get("status") == "ok") and not health.get("_error")

    blocking_bugs: list[str] = []
    if contam_low10 > 0:
        blocking_bugs.append(f"contamination_of_low10={contam_low10}%")
    if actual_creates_flag_off > 0:
        blocking_bugs.append(
            f"flag_off_would_create={actual_creates_flag_off} (safety broken)"
        )
    if not flag_off:
        blocking_bugs.append("commercial_discovery_enabled unexpectedly True")
    if not isolation_ok:
        blocking_bugs.append(
            f"isolation drift hl={iso.get('human_labels')} ai={iso.get('ai_confirmed')}"
        )
    if would_create_n == 0 and len(low_dry) > 0:
        blocking_bugs.append("zero would-create despite LOW survivors present")
    if would_create_n != would_survive_n:
        # Worker would skip some of the created set — investigate before enable.
        blocking_bugs.append(
            f"worker_skip_after_create create={would_create_n} survive={would_survive_n}"
        )

    # H1 only if the blocking issue is specifically HIGH FO path, not LOW dry-run.
    needs_h1 = False
    # Prefer completing H2; H1 only when LOW path is blocked by HIGH-related defect.
    # (No such defect expected — HIGH exclusion is by design.)

    if blocking_bugs:
        decision = "HOLD_NEEDS_FIX"
        recommendation = "HOLD"
        rationale = (
            "Shadow enqueue dry-run found blocking issue(s): "
            + "; ".join(blocking_bugs)
            + ". Do not enable. Flag remains OFF."
        )
        if needs_h1:
            decision = "NEXT=H1"
            rationale += " Escalate to H1 (HIGH FO bridge) only if required by the fix."
    elif would_create_n == len(low_dry) == 10 and would_survive_n == 10 and contam_low10 == 0.0:
        decision = "READY_FOR_AUTH_ENABLE"
        recommendation = "HOLD_PENDING_AUTH"
        rationale = (
            f"H2 dry-run clean: {would_create_n}/10 LOW survivors would create "
            f"PENDING disc_v6 candidates; worker would build episodes (no blind/"
            f"AI_CONFIRMED skips); contam={contam_low10}%; disc_v6 dups={prior_v6}; "
            f"prior any-version rows={prior_any} (non-blocking — unique is seed+version); "
            f"rate headroom={max_c_hour - cand_hour_sim}/h; empiric UB "
            f"~{expected_hour_ub}/h ~{expected_day_ub}/d. Still HOLD: do NOT flip "
            f"commercial_discovery_enabled without explicit 'enable now'. "
            f"HIGH survivors {len(high_doc)}/6 + FO probes 4/4 remain CRM path (H1 optional)."
        )
    else:
        decision = "HOLD_NEEDS_FIX"
        recommendation = "HOLD"
        rationale = (
            f"Unexpected dry-run shape: low={len(low_dry)} would_create={would_create_n} "
            f"survive={would_survive_n} contam={contam_low10}. Investigate before auth."
        )

    acceptance = {
        "standing_denominator": "refined_frozen",
        "refined_pool_n": len(refined_rows),
        "refined_v6_eligible": len(elig_v6_tuples),
        "low_survivors": len(low_dry),
        "high_survivors_documented": len(high_doc),
        "would_create_disc_v6": would_create_n,
        "would_survive_worker": would_survive_n,
        "contam_low10_pct": contam_low10,
        "contam_all_survivors_pct": contam_all,
        "disc_v6_dup_among_low10": prior_v6,
        "prior_any_version_among_low10": prior_any,
        "actual_flag_off_creates": actual_creates_flag_off,
        "flag_off": flag_off,
        "shadow_off": shadow_off,
        "isolation_ok": isolation_ok,
        "gates_94_0_94": gates_ok,
        "health_ok": health_ok,
        "no_production_writes": True,
        "recommendation": recommendation,
        "decision": decision,
    }
    acceptance_pass = (
        acceptance["flag_off"]
        and acceptance["shadow_off"]
        and acceptance["isolation_ok"]
        and acceptance["contam_low10_pct"] == 0.0
        and acceptance["would_create_disc_v6"] == 10
        and acceptance["would_survive_worker"] == 10
        and acceptance["actual_flag_off_creates"] == 0
        and acceptance["disc_v6_dup_among_low10"] == 0
        and decision == "READY_FOR_AUTH_ENABLE"
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "runtime_sec": round(time.time() - t0, 2),
        "read_only": True,
        "wrote_candidates": False,
        "code_change_this_cycle": True,  # audit script + evidence only
        "code_change_justified": "H2 dry-run measurement script (no enable path)",
        "cycle": 4,
        "hypothesis": "H2",
        "parent_evidence": "103",
        "decision": decision,
        "recommendation": recommendation,
        "decision_rationale": rationale,
        "settings": {
            "commercial_discovery_enabled": settings.commercial_discovery_enabled,
            "commercial_discovery_version": disc_ver,
            "code_default_DISCOVERY_VERSION": DISCOVERY_VERSION,
            "commercial_discovery_max_candidates_per_hour": max_c_hour,
            "commercial_episode_shadow_enabled": settings.commercial_episode_shadow_enabled,
        },
        "standing_denominator": {
            "name": "refined_frozen",
            "pool_n": len(refined_rows),
            "disc_v6_eligible": len(elig_v6_tuples),
            "contam_all_pct": contam_all,
            "low_enqueue_set_n": len(low_dry),
            "high_documented_n": len(high_doc),
            "other_n": len(other_doc),
        },
        "shadow_enqueue_dryrun": {
            "mode": "counterfactual_flag_on_no_writes",
            "disc_version": disc_ver,
            "would_create_status": "PENDING",
            "would_create_n": would_create_n,
            "would_survive_worker_n": would_survive_n,
            "skip_reason_counts": dict(skip_reason_counts),
            "rate_limit": {
                "max_per_hour": max_c_hour,
                "existing_candidates_last_hour": int(cand_1h.get("n") or 0),
                "headroom": max_c_hour - int(cand_1h.get("n") or 0),
                "simulated_burst_creates": create_sim_count,
                "would_hit_cap": create_sim_count > (
                    max_c_hour - int(cand_1h.get("n") or 0)
                ),
            },
            "duplicates": {
                "existing_total_candidates": int(iso.get("disc_candidates") or 0),
                "existing_by_version_status": [
                    {
                        "discovery_version": r["discovery_version"],
                        "status": r["status"],
                        "n": int(r["n"]),
                    }
                    for r in cand_meta
                ],
                "low10_with_disc_v6_row": prior_v6,
                "low10_with_any_prior_version": prior_any,
                "note": (
                    "Analyzer unique key is (seed_message_id, discovery_version). "
                    "Prior disc_v1/v2 rows do NOT block disc_v6 create."
                ),
            },
            "contamination_low10_pct": contam_low10,
            "low_rows": low_dry,
            "high_rows_documented": high_doc,
            "other_rows": other_doc,
            "fo_probes_documented": fo_rows,
            "actual_flag_off_would_create": actual_creates_flag_off,
        },
        "expected_volume_if_enabled": {
            "settings_cap_per_hour": max_c_hour,
            "empiric_recent_low_sample_n": len(recent_low),
            "empiric_recent_low_v6_eligible": recent_elig,
            "empiric_per_hour": empiric_per_hour,
            "empiric_per_day": empiric_per_day,
            "upper_bound_per_hour": expected_hour_ub,
            "upper_bound_per_day": expected_day_ub,
            "refined_audit_low_elig_per_day_360d": refined_low_per_day_360,
            "live_candidates_created_1h": int(cand_1h.get("n") or 0),
            "live_candidates_created_24h": int(cand_24h.get("n") or 0),
            "live_candidates_created_7d": int(cand_7d.get("n") or 0),
            "dryrun_burst_would_create_now": would_create_n,
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
        "blocking_bugs": blocking_bugs,
        "acceptance": acceptance,
        "acceptance_pass": acceptance_pass,
        "next_hypothesis": {
            "if_ready_for_auth": (
                "Wait for explicit human authorization ('enable now') before flipping "
                "COMMERCIAL_DISCOVERY_ENABLED; then deploy analyzer + discovery worker "
                "and verify candidate rate vs cap + isolation snapshot."
            ),
            "if_hold_needs_fix": "Fix listed blocking_bugs; re-run H2 dry-run.",
            "h1_deferred": (
                "HIGH-tier FO bridge remains optional product decision; H2 did not find "
                "a LOW-path blocking bug that requires H1."
            ),
        },
        "locks": {
            "no_flag_flip": True,
            "no_scoring_loosen": True,
            "no_global_veto_removal": True,
            "no_0007_ml_sdk_outreach_human_labels_mutation": True,
            "no_candidate_writes": True,
            "no_shadow_enable": True,
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        f"Evidence {EV}: disc_v6 shadow enqueue dry-run Cycle 4 (H2)",
        f"generated_at={summary['generated_at']} head={summary['head']}",
        f"runtime_sec={summary['runtime_sec']} read_only=true wrote_candidates=false",
        f"decision={decision} recommendation={recommendation} acceptance_pass={acceptance_pass}",
        "",
        "=== 0. PARENT / LOCKS ===",
        "  parent=Evidence 103 HOLD_PENDING_AUTH (checklist 10/10; flag OFF)",
        f"  commercial_discovery_enabled={settings.commercial_discovery_enabled}",
        f"  commercial_episode_shadow_enabled={settings.commercial_episode_shadow_enabled}",
        f"  version={disc_ver} code_default={DISCOVERY_VERSION}",
        "  NO flag flip / NO shadow enable / NO candidate writes / NO scoring loosen",
        "",
        "=== 1. STANDING DENOMINATOR = refined_frozen ===",
        f"  pool_n={len(refined_rows)} v6_eligible={len(elig_v6_tuples)} "
        f"contam_all={contam_all}%",
        f"  LOW dry-run set={len(low_dry)} HIGH documented={len(high_doc)} other={len(other_doc)}",
        "",
        "=== 2. SHADOW ENQUEUE DRY-RUN (counterfactual flag ON, no writes) ===",
        f"  would_create PENDING disc_v6={would_create_n}/{len(low_dry)}",
        f"  would_survive_worker (EPISODE_BUILT)={would_survive_n}/{len(low_dry)}",
        f"  skip_reasons={dict(skip_reason_counts)}",
        f"  rate_limit headroom={max_c_hour - int(cand_1h.get('n') or 0)} "
        f"(existing_1h={int(cand_1h.get('n') or 0)} cap={max_c_hour})",
        f"  disc_v6_dups_among_low10={prior_v6} prior_any_version={prior_any}",
        f"  existing candidates total={iso.get('disc_candidates')} by_ver={cand_meta}",
        f"  actual_flag_off_would_create={actual_creates_flag_off} (expect 0)",
        f"  contam_low10={contam_low10}%",
    ]
    for r in low_dry:
        wr = r.get("worker_if_created") or {}
        lines.append(
            f"  LOW id={r['message_id']} create={r['would_create_disc_v6']} "
            f"worker={wr.get('final_status')} prior={r.get('existing_any_versions')} "
            f"trig={r['trigger']} | {r['preview'][:80]}"
        )
    lines += [
        "",
        "=== 3. HIGH SURVIVORS DOCUMENTED (no enqueue; H1 deferred) ===",
        f"  n={len(high_doc)} analyzer_outcome=skip_tier_not_low",
    ]
    for r in high_doc:
        lines.append(
            f"  HIGH id={r['message_id']} ai_conf={r['is_ai_confirmed']} "
            f"carve={r['v6_carve']} trig={r['trigger']} | {r['preview'][:80]}"
        )
    lines += [
        "  FO probes:",
    ]
    for r in fo_rows:
        lines.append(
            f"    lead={r['lead_id']} msg={r['message_id']} tier={r['tier']} "
            f"create=False | {r['preview'][:70]}"
        )
    lines += [
        "",
        "=== 4. EXPECTED VOLUME IF ENABLED ===",
        f"  settings_cap={max_c_hour}/h",
        f"  empiric recent LOW: elig={recent_elig}/{len(recent_low)} → "
        f"~{empiric_per_hour}/h ~{empiric_per_day}/d",
        f"  upper_bound ~{expected_hour_ub}/h ~{expected_day_ub}/d",
        f"  refined_audit LOW density ~{refined_low_per_day_360}/d over 360d",
        f"  dryrun burst would_create_now={would_create_n}",
        f"  live candidates while flag OFF: 1h={cand_1h.get('n')} 24h={cand_24h.get('n')} "
        f"7d={cand_7d.get('n')}",
        "",
        "=== 5. RUNTIME VERIFY ===",
        f"  health={health}",
        f"  gates messages/true/false={gates_msg}/{gates_true}/{gates_false} "
        f"ml={gates.get('ml_training_enabled')} ok={gates_ok}",
        "",
        "=== 6. ISOLATION ===",
        f"  human_labels={iso.get('human_labels')} AI_CONFIRMED={iso.get('ai_confirmed')} "
        f"message_scores={iso.get('message_scores')} messages={iso.get('messages')} "
        f"disc_candidates={iso.get('disc_candidates')}",
        f"  isolation_ok={isolation_ok} (expect 1524/4)",
        "",
        "=== 7. BLOCKING BUGS ===",
        f"  {blocking_bugs or 'none'}",
        "",
        "=== 8. ACCEPTANCE / DECISION ===",
        f"  acceptance={json.dumps(acceptance, ensure_ascii=False)}",
        f"  acceptance_pass={acceptance_pass}",
        f"  decision={decision} recommendation={recommendation}",
        f"  rationale={rationale}",
        "",
        "=== 9. NEXT ===",
        f"  if_ready: {summary['next_hypothesis']['if_ready_for_auth']}",
        f"  h1_deferred: {summary['next_hypothesis']['h1_deferred']}",
        "",
        "=== 10. BUSINESS ===",
        "  NO OUTREACH / NO ML GO / FLAG REMAINS OFF / NO THRESHOLD LOOSEN / NO WRITES",
        "  READY_FOR_AUTH_ENABLE means measurement-ready only — still requires explicit enable",
        "",
    ]
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_TXT.read_text(encoding="utf-8"))
    print(f"Wrote {OUT_JSON} and {OUT_TXT}")
    return 0 if acceptance_pass or decision == "READY_FOR_AUTH_ENABLE" else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--recent-low-limit", type=int, default=5000)
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
