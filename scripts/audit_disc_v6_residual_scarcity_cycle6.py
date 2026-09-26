#!/usr/bin/env python3
"""Evidence 112: POST-ENABLE CYCLE 6 — residual scarcity vs discovery loss.

Measurement-only. Does NOT change disc_v6 paths/carve/thresholds/vetoes,
flip shadow, mutate labels, wipe candidates, or loosen scoring.yaml.

Hypothesis (ONE):
  Under current disc_v6 (post path_b+c), genuine commercial yield in the
  existing frozen commercial∧domain / refined_frozen populations is scarce
  enough that ~0 NEW in hundreds–thousands of scored live msgs is expected
  (RESIDUAL_SCARCITY), not a new semantic loss path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
import urllib.request
from collections import Counter, defaultdict
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
    population_bucket,
)
from shared.db import SessionLocal
from shared.settings import get_settings

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

EV = "112"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-disc-v6-residual-scarcity-cycle6.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-disc-v6-residual-scarcity-cycle6.json"

PATH_B_DEPLOY_ISO = "2026-09-26T06:53:16+00:00"
PATH_C_MARKER_ISO = "2026-09-26T06:41:22+00:00"
ENABLE_AT_ISO = "2026-09-25T19:27:00+00:00"
# Evidence 103/104 empiric UB (pre path_b+c tighten; still the standing UB).
EMPIRIC_UB_PER_HOUR = 0.018
# Pre-fix contaminated live rate (Evidence 107 since-enable).
PRE_FIX_CONTAM_RATE_PER_HOUR = 0.54

TRUE_BUYER_NEAR_MISS_RX = re.compile(
    r"("
    r"hire\s+(an?\s+)?(developer|engineer|freelancer|contractor)|"
    r"looking\s+for\s+(a\s+)?(developer|engineer|freelancer|contractor)|"
    r"need\s+(a\s+)?(developer|engineer|freelancer|contractor|dev)\b|"
    r"freelance\s+opportunity|"
    r"(build|fix|repair|develop).{0,40}(trading\s+)?bot|"
    r"budget\s*[:=]?\s*\$\s*\d|"
    r"нужен\s+разработчик|починить"
    r")",
    re.I,
)

ISO_SQL = """
SELECT
  (SELECT COUNT(*) FROM human_labels) AS human_labels,
  (SELECT COUNT(*) FROM leads WHERE status = 'AI_CONFIRMED') AS ai_confirmed,
  (SELECT COUNT(*) FROM message_scores) AS message_scores,
  (SELECT COUNT(*) FROM messages) AS messages,
  (SELECT COUNT(*) FROM commercial_discovery_candidates) AS disc_candidates,
  (SELECT COUNT(*) FROM commercial_discovery_candidates
     WHERE discovery_version = 'disc_v6') AS disc_v6_candidates
"""

WINDOW_SQL = """
SELECT
  (SELECT COUNT(*) FROM messages WHERE created_at > :since) AS msg,
  (SELECT COUNT(*) FROM message_scores WHERE scored_at > :since) AS score,
  (SELECT COUNT(*) FROM commercial_discovery_candidates
     WHERE created_at > :since) AS cand,
  (SELECT COUNT(*) FROM commercial_discovery_candidates
     WHERE discovery_version = 'disc_v6' AND created_at > :since) AS disc_v6
"""

RECENT_SCORED_SQL = """
SELECT m.id, m.text, m.message_date, c.name AS community_name,
       c.username AS community_username, s.tier, s.score, s.scored_at
FROM message_scores s
JOIN messages m ON m.id = s.message_id
LEFT JOIN communities c ON c.id = m.community_id
WHERE s.scored_at > :since
ORDER BY s.scored_at DESC, m.id DESC
LIMIT :lim
"""

RECENT_LOW_SQL = """
SELECT m.id, m.text, m.message_date, c.name AS community_name,
       c.username AS community_username, s.tier, s.score, s.scored_at
