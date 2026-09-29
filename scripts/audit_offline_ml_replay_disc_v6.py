#!/usr/bin/env python3
"""Evidence 130 / Mode-1 — In-process offline replay (LeadScorer + disc_v6).

READ-ONLY against production Postgres. Does NOT write message_scores,
candidates, labels, flip flags, reopen path_b/c, enable shadow/ML, or
loosen scoring.yaml. Prefer this over scripts/rescore_slice.py.

Extends the E118 messages-only stratified pattern: every sample row gets
LeadScorer.score() AND evaluate_discovery(disc_v6) in-process, plus an
optional read-only human_labels join for ML-prep coverage stats.

Run:
  docker compose run --rm --no-deps -e GIT_HEAD=$(git rev-parse --short HEAD) \\
    -v \"$PWD:/app\" -w /app analyzer \\
    sh -c 'PYTHONPATH=/app python scripts/audit_offline_ml_replay_disc_v6.py'
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
    first_loss,
    is_exchange_community,
)

EV = "130"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-offline-ml-replay-disc-v6.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-offline-ml-replay-disc-v6.json"
OUT_ROWS = ROOT / f"docs/audit/evidence/{EV}-offline-ml-replay-rows.jsonl"
OUT_TMP = Path(f"/tmp/{EV}-offline-ml-replay-disc-v6.json")
OUT_TMP_ROWS = Path(f"/tmp/{EV}-offline-ml-replay-rows.jsonl")

PATH_B_AT = datetime.fromisoformat("2026-09-26T06:53:16+00:00")
EXPECT_HL = 1524
EXPECT_AC = 4

# Distinct modular sample from E118 (stride=97,offset=3) / 118b / 118c.
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

SAMPLE_SQL = """
SELECT m.id, m.text, m.message_date, m.community_id,
       c.name AS community_name, c.username AS community_username,
       s.tier AS persisted_tier, s.score AS persisted_score,
       hl.label AS human_label,
       hl.commercially_actionable AS commercially_actionable
FROM messages m
LEFT JOIN communities c ON c.id = m.community_id
LEFT JOIN message_scores s ON s.message_id = m.id
LEFT JOIN human_labels hl ON hl.message_id = m.id
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


def _preview(text: str, n: int = 160) -> str:
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


