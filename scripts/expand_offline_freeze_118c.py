#!/usr/bin/env python3
"""Evidence 118c / WP2c — Further freeze expand beyond WP2b (read-only).

Mode 1 read-only. Does NOT write message_scores, candidates, labels, or flags.
Does NOT overwrite Evidence 118 artifacts.

Selection policy (pre-registered):
  - Different modular sample (stride/offset) than E118 to harvest new IDs.
  - Prefer community_class NOT IN (JOB_BOARD, EXCHANGE_OFFICIAL) when filling freezes.
  - Prefer NO_PATH_MATCH near-miss over veto/job-aggregator for scientific power.
  - Exclude already-adjudicated E118 WP2 message_ids.
  - Cap freezes; do not invent volume via threshold loosening.

Run:
  docker compose run --rm --no-deps -v \"$PWD:/app\" -w /app analyzer \\
    sh -c 'PYTHONPATH=/app python scripts/expand_offline_freeze_118c.py'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
from collections import Counter
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

EV = "118c"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-offline-freeze-expand.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-offline-freeze-expand.json"
OUT_FREEZE = ROOT / f"docs/audit/evidence/{EV}-offline-freeze-samples.json"

PATH_B_AT = datetime.fromisoformat("2026-09-26T06:53:16+00:00")
EXPECT_HL = 1524
EXPECT_AC = 4
DEPRIORITIZED = frozenset({"JOB_BOARD", "EXCHANGE_OFFICIAL"})

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
  (SELECT COUNT(*) FROM messages) AS messages
"""

SAMPLE_SQL = """
SELECT m.id, m.text, m.message_date, m.community_id,
       c.name AS community_name, c.username AS community_username
FROM messages m
LEFT JOIN communities c ON c.id = m.community_id
WHERE m.message_date >= :lo AND m.message_date < :hi
  AND m.text IS NOT NULL AND length(m.text) >= 20
  AND (m.id % :stride) = :offset
ORDER BY m.message_date DESC, m.id DESC
LIMIT :lim
"""


def _git_head() -> str:
    import os

    env = (os.environ.get("GIT_HEAD") or "").strip()
    if env:
        return env
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


PRIOR_FREEZES = (
    ROOT / "docs/audit/evidence/118-offline-freeze-samples.json",
    ROOT / "docs/audit/evidence/118b-offline-freeze-samples.json",
)


def _excluded_ids() -> set[int]:
    """Exclude ALL prior WP2+WP2b freeze IDs (24), not only E118's 8."""
    out: set[int] = set()
    keys = ("nopath_near_miss", "veto_near_or_refined", "eligible_retrieved", "eligible_non_low")
    for path in PRIOR_FREEZES:
        if not path.exists():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        for key in keys:
            for item in doc.get(key) or []:
                out.add(int(item["message_id"]))
    return out


def _rank_key(item: dict[str, Any]) -> tuple[int, int, str]:
    """Lower is better: prefer non-deprioritized, then nearer buckets, then id."""
    cclass = str(item.get("community_class") or "")
    depri = 2 if cclass in DEPRIORITIZED else 0  # stronger demotion of JOB_BOARD/EXCHANGE
    bucket_rank = {"0_7d": 0, "8_30d": 1, "31_90d": 2, "91d_plus": 3}.get(
        str(item.get("bucket")), 9
    )
    return (depri, bucket_rank, str(item.get("message_id")))