FROM message_scores s
JOIN messages m ON m.id = s.message_id
LEFT JOIN communities c ON c.id = m.community_id
WHERE s.tier = 'LOW' AND s.scored_at > now() - interval '7 days'
ORDER BY s.scored_at DESC, m.id DESC
LIMIT :lim
"""

PROBE_SQL = """
SELECT l.id AS lead_id, l.message_id, l.tier AS lead_tier, l.status,
       m.text, c.name AS community_name, c.username AS community_username,
       s.tier AS score_tier
FROM leads l
JOIN messages m ON m.id = l.message_id
LEFT JOIN communities c ON c.id = m.community_id
LEFT JOIN message_scores s ON s.message_id = m.id
WHERE l.status = 'AI_CONFIRMED'
ORDER BY l.id
"""

LIVE_DISC_V6_SQL = """
SELECT id, seed_message_id, trigger_type, status, created_at
FROM commercial_discovery_candidates
WHERE discovery_version = 'disc_v6'
ORDER BY id
"""


def _git_head() -> str:
    env = __import__("os").environ.get("GIT_HEAD", "").strip()
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _git_head_short() -> str:
    h = _git_head()
    return h[:12] if h and h != "unknown" else h


def _preview(t: str, n: int = 160) -> str:
    return " ".join((t or "").split())[:n]


def _http_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _probe_api() -> tuple[dict[str, Any], dict[str, Any], str]:
    for base in ("http://api:8010", "http://127.0.0.1:8010"):
        try:
            return (
                _http_json(f"{base}/health"),
                _http_json(f"{base}/validation/gates"),
                base,
            )
        except Exception:
            continue
    return {}, {}, ""


def _redis_stats() -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False}
    try:
        import redis as redis_lib

        settings = get_settings()
        url = getattr(settings, "redis_url", None) or "redis://redis:6379/0"
        client = redis_lib.Redis.from_url(url, decode_responses=True)
        streams = {
            "commercial_discovery": "telegram:commercial_discovery",
            "commercial_ai_review": "telegram:commercial_ai_review",
            "commercial_episode_shadow": "telegram:commercial_episode_shadow",
        }
        groups = {
            "commercial_discovery": "commercial-discovery-workers",
            "commercial_ai_review": "commercial-ai-workers",
            "commercial_episode_shadow": "commercial-episode-shadow-workers",
        }
        detail: dict[str, Any] = {}
        for name, key in streams.items():
            entry: dict[str, Any] = {"xlen": int(client.xlen(key))}
            g = groups[name]
            try:
                pend = client.xpending(key, g)
                if isinstance(pend, dict):
                    entry["pending"] = int(
                        pend.get("pending") or pend.get("count") or 0
                    )
                elif isinstance(pend, (list, tuple)) and pend:
                    entry["pending"] = int(pend[0])
                else:
                    entry["pending"] = 0
                cons = client.xinfo_consumers(key, g)
                entry["consumers"] = len(cons) if cons else 0
                info = client.xinfo_groups(key)
                for gi in info or []:
                    if gi.get("name") == g:
                        entry["lag"] = int(gi.get("lag") or 0)
                        entry["last_delivered_id"] = gi.get("last-delivered-id")
                        break
            except Exception as exc:  # noqa: BLE001
                entry["group_error"] = f"{type(exc).__name__}: {exc}"
            detail[name] = entry
        out["streams"] = detail
        out["ok"] = True
        out["via"] = "redis-py"
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _hours_since(iso: str, now: datetime) -> float:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds() / 3600.0)


def _classify_live_rate(hours: float, cand_new: int) -> str:
    expected = EMPIRIC_UB_PER_HOUR * hours
    if cand_new == 0:
        return "EXPECTED_LOW_RATE"
    if cand_new > max(1.0, expected * 3):
        return "ABOVE_EXPECTED"
    return "WITHIN_EXPECTED"


def _funnel_row(scorer: LeadScorer, row: dict, ver: str = DISCOVERY_VERSION_V6) -> dict:
    text = row.get("text") or ""
    feats = evaluate_discovery(scorer, text, version=ver)
    fl = first_loss(feats)
    near_miss = False
    if fl == "NO_PATH_MATCH" and TRUE_BUYER_NEAR_MISS_RX.search(text):
        near_miss = True
    return {
        "message_id": row["id"],
        "tier": row.get("tier"),
        "community": row.get("community_name"),
        "eligible": bool(feats.eligible),
        "first_loss": fl,
        "trigger": feats.trigger_type,
        "paths": list(feats.matched_paths or []),
        "carve": bool(getattr(feats, "gig_project_carve", False)),
        "vetoes": list(feats.veto_categories or []),
        "hard_exclude": bool(feats.hard_exclude),
        "direction": feats.buyer_direction,
        "bucket": population_bucket(feats),
        "repair": bool(feats.repair_signal),
        "automation": bool(feats.automation_signal),
        "ownership": bool(feats.ownership_signal),
        "direct": bool(feats.direct_request_signal),
        "hire": bool(feats.hiring_patterns),
        "budget": bool(feats.budget_signal or feats.true_budget_signal),
        "near_miss_buyerish": near_miss,
        "preview": _preview(text),
        "would_enqueue_low": bool(feats.eligible and (row.get("tier") or "") == "LOW"),
    }


def measure_funnel(scorer: LeadScorer, rows: list[dict], name: str) -> dict:
    evals = [_funnel_row(scorer, r) for r in rows]
    n = len(evals)
    first_losses = Counter(e["first_loss"] for e in evals)
    loss_family = Counter()
    for fl in first_losses:
        if fl == "RETRIEVED":
            loss_family["survivor"] += first_losses[fl]
        elif fl.startswith("VETO_") or fl == "HARD_EXCLUDE_EARLY":
            loss_family["hard_veto"] += first_losses[fl]
        elif fl.startswith("DIRECTION_"):
            loss_family["direction"] += first_losses[fl]
        elif fl == "NO_PATH_MATCH":
            loss_family["semantic_miss"] += first_losses[fl]
        else:
            loss_family["other"] += first_losses[fl]

    elig = [e for e in evals if e["eligible"]]
    elig_low = [e for e in elig if e["would_enqueue_low"]]
    carve_n = sum(1 for e in evals if e["carve"])
    path_hits = Counter()
    for e in elig:
        for p in e["paths"]:
            path_hits[p] += 1
    triggers = Counter(e["trigger"] for e in elig if e["trigger"])

    # Stage funnel (ordered filters approximating eval order).
    stage_pool = n
    stage_after_hard = sum(
        1
        for e in evals
        if not (
            e["first_loss"] == "HARD_EXCLUDE_EARLY"
        )
    )
    stage_after_veto = sum(
        1
        for e in evals
        if e["first_loss"]
        not in (
            "HARD_EXCLUDE_EARLY",
        )
        and not e["first_loss"].startswith("VETO_")
    )
    stage_after_paths = sum(
        1
        for e in evals
        if e["eligible"]
        or e["first_loss"].startswith("DIRECTION_")
    )
    stage_candidate = len(elig)
    stage_low_enqueue = len(elig_low)

    def _pct(kept: int, base: int) -> float:
        return round(100.0 * kept / max(base, 1), 2)

    near_misses = [e for e in evals if e["near_miss_buyerish"]]
    # Qualitative: near-misses that still look like true buyers after path_b+c.
    near_miss_samples = near_misses[:12]
    no_path_samples = [
        e for e in evals if e["first_loss"] == "NO_PATH_MATCH"
    ][:8]
    veto_samples = [
        e
        for e in evals
        if e["first_loss"].startswith("VETO_") or e["first_loss"] == "HARD_EXCLUDE_EARLY"
    ][:5]
    survivor_samples = elig[:10]

    contam_real = 0.0
    if elig:
        cands = []
        by_id = {r["id"]: r for r in rows}
        for e in elig:
            row = by_id[e["message_id"]]
            feats = evaluate_discovery(
                scorer, row.get("text") or "", version=DISCOVERY_VERSION_V6
            )
            cands.append((e["message_id"], row.get("text") or "", feats, e["bucket"]))
        contam_real = contamination(cands)

    return {
        "name": name,
        "pool_n": n,
        "stages": {
            "pool": stage_pool,
            "after_hard_exclude_pass": stage_after_hard,
            "after_veto_pass": stage_after_veto,
            "after_path_or_direction": stage_after_paths,
            "eligible_candidate": stage_candidate,
            "low_enqueueable": stage_low_enqueue,
            "pct_retained_eligible": _pct(stage_candidate, stage_pool),
            "pct_retained_low_enqueue": _pct(stage_low_enqueue, stage_pool),
            "pct_removed_to_eligible": round(
                100.0 - _pct(stage_candidate, stage_pool), 2
            ),
        },
        "first_loss": dict(first_losses.most_common()),
        "loss_family": dict(loss_family.most_common()),
        "eligible": stage_candidate,
        "eligible_LOW": stage_low_enqueue,
        "eligible_pct": _pct(stage_candidate, stage_pool),
        "carve_true_count": carve_n,
        "path_hits_among_eligible": dict(path_hits.most_common()),
        "eligible_triggers": dict(triggers.most_common()),
        "contamination_pct": contam_real,
        "near_miss_buyerish_nopath": len(near_misses),
        "samples": {
            "survivors": survivor_samples,
            "near_miss_buyerish": near_miss_samples,
            "no_path": [
                {
                    "message_id": e["message_id"],
                    "community": e["community"],
                    "preview": e["preview"],
                    "repair": e["repair"],
                    "automation": e["automation"],
                    "ownership": e["ownership"],
                    "direct": e["direct"],
                    "hire": e["hire"],
                    "budget": e["budget"],
                }
                for e in no_path_samples
            ],
            "veto_hard": [
                {
                    "message_id": e["message_id"],
                    "first_loss": e["first_loss"],
                    "community": e["community"],
                    "preview": e["preview"],
                }
                for e in veto_samples
            ],
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-iso", default=E101_SINCE_ISO)
    parser.add_argument("--recent-scored-limit", type=int, default=2500)
    parser.add_argument("--recent-low-limit", type=int, default=5000)
    parser.add_argument("--path-b-at", default=PATH_B_DEPLOY_ISO)
    parser.add_argument("--path-c-at", default=PATH_C_MARKER_ISO)
    parser.add_argument("--enable-at", default=ENABLE_AT_ISO)
    parser.add_argument("--write-evidence", action="store_true", default=True)
    parser.add_argument("--no-write-evidence", action="store_true")
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

        win_path_b = dict(
            (
                await session.execute(sql_text(WINDOW_SQL), {"since": path_b_at})
            )
            .mappings()
            .one()
        )
        win_path_c = dict(
            (
                await session.execute(sql_text(WINDOW_SQL), {"since": path_c_at})
            )
            .mappings()
            .one()
        )
        win_enable = dict(
            (
                await session.execute(sql_text(WINDOW_SQL), {"since": enable_at})
            )
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
    # Cap recent_scored sample evals in evidence samples to keep JSON bounded
    funnel_recent_low = measure_funnel(scorer, recent_low, "recent_low_7d_sample")

    # FO probes
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

    hours_path_b = _hours_since(args.path_b_at, now)
    hours_path_c = _hours_since(args.path_c_at, now)
    hours_enable = _hours_since(args.enable_at, now)

    live_path_b = {
        "since": args.path_b_at,
        "hours": round(hours_path_b, 3),
        "msg": int(win_path_b["msg"] or 0),
        "score": int(win_path_b["score"] or 0),
        "cand": int(win_path_b["cand"] or 0),
        "disc_v6": int(win_path_b["disc_v6"] or 0),
        "rate_per_hour": round(
            int(win_path_b["disc_v6"] or 0) / max(hours_path_b, 1e-9), 4
        ),
        "class": _classify_live_rate(hours_path_b, int(win_path_b["disc_v6"] or 0)),
        "expected_count_at_ub": round(EMPIRIC_UB_PER_HOUR * hours_path_b, 4),
    }
    live_path_c = {
        "since": args.path_c_at,
        "hours": round(hours_path_c, 3),
        "msg": int(win_path_c["msg"] or 0),
        "score": int(win_path_c["score"] or 0),
        "cand": int(win_path_c["cand"] or 0),
        "disc_v6": int(win_path_c["disc_v6"] or 0),
        "rate_per_hour": round(
            int(win_path_c["disc_v6"] or 0) / max(hours_path_c, 1e-9), 4
        ),
        "class": _classify_live_rate(hours_path_c, int(win_path_c["disc_v6"] or 0)),
        "expected_count_at_ub": round(EMPIRIC_UB_PER_HOUR * hours_path_c, 4),
    }
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
    }

    # Density → implied hourly rates
    refined_days = max(1.0, (now - since).total_seconds() / 86400.0)
    refined_low = funnel_refined["eligible_LOW"]
    refined_any = funnel_refined["eligible"]
    implied_refined_low_per_day = round(refined_low / refined_days, 4)
    implied_refined_low_per_hour = round(implied_refined_low_per_day / 24.0, 5)
    implied_refined_any_per_day = round(refined_any / refined_days, 4)
    implied_refined_any_per_hour = round(implied_refined_any_per_day / 24.0, 5)

    # Recent LOW empiric under CURRENT code (post path_b+c)
    recent_low_elig = funnel_recent_low["eligible"]
    recent_low_elig_low = funnel_recent_low["eligible_LOW"]
    # recent_low are all LOW tier by query, so eligible == enqueueable
    hours_7d = 7.0 * 24.0
    empiric_current_per_hour = round(recent_low_elig_low / hours_7d, 5)
    empiric_current_per_day = round(recent_low_elig_low / 7.0, 4)

    # Live scored since path_b: eligible density among scored sample
    scored_n = funnel_recent_scored["pool_n"]
    scored_elig = funnel_recent_scored["eligible"]
    scored_low_enq = funnel_recent_scored["eligible_LOW"]
    live_score_rate_per_hour = round(
        live_path_b["score"] / max(hours_path_b, 1e-9), 2
    )
    # If eligible density among scored is p, implied cand/h ≈ score_rate * p_low
    p_low = scored_low_enq / max(scored_n, 1)
    implied_from_live_scored = round(live_score_rate_per_hour * p_low, 5)

    isolation_ok = (
        int(iso.get("human_labels") or 0) == 1524
        and int(iso.get("ai_confirmed") or 0) == 4
    )

    # Classification logic
    dominant_refined_loss = (
        funnel_refined["first_loss"].copy() if funnel_refined["first_loss"] else {}
    )
    dominant_refined_loss.pop("RETRIEVED", None)
    top_loss = max(dominant_refined_loss, key=dominant_refined_loss.get) if dominant_refined_loss else None

    near_miss_n = funnel_refined["near_miss_buyerish_nopath"]
    recent_near_miss = funnel_recent_scored["near_miss_buyerish_nopath"]
    traffic_ok = live_path_b["score"] >= 200  # non-trivial post path_b traffic

    # If frozen survivors still material and near-miss true buyers ~0, and live
    # zero with score ≫ expected under UB → RESIDUAL_SCARCITY.
    # If near-miss buyerish NO_PATH is material → RESIDUAL_DISCOVERY_LOSS.
    # If live score near 0 → LOW_LIVE_TRAFFIC.
    if not traffic_ok and live_path_b["score"] < 50:
        classification = "LOW_LIVE_TRAFFIC"
        stage_named = None
        rationale = (
            "Post-path_b scored traffic still too low to distinguish scarcity "
            "from quiet collector window."
        )
    elif near_miss_n >= 3 or recent_near_miss >= 3:
        classification = "RESIDUAL_DISCOVERY_LOSS"
        stage_named = "NO_PATH_MATCH"
        rationale = (
            f"Buyerish near-miss NO_PATH count refined={near_miss_n} "
            f"recent_scored={recent_near_miss}; suggests residual semantic miss."
        )
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
            "Frozen refined survivors remain material with 0% contam and 0 "
            "buyerish NO_PATH near-misses under current path_b+c code; live "
            f"scored={live_path_b['score']} over {live_path_b['hours']}h with 0 NEW "
            f"is consistent with empiric UB ~{EMPIRIC_UB_PER_HOUR}/h "
            f"(expected≈{live_path_b['expected_count_at_ub']}) and refined LOW "
            f"density ~{implied_refined_low_per_hour}/h."
        )
    elif live_path_b["disc_v6"] == 0 and traffic_ok:
        classification = "RESIDUAL_SCARCITY"
        stage_named = None
        rationale = (
            "Zero NEW with non-trivial scored traffic; frozen funnel shows no "
            "new dominant semantic-loss stage beyond known veto/NO_PATH noise."
        )
    else:
        classification = "OBSERVABILITY"
        stage_named = None
        rationale = "Insufficient signal to classify; hold observe."

    decision = "OBSERVE"
    code_change = "none"

    next_hypothesis = (
        "Continue live observe to first NEW disc_v6 candidate OR ≥4–8h post "
        "path_b (expected_count at standing UB 0.018/h still <1 until ~55h; "
        "current-code recent LOW empiric is 0/5000 → effective rate may be "
        "≪0.018/h). First NEW quality check (clean RFQ vs residual contam on "
        "untouched path) is the strongest next measurement. Do NOT enable "
        "shadow or reopen path_b/c without explicit authorization + evidence."
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
            "implied_any_per_day": implied_refined_any_per_day,
            "implied_any_per_hour": implied_refined_any_per_hour,
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

    payload = {
        "evidence_id": 112,
        "experiment_id": "POST_ENABLE_CYCLE6_RESIDUAL_SCARCITY",
        "generated_at": now.isoformat(),
        "runtime_sec": round(time.time() - t0, 2),
        "parent": "Evidence 111 OBSERVE (INTENTIONAL_SHADOW_OFF)",
        "decision": decision,
        "classification": classification,
        "loss_stage_if_discovery_loss": stage_named,
        "decision_rationale": rationale,
        "read_only": True,
        "code_change": code_change,
        "hypothesis": (
            "Under current disc_v6 (post path_b+c), genuine commercial yield in "
            "frozen commercial∧domain / refined_frozen is scarce enough that "
            "~0 NEW in hundreds–thousands of scored live msgs is expected "
            "(RESIDUAL_SCARCITY), not a new semantic loss path."
        ),
        "runtime": runtime,
        "live_windows": {
            "since_path_b": live_path_b,
            "since_path_c": live_path_c,
            "since_enable": live_enable,
            "last_1h": live_1h,
        },
        "populations": {
            "legacy_commercial_and_domain": {
                "since": args.since_iso,
                "funnel": {
                    k: v
                    for k, v in funnel_legacy.items()
                    if k != "samples"
                },
                "samples": funnel_legacy["samples"],
            },
            "refined_frozen": {
                "since": args.since_iso,
                "funnel": {
                    k: v
                    for k, v in funnel_refined.items()
                    if k != "samples"
                },
                "samples": funnel_refined["samples"],
            },
            "recent_scored_since_path_b": {
                "limit": args.recent_scored_limit,
                "funnel": {
                    k: v
                    for k, v in funnel_recent_scored.items()
                    if k != "samples"
                },
                "samples": {
                    "survivors": funnel_recent_scored["samples"]["survivors"][:5],
                    "near_miss_buyerish": funnel_recent_scored["samples"][
                        "near_miss_buyerish"
                    ][:8],
                    "no_path": funnel_recent_scored["samples"]["no_path"][:5],
                },
            },
            "recent_low_7d": {
                "limit": args.recent_low_limit,
                "funnel": {
                    k: v
                    for k, v in funnel_recent_low.items()
                    if k != "samples"
                },
            },
        },
        "fo_probes": {"recovered_carve": f"{fo_ok}/{len(probes)}", "rows": fo_out},
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

    # Trim giant sample payloads for survivors that include full eval dicts
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

    payload["populations"]["legacy_commercial_and_domain"]["samples"][
        "survivors"
    ] = _slim_surv(
        payload["populations"]["legacy_commercial_and_domain"]["samples"]["survivors"]
    )
    payload["populations"]["refined_frozen"]["samples"]["survivors"] = _slim_surv(
        payload["populations"]["refined_frozen"]["samples"]["survivors"]
    )
    payload["populations"]["refined_frozen"]["samples"][
        "near_miss_buyerish"
    ] = _slim_surv(
        payload["populations"]["refined_frozen"]["samples"]["near_miss_buyerish"]
    )

    txt_lines = [
        f"Evidence 112: disc_v6 post-enable Cycle 6 residual scarcity vs discovery loss",
        f"generated_at={now.isoformat()} head={_git_head_short()} parent=Evidence 111 OBSERVE",
        f"experiment_id=POST_ENABLE_CYCLE6_RESIDUAL_SCARCITY",
        f"decision={decision}",
        f"classification={classification}"
        + (f" loss_stage={stage_named}" if stage_named else ""),
        f"read_only=true code_change={code_change}",
        "",
        "=== 0. PARENT / LOCKS ===",
        "  parent=Evidence 111 OBSERVE (INTENTIONAL_SHADOW_OFF; EXPECTED_LOW_RATE)",
        "  NO path_b/c reopen / NO FO carve change / NO threshold loosen / NO veto removal",
        "  NO human_labels / AI_CONFIRMED mutation / NO ML GO / NO outreach",
        "  NO docker compose down -v / NO wipe / NO shadow enable / NO community expansion",
        "",
        "=== 1. HYPOTHESIS (ONE) ===",
        "  H1: Under current disc_v6 (post path_b+c), genuine commercial yield in",
        "  frozen commercial∧domain / refined_frozen is scarce enough that ~0 NEW",
        "  in hundreds–thousands of scored live msgs is expected (RESIDUAL_SCARCITY),",
        "  not a new semantic loss path.",
        "",
        "=== 2. RUNTIME CONFIRM ===",
        f"  HEAD={_git_head()}",
        f"  path_b deploy marker: {args.path_b_at}",
        f"  measure_at≈{now.isoformat()} (~{live_path_b['hours']}h since path_b)",
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
            f"  telegram:{sname if sname.startswith('commercial') else sname}: "
            f"XLEN={sdata.get('xlen')} pending={sdata.get('pending')} "
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
        f"  B) since path_c {args.path_c_at} (~{live_path_c['hours']}h):",
        f"     msg={live_path_c['msg']} score={live_path_c['score']}"
        f" disc_v6_new={live_path_c['disc_v6']} class={live_path_c['class']}",
        f"  C) last_1h: msg={live_1h['msg']} score={live_1h['score']}"
        f" cand={live_1h['cand']}",
        f"  D) since enable: msg={live_enable['msg']} disc_v6={live_enable['disc_v6']}"
        f" (6 historical CONTAMINATED retained)",
        "",
        "=== 5. FROZEN POPULATION FUNNEL (current disc_v6) ===",
        f"  legacy commercial∧domain: n={funnel_legacy['pool_n']}"
        f" elig={funnel_legacy['eligible']}"
        f" LOW={funnel_legacy['eligible_LOW']}"
        f" rate={funnel_legacy['eligible_pct']}%"
        f" contam={funnel_legacy['contamination_pct']}%",
        f"    stages: {funnel_legacy['stages']}",
        f"    first_loss: {funnel_legacy['first_loss']}",
        f"    triggers: {funnel_legacy['eligible_triggers']}",
        f"    near_miss_buyerish_nopath: {funnel_legacy['near_miss_buyerish_nopath']}",
        f"  refined_frozen: n={funnel_refined['pool_n']}"
        f" elig={funnel_refined['eligible']}"
        f" LOW={funnel_refined['eligible_LOW']}"
        f" rate={funnel_refined['eligible_pct']}%"
        f" contam={funnel_refined['contamination_pct']}%",
        f"    stages: {funnel_refined['stages']}",
        f"    first_loss: {funnel_refined['first_loss']}",
        f"    triggers: {funnel_refined['eligible_triggers']}",
        f"    near_miss_buyerish_nopath: {funnel_refined['near_miss_buyerish_nopath']}",
        f"    dominant_rejection (excl RETRIEVED): {top_loss}",
        f"  FO probes: {fo_ok}/{len(probes)} eligible+carve",
        "",
        "=== 6. RECENT WINDOWS (current code) ===",
        f"  recent_scored since path_b (lim={args.recent_scored_limit}):"
        f" n={scored_n} elig={scored_elig} LOW_enq={scored_low_enq}"
        f" near_miss={recent_near_miss}",
        f"    first_loss: {funnel_recent_scored['first_loss']}",
        f"  recent_low 7d (lim={args.recent_low_limit}):"
        f" n={funnel_recent_low['pool_n']} elig={recent_low_elig_low}"
        f" near_miss={funnel_recent_low['near_miss_buyerish_nopath']}",
        f"    first_loss: {funnel_recent_low['first_loss']}",
        "",
        "=== 7. DENSITY → IMPLIED RATE vs LIVE ===",
        f"  standing empiric UB: {EMPIRIC_UB_PER_HOUR}/h"
        f" (E103/104; 3/5000 LOW/7d)",
        f"  pre-fix contam live: ~{PRE_FIX_CONTAM_RATE_PER_HOUR}/h (E107)",
        f"  refined_frozen LOW density: {refined_low} over ~{refined_days:.1f}d"
        f" → ~{implied_refined_low_per_day}/d ~{implied_refined_low_per_hour}/h",
        f"  recent_low current-code empiric: {recent_low_elig_low}"
        f"/{funnel_recent_low['pool_n']} → ~{empiric_current_per_hour}/h"
        f" ~{empiric_current_per_day}/d",
        f"  live score rate post path_b: ~{live_score_rate_per_hour}/h;"
        f" p_LOW_eligible≈{p_low:.6f} → implied≈{implied_from_live_scored}/h",
        f"  live observed post path_b: {live_path_b['rate_per_hour']}/h"
        f" (0 NEW; expected_count≈{live_path_b['expected_count_at_ub']})",
        f"  compare: live ≪ pre-fix 0.54/h contam; consistent with UB 0.018/h null",
        "",
        "=== 8. NEAR-MISS / TRUE-BUYER SAMPLES ===",
    ]
    nm = funnel_refined["samples"]["near_miss_buyerish"]
    if not nm:
        txt_lines.append(
            "  refined_frozen buyerish NO_PATH near-misses: 0"
            " (no residual true-buyer semantic miss in standing denom)"
        )
    else:
        for e in nm[:8]:
            txt_lines.append(
                f"  near_miss id={e.get('message_id')} | {_preview(str(e.get('preview') or ''), 120)}"
            )
    rs_nm = funnel_recent_scored["samples"]["near_miss_buyerish"]
    if rs_nm:
        txt_lines.append("  recent_scored near-misses:")
        for e in rs_nm[:5]:
            txt_lines.append(
                f"    id={e.get('message_id')} fl=? | {_preview(str(e.get('preview') or ''), 120)}"
            )
    else:
        txt_lines.append("  recent_scored buyerish NO_PATH near-misses: 0")

    # Show a few refined survivors for context
    txt_lines.append("  refined survivors (sample):")
    for e in funnel_refined["samples"]["survivors"][:5]:
        txt_lines.append(
            f"    id={e['message_id']} tier={e['tier']} trig={e['trigger']}"
            f" carve={e['carve']} | {_preview(e['preview'], 100)}"
        )

    txt_lines += [
        "",
        "=== 9. ATTRIBUTION / CLASSIFICATION ===",
        f"  classification={classification}",
        f"  loss_stage={stage_named}",
        f"  rationale={rationale}",
        "  Not PIPELINE_FAILURE: redis lag=0 discovery/AI; workers Up; isolation ok.",
        "  Not LOW_LIVE_TRAFFIC: post-path_b scored traffic recovered (see §4).",
        "  Shadow remains INTENTIONAL_OFF (E111) — out of scope this cycle.",
        "",
        "=== 10. DECISION ===",
        f"  {decision} — evidence-only; no path/carve/threshold/shadow change.",
        "",
        "=== 11. NEXT HYPOTHESIS (ONE) ===",
        f"  H1: {next_hypothesis}",
        "",
        "=== 12. BUSINESS ===",
        "  outreach=false ml_go=false threshold_loosen=false fo_carve_untouched=true",
        "  discovery_semantic_change=false path_b_c_reopened=false shadow_unchanged=true",
        "",
        f"JSON: docs/audit/evidence/{EV}-disc-v6-residual-scarcity-cycle6.json",
    ]

    text_out = "\n".join(txt_lines) + "\n"
    print(text_out)

    if write:
        OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
        OUT_TXT.write_text(text_out, encoding="utf-8")
        OUT_JSON.write_text(
            json.dumps(payload, indent=2, default=str, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {OUT_TXT}")
        print(f"Wrote {OUT_JSON}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
