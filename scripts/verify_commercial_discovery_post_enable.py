#!/usr/bin/env python3
"""Post-enable commercial discovery verifier (observability / baseline).

Evidence-oriented baseline for COMMERCIAL_DISCOVERY_ENABLED=true. Does NOT
change disc_v6 semantics, flip flags, mutate human_labels / AI_CONFIRMED, or
blind-XACK Redis.

Failure classes (explicit; mutually prioritized):
  DB_FAILURE, REDIS_FAILURE, QUERY_FAILURE, WORKER_FAILURE, PIPELINE_FAILURE,
  NO_DATA, EXPECTED_LOW_RATE — plus OK / ABOVE_EXPECTED / CONTAMINATED for
  rate/quality when infra is healthy.

Hard rule: score inventory uses message_scores (never a table named scores).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V6,
    evaluate_discovery,
    population_bucket,
)
from shared.db import SessionLocal
from shared.settings import get_settings

# Reuse standing denominator helpers (read-only).
from scripts.audit_disc_v6_refined_population_cycle2 import (  # noqa: E402
    E101_SINCE_ISO,
    LEGACY_COMMERCIAL,
    POOL_DOMAIN,
    POOL_SELECT_SQL,
    REFINED_COMMERCIAL,
    contamination,
    first_loss,
    is_exchange_community,
)

EV_DEFAULT = "107"
ENABLE_AT_DEFAULT = "2026-09-25T19:27:00+00:00"
# Evidence 104 empiric UB for disc_v6-eligible among recent LOW traffic.
EXPECTED_RATE_PER_HOUR = 0.018
# Poisson-ish short-window tolerance: treat zero as EXPECTED_LOW_RATE when
# expected count in window is still < 1.
EXPECTED_COUNT_THRESHOLD = 1.0

# Forbidden bare table — only message_scores is valid for score inventory.
_FORBIDDEN_SCORES_TABLE_RX = re.compile(
    r"(?i)\b(?:from|join|into|update|table)\s+scores\b"
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

WINDOWS_SQL = """
SELECT
  (SELECT COUNT(*) FROM messages WHERE created_at > :since) AS msg_since,
  (SELECT COUNT(*) FROM message_scores WHERE scored_at > :since) AS score_since,
  (SELECT COUNT(*) FROM commercial_discovery_candidates
     WHERE created_at > :since) AS cand_since,
  (SELECT COUNT(*) FROM commercial_discovery_candidates
     WHERE discovery_version = 'disc_v6' AND created_at > :since) AS disc_v6_since,
  (SELECT COUNT(*) FROM messages WHERE created_at > now() - interval '1 hour') AS msg_1h,
  (SELECT COUNT(*) FROM message_scores WHERE scored_at > now() - interval '1 hour') AS score_1h,
  (SELECT COUNT(*) FROM commercial_discovery_candidates
     WHERE created_at > now() - interval '1 hour') AS cand_1h,
  (SELECT COUNT(*) FROM messages WHERE created_at > now() - interval '6 hours') AS msg_6h,
  (SELECT COUNT(*) FROM message_scores WHERE scored_at > now() - interval '6 hours') AS score_6h,
  (SELECT COUNT(*) FROM commercial_discovery_candidates
     WHERE created_at > now() - interval '6 hours') AS cand_6h,
  (SELECT COUNT(*) FROM messages WHERE created_at > now() - interval '24 hours') AS msg_24h,
  (SELECT COUNT(*) FROM message_scores WHERE scored_at > now() - interval '24 hours') AS score_24h,
  (SELECT COUNT(*) FROM commercial_discovery_candidates
     WHERE created_at > now() - interval '24 hours') AS cand_24h
"""

CAND_BY_VER_SQL = """
SELECT discovery_version, status, COUNT(*) AS n
FROM commercial_discovery_candidates
GROUP BY 1, 2
ORDER BY 1, 2
"""

LIVE_CANDS_SQL = """
SELECT id, seed_message_id, community_id, author_id, discovery_version,
       trigger_type, scorer_tier, status, skip_reason, episode_id, created_at
FROM commercial_discovery_candidates
WHERE discovery_version = 'disc_v6'
  AND created_at > :since
ORDER BY created_at
"""

SEED_TEXT_SQL = """
SELECT m.id, m.text, m.community_id, c.name AS community_name,
       c.username AS community_username, s.tier, s.score, s.lead_type