def _select_ranked(candidates: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    ranked = sorted(candidates, key=_rank_key)
    return ranked[:cap]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Different modular sample than E118 (stride=97, offset=3).
    ap.add_argument("--per-bucket", type=int, default=2500)
    ap.add_argument("--freeze-cap-nopath", type=int, default=40)
    ap.add_argument("--freeze-cap-veto", type=int, default=40)
    ap.add_argument("--stride", type=int, default=83)
    # Multiple offsets ≠ E118 (97/3) to expand power without threshold loosening.
    ap.add_argument(
        "--offsets",
        default="7,19,37,53",
        help="Comma-separated modular offsets (default: 7,19,37,53)",
    )
    ap.add_argument("--statement-timeout-ms", type=int, default=180000)
    args = ap.parse_args()
    offsets = [int(x.strip()) for x in str(args.offsets).split(",") if x.strip()]
    if not offsets:
        print("No offsets", file=sys.stderr)
        return 2

    t0 = time.time()
    now = datetime.now(timezone.utc)
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    head = _git_head()
    refined_rx = re.compile(REFINED_COMMERCIAL, re.I)
    excluded = _excluded_ids()

    pre_registered = {
        "hypothesis": (
            "E118 freeze n=8 was contamination-dominant (job-board/vendor). "
            "A different messages-only sample with preferential non-JOB_BOARD / "
            "non-EXCHANGE_OFFICIAL selection may surface genuine buyer near-misses "
            "or confirm contamination dominance."
        ),
        "decision_rule": (
            "After adjudication of NEW freeze IDs: if genuine_buyer_project share "
            "<15% AND commercially_actionable true=0 → CONTAMINATION_DOMINANT / "
            "lean sourcing discussion; if ≥1 clear genuine buyer → document Mode-2 "
            "hypothesis (auth still required). Do not reopen path_b/c from freezes alone."
        ),
        "selection": (
            "Prefer community_class NOT IN (JOB_BOARD, EXCHANGE_OFFICIAL); "
            "prefer NO_PATH_MATCH near-miss; exclude ALL 24 prior WP2+WP2b freeze message_ids; "
            f"stride={args.stride} offsets={offsets} (≠ E118 97/3 and ≠ E118b 89/11,23,41)."
        ),
        "mode": "OFFLINE_PREP_READONLY",
        "path_freeze": "path_b+path_c",
        "ml_training_enabled": False,
    }

    nopath_cands: list[dict[str, Any]] = []
    veto_cands: list[dict[str, Any]] = []
    eligible_cands: list[dict[str, Any]] = []
    loss_hist: Counter[str] = Counter()
    community_hist: Counter[str] = Counter()
    near_total = 0
    sampled_total = 0
    skipped_excluded = 0
    bucket_sampled: dict[str, int] = {}

    async with SessionLocal() as session:
        await session.execute(
            sql_text(f"SET LOCAL statement_timeout = {int(args.statement_timeout_ms)}")
        )
        iso = dict((await session.execute(sql_text(ISO_SQL))).mappings().one())
        isolation_ok = (
            int(iso["human_labels"]) == EXPECT_HL
            and int(iso["ai_confirmed"]) == EXPECT_AC
        )
        if not isolation_ok:
            print(f"ISOLATION_DRIFT: {iso}", file=sys.stderr)
            return 2

        seen_mids: set[int] = set()
        for off in offsets:
            for name, lo_d, hi_d in AGE_BUCKETS:
                lo, hi = _bucket_bounds(now, lo_d, hi_d)
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
                                "offset": int(off),
                            },
                        )
                    )
                    .mappings()
                    .all()
                ]
                key = f"{name}@off{off}"
                bucket_sampled[key] = len(rows)
                sampled_total += len(rows)
                for r in rows:
                    mid = int(r["id"])
                    if mid in excluded:
                        skipped_excluded += 1
                        continue
                    if mid in seen_mids:
                        continue
                    seen_mids.add(mid)
                    text = r.get("text") or ""
                    feats = evaluate_discovery(
                        scorer, text, version=DISCOVERY_VERSION_V6
                    )
                    fl = first_loss(feats)
                    loss_hist[fl] += 1
                    near = bool(TRUE_BUYER_NEAR_MISS_RX.search(text))
                    refined_hit = bool(refined_rx.search(text))
                    if near:
                        near_total += 1
                    cclass = _community_class(
                        scorer, r.get("community_username"), r.get("community_name")
                    )
                    community_hist[cclass] += 1
                    base = {
                        "message_id": mid,
                        "bucket": name,
                        "sample_offset": off,
                        "first_loss": fl,
                        "community_class": cclass,
                        "near_miss_buyer_rx": near,
                        "refined_commercial_rx": refined_hit,
                        "preview": _preview(text),
                    }
                    if feats.eligible:
                        eligible_cands.append(
                            {
                                **base,
                                "trigger_type": getattr(feats, "trigger_type", None),
                                "first_loss": "RETRIEVED",
                            }
                        )
                        continue
                    if fl == "NO_PATH_MATCH" and near:
                        nopath_cands.append(
                            {
                                **base,
                                "direction": getattr(feats, "buyer_direction", None),
                            }
                        )
                    elif (fl.startswith("VETO_") or fl == "HARD_EXCLUDE_EARLY") and (
                        near or refined_hit
                    ):
                        veto_cands.append(base)

    freeze_nopath = _select_ranked(nopath_cands, int(args.freeze_cap_nopath))
    freeze_veto = _select_ranked(veto_cands, int(args.freeze_cap_veto))
    freeze_eligible = _select_ranked(eligible_cands, 20)

    preferred_nopath = sum(
        1 for x in freeze_nopath if x["community_class"] not in DEPRIORITIZED
    )
    preferred_veto = sum(
        1 for x in freeze_veto if x["community_class"] not in DEPRIORITIZED
    )
    preferred_eligible = sum(
        1 for x in freeze_eligible if x["community_class"] not in DEPRIORITIZED
    )

    payload = {
        "evidence_id": EV,
        "generated_at": now.isoformat(),
        "head": head,
        "runtime_sec": round(time.time() - t0, 2),
        "read_only": True,
        "mode": "OFFLINE_PREP_READONLY",
        "pre_registered": pre_registered,
        "isolation": {
            "human_labels": int(iso["human_labels"]),
            "ai_confirmed": int(iso["ai_confirmed"]),
            "message_scores": int(iso["message_scores"]),
            "messages": int(iso["messages"]),
            "expect_human_labels": EXPECT_HL,
            "expect_ai_confirmed": EXPECT_AC,
            "isolation_ok": isolation_ok,
        },
        "sample": {
            "per_bucket": args.per_bucket,
            "stride": args.stride,
            "offsets": offsets,
            "n_sampled_total": sampled_total,
            "n_unique_scored": len(seen_mids),
            "bucket_sampled": bucket_sampled,
            "skipped_excluded_prior_ids": skipped_excluded,
            "excluded_prior_freeze_ids": sorted(excluded),
            "discovery_version": DISCOVERY_VERSION_V6,
        },
        "first_loss_histogram": dict(loss_hist),
        "community_class_hist": dict(community_hist),
        "candidate_pools": {
            "nopath_near_miss": len(nopath_cands),
            "veto_near_or_refined": len(veto_cands),
            "eligible_retrieved": len(eligible_cands),
            "near_miss_rx_hits": near_total,
        },
        "freeze_counts": {
            "nopath_near_miss": len(freeze_nopath),
            "veto_near_or_refined": len(freeze_veto),
            "eligible_retrieved": len(freeze_eligible),
            "preferred_community_nopath": preferred_nopath,
            "preferred_community_veto": preferred_veto,
            "preferred_community_eligible": preferred_eligible,
        },
        "business": {
            "outreach": False,
            "ml_go": False,
            "threshold_loosen": False,
            "discovery_semantic_change": False,
            "path_b_path_c_reopened": False,
        },
        "next": "WP2c adjudicate NEW freeze IDs into label_reviews only (batch e118c_wp2c_* → Evidence 119d).",
    }
    freeze_doc = {
        "evidence_id": EV,
        "generated_at": now.isoformat(),
        "head": head,
        "parent_evidence": ["118", "118b"],
        "selection_policy": pre_registered["selection"],
        "excluded_prior_message_ids": sorted(excluded),
        "nopath_near_miss": freeze_nopath,
        "veto_near_or_refined": freeze_veto,
        "eligible_retrieved": freeze_eligible,
    }

    lines = [
        f"Evidence {EV}: Offline freeze expansion (WP2c / Mode 1 read-only)",
        "experiment_id=OFFLINE_FREEZE_EXPAND_118C",
        "decision=OBSERVE / FREEZE_EXPANDED_FOR_WP2C",
        "path_freeze=path_b+path_c",
        "shadow=OFF ml_training_enabled=false",
        "code_change=read-only expand script only (no discovery semantics)",
        f"generated_at={now.isoformat()} head={head}",
        f"runtime_sec={payload['runtime_sec']} read_only=true",
        "",
        "=== 0. PRE-REGISTERED ===",
        f"  hypothesis={pre_registered['hypothesis']}",
        f"  decision_rule={pre_registered['decision_rule']}",
        f"  selection={pre_registered['selection']}",
        "",
        "=== 1. ISOLATION ===",
        f"  human_labels={iso['human_labels']} AI_CONFIRMED={iso['ai_confirmed']} "
        f"isolation_ok={isolation_ok}",
        "",
        "=== 2. SAMPLE ===",
        f"  n_sampled={sampled_total} unique={len(seen_mids)} stride={args.stride} "
        f"offsets={offsets} per_bucket={args.per_bucket}",
        f"  bucket_sampled={bucket_sampled}",
        f"  skipped_excluded_prior={skipped_excluded} excluded_ids={sorted(excluded)}",
        f"  near_miss_rx_hits={near_total}",
        f"  first_loss_hist={dict(loss_hist)}",
        f"  community_hist={dict(community_hist)}",
        "",
        "=== 3. FREEZE SELECTION ===",
        f"  pools nopath={len(nopath_cands)} veto={len(veto_cands)} "
        f"eligible={len(eligible_cands)}",
        f"  selected nopath={len(freeze_nopath)} (preferred_community={preferred_nopath})",
        f"  selected veto={len(freeze_veto)} (preferred_community={preferred_veto})",
        f"  selected eligible_retrieved={len(freeze_eligible)} "
        f"(preferred_community={preferred_eligible})",
        "",
        "=== 4. BUSINESS / LOCKS ===",
        f"  {payload['business']}",
        "",
        "=== 5. NEXT ===",
        f"  {payload['next']}",
        "",
    ]
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    OUT_FREEZE.write_text(
        json.dumps(freeze_doc, indent=2, default=str, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(OUT_TXT.read_text())
    print(
        json.dumps(
            {
                "freeze_nopath": len(freeze_nopath),
                "freeze_veto": len(freeze_veto),
                "freeze_eligible": len(freeze_eligible),
                "preferred_nopath": preferred_nopath,
                "preferred_veto": preferred_veto,
                "isolation_ok": isolation_ok,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