def _score_snap(scorer: LeadScorer, text: str, username: str | None, name: str | None) -> dict[str, Any]:
    try:
        scored = scorer.score(text, community_username=username, community_name=name)
        return {
            "score": float(getattr(scored, "score", 0.0) or 0.0),
            "tier": str(getattr(scored, "tier", "UNSCORED") or "UNSCORED").upper(),
            "lead_type": getattr(scored, "lead_type", None),
            "buyer_type": getattr(scored, "buyer_type", None),
            "intent_score": float(getattr(scored, "intent_score", 0.0) or 0.0),
            "technical_score": float(getattr(scored, "technical_score", 0.0) or 0.0),
            "commercial_score": float(getattr(scored, "commercial_score", 0.0) or 0.0),
            "promotion_score": float(getattr(scored, "promotion_score", 0.0) or 0.0),
        }
    except Exception as exc:  # noqa: BLE001 — offline inventory must not abort sample
        return {"score": None, "tier": "ERROR", "error": type(exc).__name__}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-bucket", type=int, default=400)
    ap.add_argument("--stride", type=int, default=101)
    ap.add_argument("--offset", type=int, default=13)
    ap.add_argument("--row-cap", type=int, default=200, help="JSONL preview rows under /tmp")
    ap.add_argument("--statement-timeout-ms", type=int, default=180000)
    args = ap.parse_args()

    t0 = time.time()
    now = datetime.now(timezone.utc)
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    head = _git_head()

    pre_registered = {
        "hypothesis": (
            "In-process LeadScorer+disc_v6 replay on messages-only sample yields an "
            "offline feature/outcome matrix for Mode-1 ML prep without mutating "
            "production message_scores denominators."
        ),
        "decision_rule": (
            "Artifact-only; does NOT unlock Optuna/LGBM/SHAP/ML GO. "
            "Standing interpretation: live RESIDUAL_SCARCITY + offline contamination "
            "(WP2/WP2b/WP2c); OBSERVE ≠ ML unlock."
        ),
        "mode": "OFFLINE_PREP_READONLY",
        "path_freeze": "path_b+path_c",
        "ml_training_enabled": False,
        "parent": "inventory + E118 + E44 ML NO-GO",
        "sample_policy": {
            "messages_only": True,
            "stride": int(args.stride),
            "offset": int(args.offset),
            "per_bucket": int(args.per_bucket),
            "distinct_from_e118": "E118 used stride=97 offset=3",
        },
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
    tier_hist: Counter[str] = Counter()
    tier_x_loss: Counter[str] = Counter()
    label_hist: Counter[str] = Counter()
    action_hist: Counter[str] = Counter()
    class_hist: Counter[str] = Counter()
    eligible_by_tier: Counter[str] = Counter()
    persisted_vs_replay: Counter[str] = Counter()
    preview_rows: list[dict[str, Any]] = []

    for r in samples:
        text = r.get("text") or ""
        bucket = r["_bucket"]
        username = r.get("community_username")
        name = r.get("community_name")
        snap = _score_snap(scorer, text, username, name)
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        fl = first_loss(feats)
        tier = str(snap.get("tier") or "UNSCORED")
        loss_hist[fl] += 1
        tier_hist[tier] += 1
        tier_x_loss[f"{tier}|{fl}"] += 1
        cclass = _community_class(scorer, username, name)
        class_hist[cclass] += 1
        hl = r.get("human_label")
        label_hist[str(hl) if hl is not None else "(unlabeled)"] += 1
        ca = r.get("commercially_actionable")
        if ca is True:
            action_hist["true"] += 1
        elif ca is False:
            action_hist["false"] += 1
        else:
            action_hist["null_or_unlabeled"] += 1
        if feats.eligible:
            eligible_by_tier[tier] += 1

        pt = r.get("persisted_tier")
        if pt is None:
            persisted_vs_replay["no_persisted_score"] += 1
        elif str(pt).upper() == tier:
            persisted_vs_replay["tier_match"] += 1
        else:
            persisted_vs_replay["tier_mismatch"] += 1

        if len(preview_rows) < int(args.row_cap):
            preview_rows.append(
                {
                    "message_id": int(r["id"]),
                    "bucket": bucket,
                    "community_id": r.get("community_id"),
                    "community_class": cclass,
                    "replay_tier": tier,
                    "replay_score": snap.get("score"),
                    "persisted_tier": pt,
                    "first_loss": fl,
                    "eligible": bool(feats.eligible),
                    "trigger_type": getattr(feats, "trigger_type", None),
                    "matched_paths": list(getattr(feats, "matched_paths", None) or []),
                    "human_label": hl,
                    "commercially_actionable": ca,
                    "preview": _preview(text),
                }
            )

    n = len(samples)
    retrieved = int(loss_hist.get("RETRIEVED", 0))
    labeled = n - int(label_hist.get("(unlabeled)", 0))

    payload: dict[str, Any] = {
        "evidence_id": EV,
        "experiment_id": "OFFLINE_ML_REPLAY_DISC_V6_130",
        "generated_at": now.isoformat(),
        "head": head,
        "elapsed_s": round(time.time() - t0, 2),
        "pre_registered": pre_registered,
        "isolation": {
            **{k: int(iso[k]) if k != "disc_v6_new" else int(iso[k]) for k in iso},
            "expect": {"human_labels": EXPECT_HL, "AI_CONFIRMED": EXPECT_AC},
            "ok": isolation_ok,
        },
        "sample": {
            "n": n,
            "bucket_stats": bucket_stats,
            "stride": int(args.stride),
            "offset": int(args.offset),
            "per_bucket": int(args.per_bucket),
        },
        "replay": {
            "first_loss": dict(loss_hist.most_common()),
            "replay_tier": dict(tier_hist.most_common()),
            "tier_x_first_loss_top": dict(tier_x_loss.most_common(25)),
            "eligible_by_replay_tier": dict(eligible_by_tier.most_common()),
            "disc_v6_retrieved_in_sample": retrieved,
            "community_class": dict(class_hist.most_common()),
            "persisted_vs_replay_tier": dict(persisted_vs_replay.most_common()),
        },
        "label_coverage_in_sample": {
            "labeled": labeled,
            "unlabeled": int(label_hist.get("(unlabeled)", 0)),
            "by_label": dict(label_hist.most_common()),
            "commercially_actionable": dict(action_hist.most_common()),
        },
        "ml_go_claim": False,
        "optuna_started": False,
        "production_rescore": False,
        "human_labels_mutated": False,
        "decision": (
            "OFFLINE_REPLAY_ARTIFACT_ONLY — OBSERVE continues; ML/Optuna/shadow stay off; "
            "path_b/c stay frozen."
        ),
        "preview_row_count": len(preview_rows),
        "artifacts": {
            "evidence_json": str(OUT_JSON),
            "evidence_txt": str(OUT_TXT),
            "tmp_json": str(OUT_TMP),
            "tmp_rows_jsonl": str(OUT_TMP_ROWS),
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, default=str) + "\n"
    OUT_JSON.write_text(body, encoding="utf-8")
    with OUT_ROWS.open("w", encoding="utf-8") as fh:
        for row in preview_rows:
            fh.write(json.dumps(row, default=str) + "\n")
    # Best-effort host /tmp mirrors (may fail if prior root-owned files exist).
    for path, content_fn in (
        (OUT_TMP, lambda: body),
        (
            OUT_TMP_ROWS,
            lambda: "".join(json.dumps(r, default=str) + "\n" for r in preview_rows),
        ),
    ):
        try:
            path.write_text(content_fn(), encoding="utf-8")
        except OSError as exc:
            payload.setdefault("tmp_write_warnings", []).append(
                {"path": str(path), "error": f"{type(exc).__name__}: {exc}"}
            )
    payload["artifacts"]["evidence_rows_jsonl"] = str(OUT_ROWS)
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    lines = [
        f"Evidence {EV}: Mode-1 in-process offline replay (LeadScorer + disc_v6)",
        f"experiment_id={payload['experiment_id']}",
        f"decision={payload['decision']}",
        f"measure_at={now.isoformat()} head={head} elapsed_s={payload['elapsed_s']}",
        "read_only=true production_rescore=false ml_go=false path_b_c_frozen=true",
        "",
        "=== 0. LOCKS ===",
        "  No rescore_slice / prod backfill; no human_labels mutation; no Optuna/LGBM/SHAP;",
        "  no shadow enable; no path reopen; OBSERVE ≠ ML unlock.",
        "",
        "=== 1. PRE-REGISTERED ===",
        f"  hypothesis={pre_registered['hypothesis']}",
        f"  decision_rule={pre_registered['decision_rule']}",
        f"  sample={pre_registered['sample_policy']}",
        "",
        "=== 2. ISOLATION ===",
        f"  hl={iso['human_labels']} ac={iso['ai_confirmed']} ok={isolation_ok}",
        f"  messages={iso['messages']} scores={iso['message_scores']} disc_v6_new={iso['disc_v6_new']}",
        "",
        "=== 3. SAMPLE ===",
        f"  n={n} stride={args.stride} offset={args.offset} per_bucket={args.per_bucket}",
    ]
    for bname, _, _ in AGE_BUCKETS:
        bs = bucket_stats.get(bname) or {}
        lines.append(
            f"  {bname}: pop={bs.get('n_messages')} scored={bs.get('n_scored')} "
            f"cov={bs.get('score_coverage')} sampled={bs.get('sampled')}"
        )
    lines += [
        "",
        "=== 4. REPLAY (in-process; no DB writes) ===",
        f"  first_loss={dict(loss_hist.most_common(12))}",
        f"  replay_tier={dict(tier_hist.most_common())}",
        f"  eligible_by_tier={dict(eligible_by_tier.most_common())}",
        f"  retrieved={retrieved}",
        f"  persisted_vs_replay={dict(persisted_vs_replay.most_common())}",
        f"  community_class_top={dict(class_hist.most_common(8))}",
        "",
        "=== 5. LABEL COVERAGE (read-only join) ===",
        f"  labeled={labeled}/{n} actionable={dict(action_hist.most_common())}",
        f"  by_label={dict(label_hist.most_common())}",
        "",
        "=== 6. ARTIFACTS ===",
        f"  {OUT_TXT}",
        f"  {OUT_JSON}",
        f"  {OUT_ROWS} (n={len(preview_rows)})",
        f"  {OUT_TMP} (best-effort)",
        f"  {OUT_TMP_ROWS} (best-effort)",
        "",
        "=== 7. NEXT ===",
        "  Continue CONTINUOUS OBSERVE. Mode-1 artifacts ready for offline R&D only.",
        "  Do not start Optuna/LGBM/SHAP; do not claim ML GO; do not UpdateGoal complete.",
    ]
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_TXT.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