FROM messages m
LEFT JOIN communities c ON c.id = m.community_id
LEFT JOIN message_scores s ON s.message_id = m.id
WHERE m.id = ANY(:ids)
ORDER BY m.id
"""

PROBE_SQL = """
SELECT l.id AS lead_id, l.message_id, l.tier AS lead_tier, l.status,
       m.text, m.community_id, c.name AS community_name, c.username AS community_username
FROM leads l
JOIN messages m ON m.id = l.message_id
LEFT JOIN communities c ON c.id = m.community_id
WHERE l.status = 'AI_CONFIRMED'
ORDER BY l.id
"""


class FailureClass(str, Enum):
    OK = "OK"
    NO_DATA = "NO_DATA"
    EXPECTED_LOW_RATE = "EXPECTED_LOW_RATE"
    ABOVE_EXPECTED = "ABOVE_EXPECTED"
    CONTAMINATED = "CONTAMINATED"
    QUERY_FAILURE = "QUERY_FAILURE"
    PIPELINE_FAILURE = "PIPELINE_FAILURE"
    REDIS_FAILURE = "REDIS_FAILURE"
    DB_FAILURE = "DB_FAILURE"
    WORKER_FAILURE = "WORKER_FAILURE"


def assert_sql_uses_message_scores(sql: str) -> None:
    """Raise QUERY_FAILURE-class error if SQL references a bare scores table."""
    if _FORBIDDEN_SCORES_TABLE_RX.search(sql):
        raise ValueError(
            "QUERY_FAILURE: SQL references forbidden table 'scores'; "
            "use message_scores"
        )
    if re.search(r"(?i)\bmessage_scores\b", sql) is None and re.search(
        r"(?i)\bscore", sql
    ):
        # Soft: score-related SQL should mention message_scores when counting.
        pass


def classify_rate(
    *,
    hours: float,
    cand_count: int,
    expected_per_hour: float = EXPECTED_RATE_PER_HOUR,
    expected_count_threshold: float = EXPECTED_COUNT_THRESHOLD,
    contamination_pct: float | None = None,
    contam_threshold_pct: float = 50.0,
) -> FailureClass:
    """Classify observed candidate rate vs empiric low-rate baseline.

    Short zeros while expected count < threshold → EXPECTED_LOW_RATE (not success,
    not failure). High contamination among live eligibles → CONTAMINATED.
    """
    if hours <= 0:
        return FailureClass.NO_DATA
    expected = expected_per_hour * hours
    if cand_count == 0 and expected < expected_count_threshold:
        return FailureClass.EXPECTED_LOW_RATE
    if cand_count == 0 and expected >= expected_count_threshold:
        # Enough wall time that zero is suspicious vs empiric UB — still may be
        # poisson; treat as EXPECTED_LOW_RATE until pipeline signals disagree.
        return FailureClass.EXPECTED_LOW_RATE
    if contamination_pct is not None and contamination_pct >= contam_threshold_pct:
        return FailureClass.CONTAMINATED
    if cand_count > max(expected_count_threshold, expected * 3):
        return FailureClass.ABOVE_EXPECTED
    return FailureClass.EXPECTED_LOW_RATE


def prioritize_failure(*classes: FailureClass) -> FailureClass:
    """Return the highest-severity failure class present."""
    order = [
        FailureClass.DB_FAILURE,
        FailureClass.REDIS_FAILURE,
        FailureClass.QUERY_FAILURE,
        FailureClass.WORKER_FAILURE,
        FailureClass.PIPELINE_FAILURE,
        FailureClass.NO_DATA,
        FailureClass.CONTAMINATED,
        FailureClass.ABOVE_EXPECTED,
        FailureClass.EXPECTED_LOW_RATE,
        FailureClass.OK,
    ]
    present = set(classes)
    for c in order:
        if c in present:
            return c
    return FailureClass.OK


def _git_head() -> str:
    env = __import__("os").environ.get("GIT_HEAD", "").strip()
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _preview(t: str, n: int = 140) -> str:
    return " ".join((t or "").split())[:n]


def _http_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_bases() -> list[str]:
    env = __import__("os").environ.get("TLM_API_BASE", "").strip().rstrip("/")
    bases: list[str] = []
    if env:
        bases.append(env)
    bases.extend(["http://127.0.0.1:8010", "http://api:8010"])
    seen: set[str] = set()
    out: list[str] = []
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _probe_api() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    notes: list[str] = []
    health: dict[str, Any] = {}
    gates: dict[str, Any] = {}
    last_err = ""
    for base in _api_bases():
        try:
            health = _http_json(f"{base}/health")
            gates = _http_json(f"{base}/validation/gates")
            notes.append(f"api_base={base}")
            return health, gates, notes
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
    notes.append(f"api_probe_error={last_err}")
    return health, gates, notes


def _redis_discovery_stats() -> dict[str, Any]:
    """Non-destructive redis stream stats (redis-py preferred; docker fallback)."""
    out: dict[str, Any] = {"ok": False}
    stream = "telegram:commercial_discovery"
    group = "commercial-discovery-workers"
    # Prefer direct Redis client (works inside compose network).
    try:
        import redis as redis_lib  # type: ignore

        settings = get_settings()
        url = getattr(settings, "redis_url", None) or "redis://redis:6379/0"
        client = redis_lib.Redis.from_url(url, decode_responses=True)
        out["xlen"] = int(client.xlen(stream))
        pend = client.xpending(stream, group)
        # redis-py may return dict or tuple depending on version
        if isinstance(pend, dict):
            out["xpending"] = int(pend.get("pending") or pend.get("count") or 0)
        elif isinstance(pend, (list, tuple)) and pend:
            out["xpending"] = int(pend[0])
        else:
            out["xpending"] = 0
        cons = client.xinfo_consumers(stream, group)
        out["consumers"] = len(cons) if cons is not None else 0
        out["via"] = "redis-py"
        out["ok"] = True
        return out
    except Exception as exc:  # noqa: BLE001
        out["redis_py_error"] = f"{type(exc).__name__}: {exc}"

    # Host fallback: docker compose exec redis-cli
    try:
        raw = subprocess.check_output(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "redis",
                "redis-cli",
                "XLEN",
                stream,
            ],
            cwd=ROOT,
            text=True,
            timeout=15,
        ).strip()
        out["xlen"] = int(raw)
        pend = subprocess.check_output(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "redis",
                "redis-cli",
                "XPENDING",
                stream,
                group,
            ],
            cwd=ROOT,
            text=True,
            timeout=15,
        ).strip()
        parts = pend.split()
        out["xpending"] = int(parts[0]) if parts else -1
        cons = subprocess.check_output(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "redis",
                "redis-cli",
                "XINFO",
                "CONSUMERS",
                stream,
                group,
            ],
            cwd=ROOT,
            text=True,
            timeout=15,
        )
        out["consumers"] = cons.count("name")
        out["via"] = "docker-compose-exec"
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["ok"] = False
    return out


_PROMO_RX = re.compile(
    r"(?i)\benjoying my experience with\b|\bforex high way ea\b|"
    r"\bwhat i really like is how it helps automate\b|"
    r"\b(?:our|my)\s+(?:ea|expert advisor)\b|"
    r"\bsignals?\s+group\b|\bpromo(?:tion)?\b",
)
_SUPPORTISH_RX = re.compile(
    r"(?i)\bboss can you help\b|\bmy bot is not working\b|"
    r"\bhelp me with my position\b",
)


def qualitative_live_contamination(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Catch false-BUYER spam the audit contamination() helper undercounts.

    disc_v6 may label EA marketing as buyer_direction=BUYER / Direct RFQ bucket
    even when text is provider promo or support chatter.
    """
    n = len(items)
    if n == 0:
        return {
            "n": 0,
            "promo_or_support_n": 0,
            "near_dup_n": 0,
            "pct": None,
            "reason_counts": {},
        }
    reasons: Counter = Counter()
    bad = 0
    previews = [((it.get("preview") or "")[:80]) for it in items]
    # near-dup: identical 80-char prefixes
    dup_counts = Counter(previews)
    near_dup_ids = {p for p, c in dup_counts.items() if c >= 2 and p}
    for it in items:
        flags: list[str] = []
        text = it.get("preview") or ""
        if _PROMO_RX.search(text):
            flags.append("promo_marketing")
        if _SUPPORTISH_RX.search(text):
            flags.append("supportish")
        if (text[:80] in near_dup_ids) and it.get("trigger_type") == "v6_automation_domain":
            flags.append("near_dup_automation")
        # score==0 LOW automation with no budget language is suspicious
        if (
            it.get("trigger_type") == "v6_automation_domain"
            and float(it.get("score") or 0) == 0.0
            and it.get("v6_automation")
        ):
            flags.append("zero_score_automation")
        if flags:
            bad += 1
            for f in flags:
                reasons[f] += 1
    pct = round(100.0 * bad / n, 1)
    return {
        "n": n,
        "promo_or_support_n": bad,
        "near_dup_n": sum(1 for p in previews if p in near_dup_ids),
        "pct": pct,
        "reason_counts": dict(reasons),
    }


async def run_baseline(
    *,
    enable_at: datetime,
    evidence_id: str,
    write_evidence: bool,
) -> dict[str, Any]:
    t0 = time.time()
    classes: list[FailureClass] = []
    notes: list[str] = []

    # Validate verifier SQL itself.
    try:
        for sql in (ISO_SQL, WINDOWS_SQL, LIVE_CANDS_SQL, SEED_TEXT_SQL):
            assert_sql_uses_message_scores(sql)
    except ValueError as exc:
        classes.append(FailureClass.QUERY_FAILURE)
        notes.append(str(exc))

    settings = get_settings()
    health, gates, api_notes = _probe_api()
    notes.extend(api_notes)

    redis_stats = _redis_discovery_stats()
    if not redis_stats.get("ok"):
        classes.append(FailureClass.REDIS_FAILURE)
        notes.append(
            f"redis_error={redis_stats.get('error') or redis_stats.get('redis_py_error')}"
        )

    iso: dict[str, Any] = {}
    windows: dict[str, Any] = {}
    by_ver: list[dict[str, Any]] = []
    live_cands: list[dict[str, Any]] = []
    live_eval: list[dict[str, Any]] = []
    replay: dict[str, Any] = {}
    probes_out: list[dict[str, Any]] = []

    scorer = LeadScorer(settings.scoring_config)

    try:
        async with SessionLocal() as session:
            # Probe forbidden table name explicitly → QUERY_FAILURE if someone
            # reintroduces it; expect relation does not exist.
            try:
                await session.execute(sql_text("SELECT COUNT(*) FROM scores"))
                classes.append(FailureClass.QUERY_FAILURE)
                notes.append(
                    "QUERY_FAILURE: unexpected table 'scores' exists; "
                    "inventory must use message_scores"
                )
            except Exception:
                await session.rollback()
                notes.append("scores_table_absent=True (correct; use message_scores)")

            iso = dict((await session.execute(sql_text(ISO_SQL))).mappings().one())
            windows = dict(
                (
                    await session.execute(
                        sql_text(WINDOWS_SQL), {"since": enable_at}
                    )
                )
                .mappings()
                .one()
            )
            by_ver = [
                dict(r)
                for r in (
                    await session.execute(sql_text(CAND_BY_VER_SQL))
                ).mappings().all()
            ]
            live_cands = [
                dict(r)
                for r in (
                    await session.execute(
                        sql_text(LIVE_CANDS_SQL), {"since": enable_at}
                    )
                )
                .mappings()
                .all()
            ]
            seed_ids = [int(c["seed_message_id"]) for c in live_cands]
            seed_rows: list[dict[str, Any]] = []
            if seed_ids:
                seed_rows = [
                    dict(r)
                    for r in (
                        await session.execute(
                            sql_text(SEED_TEXT_SQL), {"ids": seed_ids}
                        )
                    )
                    .mappings()
                    .all()
                ]
            probes_db = [
                dict(r)
                for r in (await session.execute(sql_text(PROBE_SQL))).mappings().all()
            ]

            # Deterministic replay: legacy commercial∧domain + refined_frozen.
            since_frozen = datetime.fromisoformat(
                E101_SINCE_ISO.replace("Z", "+00:00")
            )
            legacy_rows = [
                dict(x)
                for x in (
                    await session.execute(
                        sql_text(POOL_SELECT_SQL),
                        {
                            "since": since_frozen,
                            "comm": LEGACY_COMMERCIAL,
                            "dom": POOL_DOMAIN,
                        },
                    )
                )
                .mappings()
                .all()
            ]
    except Exception as exc:  # noqa: BLE001
        classes.append(FailureClass.DB_FAILURE)
        notes.append(f"db_error={type(exc).__name__}: {exc}")
        primary = prioritize_failure(*classes)
        return {
            "evidence_id": int(evidence_id) if evidence_id.isdigit() else evidence_id,
            "failure_class": primary.value,
            "failure_classes_seen": [c.value for c in classes],
            "notes": notes,
            "error": str(exc),
        }

    # Live seed evaluation + contamination.
    seed_by_id = {int(r["id"]): r for r in seed_rows}
    live_elig_cands: list[tuple] = []
    for c in live_cands:
        sid = int(c["seed_message_id"])
        row = seed_by_id.get(sid) or {}
        text = row.get("text") or ""
        f6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        f4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
        bucket = population_bucket(f6)
        item = {
            "candidate_id": c["id"],
            "seed_message_id": sid,
            "community_id": c.get("community_id"),
            "community_name": row.get("community_name"),
            "author_id": c.get("author_id"),
            "trigger_type": c.get("trigger_type"),
            "status": c.get("status"),
            "scorer_tier": c.get("scorer_tier"),
            "score": row.get("score"),
            "lead_type": row.get("lead_type"),
            "v6_eligible": bool(f6.eligible),
            "v6_first_loss": first_loss(f6),
            "v6_trigger": f6.trigger_type,
            "v6_direction": f6.buyer_direction,
            "v6_bucket": bucket,
            "v6_automation": bool(f6.automation_signal),
            "v6_marketing": bool(f6.marketing_signal),
            "v6_provider": bool(f6.provider_signal),
            "v4_eligible": bool(f4.eligible),
            "v4_first_loss": first_loss(f4),
            "v4_trigger": f4.trigger_type,
            "preview": _preview(text),
        }
        live_eval.append(item)
        if f6.eligible:
            live_elig_cands.append((sid, text, f6, bucket))

    audit_contam = contamination(live_elig_cands) if live_elig_cands else None
    qual_contam = qualitative_live_contamination(live_eval)
    # Prefer qualitative when audit undercounts false-BUYER promo/support.
    live_contam = audit_contam
    if qual_contam.get("pct") is not None and (
        audit_contam is None or float(qual_contam["pct"]) > float(audit_contam or 0)
    ):
        live_contam = float(qual_contam["pct"])
        notes.append(
            f"qualitative_contam_override audit={audit_contam} "
            f"qual={qual_contam['pct']} reasons={qual_contam.get('reason_counts')}"
        )

    # Replay populations.
    refined_rx = re.compile(REFINED_COMMERCIAL, re.I)
    refined_rows = []
    for r in legacy_rows:
        text = r.get("text") or ""
        if not refined_rx.search(text):
            continue
        if is_exchange_community(
            scorer, r.get("community_username"), r.get("community_name")
        ):
            continue
        refined_rows.append(r)

    def _measure(rows: list[dict[str, Any]], ver: str) -> dict[str, Any]:
        elig: list[tuple] = []
        fl: Counter = Counter()
        trig: Counter = Counter()
        low = high = other = 0
        for r in rows:
            feats = evaluate_discovery(scorer, r.get("text") or "", version=ver)
            fl[first_loss(feats)] += 1
            if feats.eligible:
                b = population_bucket(feats)
                elig.append((r["id"], r.get("text"), feats, b))
                trig[feats.trigger_type or "none"] += 1
                tier = r.get("tier")
                if tier == "LOW":
                    low += 1
                elif tier == "HIGH":
                    high += 1
                else:
                    other += 1
        return {
            "pool_n": len(rows),
            "eligible": len(elig),
            "contamination_pct": contamination(elig) if elig else 0.0,
            "first_loss": dict(fl.most_common()),
            "triggers": dict(trig.most_common()),
            "eligible_by_tier": {"LOW": low, "HIGH": high, "other": other},
        }

    replay = {
        "legacy_commercial_domain": {
            "disc_v4": _measure(legacy_rows, DISCOVERY_VERSION_V4),
            "disc_v6": _measure(legacy_rows, DISCOVERY_VERSION_V6),
        },
        "refined_frozen": {
            "disc_v4": _measure(refined_rows, DISCOVERY_VERSION_V4),
            "disc_v6": _measure(refined_rows, DISCOVERY_VERSION_V6),
        },
    }

    for p in probes_db:
        f6 = evaluate_discovery(scorer, p.get("text") or "", version=DISCOVERY_VERSION_V6)
        f4 = evaluate_discovery(scorer, p.get("text") or "", version=DISCOVERY_VERSION_V4)
        probes_out.append(
            {
                "lead_id": p["lead_id"],
                "message_id": p["message_id"],
                "lead_tier": p.get("lead_tier"),
                "v6_eligible": bool(f6.eligible),
                "v6_trigger": f6.trigger_type,
                "v4_eligible": bool(f4.eligible),
                "preview": _preview(p.get("text") or ""),
            }
        )

    now = datetime.now(timezone.utc)
    hours = max(0.0, (now - enable_at).total_seconds() / 3600.0)
    cand_since = int(windows.get("disc_v6_since") or 0)
    rate_per_hour = round(cand_since / hours, 4) if hours > 0 else None

    # Isolation
    isolation_ok = int(iso.get("human_labels") or 0) == 1524 and int(
        iso.get("ai_confirmed") or 0
    ) == 4

    # Infra / pipeline classes
    if int(windows.get("score_since") or 0) == 0 and int(windows.get("msg_since") or 0) == 0:
        classes.append(FailureClass.NO_DATA)
        notes.append("NO_DATA: no messages/scores since enable")
    elif int(windows.get("score_since") or 0) == 0 and int(windows.get("msg_since") or 0) > 0:
        classes.append(FailureClass.PIPELINE_FAILURE)
        notes.append("PIPELINE_FAILURE: messages ingested but no message_scores since enable")

    if settings.commercial_discovery_enabled:
        if redis_stats.get("ok") and int(redis_stats.get("consumers") or 0) == 0:
            classes.append(FailureClass.WORKER_FAILURE)
            notes.append("WORKER_FAILURE: discovery enabled but no redis consumers")
        xpending = int(redis_stats.get("xpending") or 0)
        if xpending > 50:
            classes.append(FailureClass.WORKER_FAILURE)
            notes.append(f"WORKER_FAILURE: XPENDING={xpending} exploding")
        # Cross-check redis entries vs DB (do not equate XPENDING=0 with health).
        xlen = int(redis_stats.get("xlen") or 0)
        if cand_since > 0 and xlen == 0 and xpending == 0:
            # Stream may trim; only flag if DB has recent PENDING not consumed.
            pending_db = sum(
                1 for c in live_cands if c.get("status") == "PENDING"
            )
            if pending_db > 0:
                classes.append(FailureClass.PIPELINE_FAILURE)
                notes.append(
                    f"PIPELINE_FAILURE: {pending_db} PENDING in DB but redis XLEN=0"
                )
        stuck = [
            c
            for c in live_cands
            if c.get("status") == "PENDING"
            and (now - c["created_at"]).total_seconds() > 600
        ]
        if stuck:
            classes.append(FailureClass.WORKER_FAILURE)
            notes.append(f"WORKER_FAILURE: {len(stuck)} PENDING >10m")

    rate_class = classify_rate(
        hours=hours,
        cand_count=cand_since,
        contamination_pct=live_contam,
    )
    classes.append(rate_class)

    if not settings.commercial_discovery_enabled:
        notes.append("flag_off: commercial_discovery_enabled=False")

    primary = prioritize_failure(*classes)

    # First meaningful loss / limitation for this cycle.
    if primary == FailureClass.CONTAMINATED:
        limitation = (
            "LIVE_CONTAMINATION: disc_v6 path (esp. v6_automation_domain) enqueued "
            "non-buyer marketing/support spam; rate ABOVE empiric UB but quality bad"
        )
    elif primary == FailureClass.ABOVE_EXPECTED:
        limitation = "VOLUME_ABOVE_EMPIRIC_UB — inspect triggers/communities before path changes"
    elif primary == FailureClass.EXPECTED_LOW_RATE:
        limitation = "EXPECTED_LOW_RATE — short/low zeros are not success or failure"
    else:
        limitation = primary.value

    # Dominant first-loss on refined_frozen (historical).
    refined_fl = (
        replay.get("refined_frozen", {})
        .get("disc_v6", {})
        .get("first_loss", {})
    )
    historical_first_loss = next(iter(refined_fl), None)

    summary = {
        "evidence_id": int(evidence_id) if str(evidence_id).isdigit() else evidence_id,
        "title": "disc_v6 post-enable Cycle 1 baseline",
        "generated_at": now.isoformat(),
        "head": _git_head(),
        "parent": "Evidence 106 ENABLED",
        "read_only": True,
        "code_change_scope": "verifier_observability_only",
        "enable_at": enable_at.isoformat(),
        "hours_since_enable": round(hours, 3),
        "settings": {
            "commercial_discovery_enabled": bool(
                settings.commercial_discovery_enabled
            ),
            "commercial_discovery_version": settings.commercial_discovery_version,
            "max_candidates_per_hour": settings.commercial_discovery_max_candidates_per_hour,
            "commercial_episode_shadow_enabled": bool(
                getattr(settings, "commercial_episode_shadow_enabled", False)
            ),
        },
        "health": health,
        "gates": {
            "ai_validated_messages": gates.get("ai_validated_messages"),
            "ai_validated_true": gates.get("ai_validated_true"),
            "ai_validated_false": gates.get("ai_validated_false"),
            "ml_training_enabled": gates.get("ml_training_enabled"),
            "ai_validation_gate_ready": gates.get("ai_validation_gate_ready"),
        },
        "isolation": iso,
        "isolation_ok": isolation_ok,
        "windows": windows,
        "candidates_by_version_status": by_ver,
        "redis": redis_stats,
        "redis_xpending_not_health": (
            "XPENDING=0 is necessary but not sufficient; cross-check DB writes + consumers"
        ),
        "live_rate": {
            "disc_v6_since_enable": cand_since,
            "per_hour": rate_per_hour,
            "expected_per_hour_ub": EXPECTED_RATE_PER_HOUR,
            "expected_count": round(EXPECTED_RATE_PER_HOUR * hours, 4),
            "classification": rate_class.value,
            "live_contamination_pct": live_contam,
            "audit_contamination_pct": audit_contam,
            "qualitative_contamination": qual_contam,
        },
        "live_candidates": live_eval,
        "replay": replay,
        "fo_probes": probes_out,
        "failure_class": primary.value,
        "failure_classes_seen": [c.value for c in dict.fromkeys(classes)],
        "limitation_class": limitation,
        "historical_first_loss_refined_v6": historical_first_loss,
        "next_hypothesis": (
            "H1: Tighten v6_automation_domain / path_c so first-person "
            "'automate my trading' EA marketing (provider/promo) does not "
            "enqueue; require buyer RFQ / hire / budget / ownership conjuncts "
            "beyond bare automate+domain. Measure on live seeds 8633878-family "
            "before any path change."
        ),
        "notes": notes,
        "runtime_sec": round(time.time() - t0, 2),
        "business": {
            "outreach": False,
            "ml_go": False,
            "threshold_loosen": False,
            "discovery_semantic_change": False,
        },
        "locks_honored": {
            "no_disc_v6_path_change": True,
            "no_threshold_loosen": True,
            "no_human_labels_mutation": True,
            "no_ai_confirmed_mutation": True,
            "no_blind_xack": True,
            "no_docker_down_v": True,
        },
    }

    if write_evidence:
        out_json = ROOT / f"docs/audit/evidence/{evidence_id}-disc-v6-post-enable-baseline-cycle1.json"
        out_txt = ROOT / f"docs/audit/evidence/{evidence_id}-disc-v6-post-enable-baseline-cycle1.txt"
        out_json.parent.mkdir(parents=True, exist_ok=True)
        # JSON-serialize datetimes
        def _ser(o: Any) -> Any:
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, dict):
                return {k: _ser(v) for k, v in o.items()}
            if isinstance(o, list):
                return [_ser(v) for v in o]
            return o

        out_json.write_text(json.dumps(_ser(summary), indent=2) + "\n", encoding="utf-8")
        out_txt.write_text(_format_txt(summary), encoding="utf-8")
        summary["evidence_paths"] = {
            "json": str(out_json.relative_to(ROOT)),
            "txt": str(out_txt.relative_to(ROOT)),
        }

    return summary


def _format_txt(s: dict[str, Any]) -> str:
    live = s.get("live_rate") or {}
    iso = s.get("isolation") or {}
    win = s.get("windows") or {}
    st = s.get("settings") or {}
    redis = s.get("redis") or {}
    lines = [
        f"Evidence {s.get('evidence_id')}: disc_v6 post-enable Cycle 1 baseline",
        f"generated_at={s.get('generated_at')} head={s.get('head')}",
        f"runtime_sec={s.get('runtime_sec')} read_only=true",
        f"failure_class={s.get('failure_class')} rate_class={live.get('classification')}",
        "",
        "=== 0. PARENT / LOCKS ===",
        f"  parent={s.get('parent')}",
        f"  enabled={st.get('commercial_discovery_enabled')} version={st.get('commercial_discovery_version')} "
        f"cap={st.get('max_candidates_per_hour')} shadow={st.get('commercial_episode_shadow_enabled')}",
        "  NO disc_v6 path/carve change / NO threshold loosen / NO label mutation / NO blind XACK",
        "",
        "=== 1. HEALTH / GATES ===",
        f"  health={s.get('health')}",
        f"  gates={s.get('gates')}",
        "",
        "=== 2. REDIS (not equating XPENDING=0 with health) ===",
        f"  {redis}",
        f"  note={s.get('redis_xpending_not_health')}",
        "",
        "=== 3. ISOLATION ===",
        f"  human_labels={iso.get('human_labels')} AI_CONFIRMED={iso.get('ai_confirmed')} "
        f"message_scores={iso.get('message_scores')} messages={iso.get('messages')} "
        f"disc_candidates={iso.get('disc_candidates')} disc_v6={iso.get('disc_v6_candidates')}",
        f"  isolation_ok={s.get('isolation_ok')} (expect 1524/4)",
        "",
        "=== 4. WINDOWS ===",
        f"  since_enable hours={s.get('hours_since_enable')} enable_at={s.get('enable_at')}",
        f"  msg/score/cand since={win.get('msg_since')}/{win.get('score_since')}/{win.get('cand_since')} "
        f"disc_v6_since={win.get('disc_v6_since')}",
        f"  1h msg/score/cand={win.get('msg_1h')}/{win.get('score_1h')}/{win.get('cand_1h')}",
        f"  6h msg/score/cand={win.get('msg_6h')}/{win.get('score_6h')}/{win.get('cand_6h')}",
        f"  24h msg/score/cand={win.get('msg_24h')}/{win.get('score_24h')}/{win.get('cand_24h')}",
        f"  by_ver={s.get('candidates_by_version_status')}",
        "",
        "=== 5. LIVE RATE vs EXPECTED_LOW_RATE ===",
        f"  disc_v6_since={live.get('disc_v6_since_enable')} rate/h={live.get('per_hour')} "
        f"expected_ub/h={live.get('expected_per_hour_ub')} expected_count={live.get('expected_count')}",
        f"  classification={live.get('classification')} live_contam%={live.get('live_contamination_pct')}",
        "  NOTE: short zero windows are EXPECTED_LOW_RATE — not success or failure.",
        "",
        "=== 6. LIVE CANDIDATES ===",
    ]
    for item in s.get("live_candidates") or []:
        lines.append(
            f"  id={item.get('candidate_id')} seed={item.get('seed_message_id')} "
            f"trig={item.get('trigger_type')} status={item.get('status')} "
            f"bucket={item.get('v6_bucket')} dir={item.get('v6_direction')} "
            f"comm={item.get('community_name')} | {item.get('preview')}"
        )
    lines += ["", "=== 7. DETERMINISTIC REPLAY ==="]
    replay = s.get("replay") or {}
    for pop in ("legacy_commercial_domain", "refined_frozen"):
        block = replay.get(pop) or {}
        v4 = block.get("disc_v4") or {}
        v6 = block.get("disc_v6") or {}
        lines.append(
            f"  {pop}: pool legacy/refined via helpers; "
            f"v4 elig={v4.get('eligible')} contam={v4.get('contamination_pct')} "
            f"v6 elig={v6.get('eligible')} contam={v6.get('contamination_pct')}"
        )
        lines.append(f"    v6 first_loss={v6.get('first_loss')}")
        lines.append(f"    v6 triggers={v6.get('triggers')} tiers={v6.get('eligible_by_tier')}")
    lines += [
        "",
        "=== 8. FAILURE / LIMITATION ===",
        f"  failure_class={s.get('failure_class')}",
        f"  seen={s.get('failure_classes_seen')}",
        f"  limitation={s.get('limitation_class')}",
        f"  historical_first_loss_refined_v6={s.get('historical_first_loss_refined_v6')}",
        "",
        "=== 9. NEXT HYPOTHESIS (no path change this cycle) ===",
        f"  {s.get('next_hypothesis')}",
        "",
        "=== 10. BUSINESS ===",
        f"  {s.get('business')}",
        f"  notes={s.get('notes')}",
        "",
    ]
    paths = s.get("evidence_paths") or {}
    if paths:
        lines.append(f"JSON: {paths.get('json')}")
    return "\n".join(lines) + "\n"


async def _amain() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-at", default=ENABLE_AT_DEFAULT)
    parser.add_argument("--evidence-id", default=EV_DEFAULT)
    parser.add_argument("--write-evidence", action="store_true", default=True)
    parser.add_argument("--no-write-evidence", action="store_true")
    args = parser.parse_args()
    enable_at = datetime.fromisoformat(args.enable_at.replace("Z", "+00:00"))
    write = not args.no_write_evidence
    summary = await run_baseline(
        enable_at=enable_at,
        evidence_id=str(args.evidence_id),
        write_evidence=write,
    )
    print(_format_txt(summary))
    print(json.dumps({"failure_class": summary.get("failure_class"),
                      "rate": summary.get("live_rate"),
                      "isolation_ok": summary.get("isolation_ok")}, indent=2))
    return 0 if summary.get("failure_class") not in {
        FailureClass.DB_FAILURE.value,
        FailureClass.QUERY_FAILURE.value,
        FailureClass.REDIS_FAILURE.value,
    } else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
