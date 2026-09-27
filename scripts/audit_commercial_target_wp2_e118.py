#!/usr/bin/env python3
"""Evidence 119 / WP2 — Commercial target definition + E118 freeze adjudication.

Mode 1 → limited Mode 2 write: append-only label_review_samples + label_reviews.
NEVER mutates human_labels or AI_CONFIRMED.

Target definition (from config/scoring.yaml commercial objectives, abbreviated):
  commercially_actionable=TRUE only when the message is a genuine buyer/project
  request for in-scope work (trading/automation/integration/repair) with clear
  intent to hire or procure — not jobs, gigs, aggregators, support, marketing,
  or vendor ads.

Reviewer provenance: audit_e118_wp2_adjudicator (reserved non-independent prefix).
Does NOT count toward independent AI validation gates / ML GO.

Run:
  docker compose run --rm --no-deps -v \"$PWD:/app\" -w /app analyzer \\
    sh -c 'PYTHONPATH=/app python scripts/audit_commercial_target_wp2_e118.py'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from shared.db import SessionLocal
from shared.models import LabelReview, LabelReviewSample, Message

EV = "119"
BATCH_ID = "e118_wp2_adjudication_2026-09-27"
REVIEWER_ID = "audit_e118_wp2_adjudicator"
FREEZE_PATH = ROOT / "docs/audit/evidence/118-offline-freeze-samples.json"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-commercial-target-wp2-e118.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-commercial-target-wp2-e118.json"

EXPECT_HL = 1524
EXPECT_AC = 4

TARGET_DEFINITION = """
Commercial target (WP2 / Evidence 119)
=====================================

A message is commercially_actionable=TRUE iff ALL hold:
  1) Buyer/project intent (hire, build, fix, procure) — not seeking a job.
  2) In-domain work relevant to trading/automation/integration/repair objectives
     in config/scoring.yaml (or clear adjacent procurement of such work).
  3) Not contamination: recruiter/employment, marketplace/gig aggregator posts,
     support troubleshooting, marketing/broadcast, vendor self-promotion,
     news digests, off-domain gigs.

Label mapping into label_reviews (human, non-independent audit reviewer):
  HUMAN_REVIEWED_TRUE  + commercially_actionable=true  + fp_class=null
  HUMAN_REVIEWED_FALSE + commercially_actionable=false + fp_class required
  HUMAN_REVIEWED_AMBIGUOUS + commercially_actionable=null when insufficient evidence

E118 freeze classes → default adjudications (text-confirmed):
  VETO_job_aggregator / laborx TOP-PAYING / Freelance Opportunity templates
    → FALSE / JOB_VACANCY (marketplace-gig aggregator)
  HARD_EXCLUDE_EARLY on job-board multi-listing
    → FALSE / JOB_VACANCY
  VETO_support_question on lead-selling / ad-spend promo
    → FALSE / MARKETING_BROADCAST or SERVICE_AD
  NO_PATH_MATCH near-miss that is generic advice (not RFQ)
    → FALSE / OFF_DOMAIN
""".strip()


def _git_head() -> str:
    env = (os.environ.get("GIT_HEAD") or "").strip()
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _load_freeze() -> list[dict[str, Any]]:
    doc = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for key, stratum in (
        ("nopath_near_miss", "e118_nopath_near_miss"),
        ("veto_near_or_refined", "e118_veto_near_or_refined"),
        ("eligible_non_low", "e118_eligible_non_low"),
    ):
        for item in doc.get(key) or []:
            rows.append({**item, "stratum": stratum, "freeze_family": key})
    # Dedupe by message_id (keep first)
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        mid = int(r["message_id"])
        if mid in seen:
            continue
        seen.add(mid)
        out.append(r)
    return out


def adjudicate(item: dict[str, Any], text: str) -> dict[str, Any]:
    """Deterministic audit adjudication from freeze metadata + text."""
    fl = str(item.get("first_loss") or "")
    preview = (text or item.get("preview") or "").lower()
    cclass = str(item.get("community_class") or "")

    # Aggregator / laborx / freelance-opportunity templates
    if (
        fl == "VETO_job_aggregator"
        or "laborx.com" in preview
        or "freelance opportunity" in preview
        or "top-paying projects" in preview
    ):
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "JOB_VACANCY",
            "commercially_actionable": False,
            "quality_class": "marketplace_gig",
            "rationale": "Marketplace/gig or job-aggregator listing; not a buyer RFQ.",
        }

    if fl == "HARD_EXCLUDE_EARLY" and (
        "top-paying" in preview or "laborx" in preview or cclass == "JOB_BOARD"
    ):
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "JOB_VACANCY",
            "commercially_actionable": False,
            "quality_class": "marketplace_gig",
            "rationale": "Hard-excluded multi-job / board listing.",
        }

    if fl.startswith("VETO_support") or (
        "ad spend" in preview and ("leads" in preview or "conversion" in preview)
    ):
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "MARKETING_BROADCAST",
            "commercially_actionable": False,
            "quality_class": "provider_vendor",
            "rationale": "Lead-selling / marketing promo; not buyer procurement.",
        }

    if fl == "NO_PATH_MATCH":
        # Near-miss RX can false-fire on non-RFQ advice.
        if re.search(
            r"\b(hire|looking for|need (a )?(developer|engineer)|budget\s*[:=])",
            preview,
            re.I,
        ) and not re.search(r"\b(should come first|complement that work)\b", preview, re.I):
            return {
                "label": "HUMAN_REVIEWED_AMBIGUOUS",
                "fp_class": None,
                "commercially_actionable": None,
                "quality_class": "uncertain",
                "rationale": "NO_PATH near-miss with hire/budget language; needs human sales review.",
            }
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "OFF_DOMAIN",
            "commercially_actionable": False,
            "quality_class": "other_contamination",
            "rationale": "NO_PATH near-miss is generic advice / non-RFQ; not commercially actionable.",
        }

    return {
        "label": "HUMAN_REVIEWED_AMBIGUOUS",
        "fp_class": None,
        "commercially_actionable": None,
        "quality_class": "uncertain",
        "rationale": f"Unhandled freeze pattern first_loss={fl}; left ambiguous.",
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Adjudicate only; no DB writes")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    head = _git_head()
    freeze_items = _load_freeze()
    if not freeze_items:
        print("No freeze samples found", file=sys.stderr)
        return 2

    adjudications: list[dict[str, Any]] = []
    async with SessionLocal() as session:
        iso = dict(
            (
                await session.execute(
                    sql_text(
                        """
                        SELECT
                          (SELECT COUNT(*) FROM human_labels) AS human_labels,
                          (SELECT COUNT(*) FROM leads WHERE status='AI_CONFIRMED') AS ai_confirmed,
                          (SELECT COUNT(*) FROM label_reviews) AS label_reviews
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
        hl0, ac0, lr0 = int(iso["human_labels"]), int(iso["ai_confirmed"]), int(iso["label_reviews"])
        if hl0 != EXPECT_HL or ac0 != EXPECT_AC:
            print(f"ISOLATION_DRIFT before write: {hl0}/{ac0}", file=sys.stderr)
            return 2

        mids = [int(x["message_id"]) for x in freeze_items]
        msg_rows = {
            int(r.id): r
            for r in (
                await session.execute(select(Message).where(Message.id.in_(mids)))
            ).scalars().all()
        }

        for item in freeze_items:
            mid = int(item["message_id"])
            msg = msg_rows.get(mid)
            text = (msg.text if msg else "") or ""
            adj = adjudicate(item, text)
            record = {
                "message_id": mid,
                "stratum": item["stratum"],
                "freeze_family": item["freeze_family"],
                "first_loss": item.get("first_loss"),
                "bucket": item.get("bucket"),
                "community_class": item.get("community_class"),
                "preview": (text or item.get("preview") or "")[:240],
                **adj,
            }
            adjudications.append(record)

            if args.dry_run:
                continue

            # Sample membership
            exists_sample = await session.scalar(
                select(LabelReviewSample.id).where(
                    LabelReviewSample.sample_batch_id == BATCH_ID,
                    LabelReviewSample.message_id == mid,
                )
            )
            if not exists_sample:
                session.add(
                    LabelReviewSample(
                        sample_batch_id=BATCH_ID,
                        message_id=mid,
                        stratum=str(item["stratum"]),
                        methodology_notes=(
                            "E118 freeze adjudication WP2. Non-independent audit reviewer. "
                            "Not for ML GO / independent validation numerators."
                        ),
                    )
                )

            existing = await session.scalar(
                select(LabelReview).where(
                    LabelReview.message_id == mid,
                    LabelReview.reviewer_id == REVIEWER_ID,
                    LabelReview.sample_batch_id == BATCH_ID,
                )
            )
            row = existing or LabelReview(
                message_id=mid,
                reviewer_id=REVIEWER_ID,
                sample_batch_id=BATCH_ID,
            )
            if not existing:
                session.add(row)
            row.label = adj["label"]
            row.fp_class = adj["fp_class"]
            row.commercially_actionable = adj["commercially_actionable"]
            row.scorer_shown = True  # audit adjudication used freeze + discovery first_loss
            row.prior_label_shown = False
            row.validator_kind = "human"
            row.notes = (
                f"WP2/{EV} quality={adj['quality_class']}; "
                f"first_loss={item.get('first_loss')}; {adj['rationale']}"
            )
            row.reviewed_at = now
            row.rationale_short = adj["rationale"]
            row.evidence_json = json.dumps(
                {
                    "source_evidence": "118",
                    "freeze_family": item["freeze_family"],
                    "first_loss": item.get("first_loss"),
                    "quality_class": adj["quality_class"],
                },
                sort_keys=True,
            )

        if not args.dry_run:
            await session.commit()

        iso_after = dict(
            (
                await session.execute(
                    sql_text(
                        """
                        SELECT
                          (SELECT COUNT(*) FROM human_labels) AS human_labels,
                          (SELECT COUNT(*) FROM leads WHERE status='AI_CONFIRMED') AS ai_confirmed,
                          (SELECT COUNT(*) FROM label_reviews) AS label_reviews,
                          (SELECT COUNT(*) FROM label_review_samples
                             WHERE sample_batch_id=:b) AS sample_rows,
                          (SELECT COUNT(*) FROM label_reviews
                             WHERE sample_batch_id=:b AND reviewer_id=:r) AS review_rows
                        """
                    ),
                    {"b": BATCH_ID, "r": REVIEWER_ID},
                )
            )
            .mappings()
            .one()
        )

    hl1, ac1 = int(iso_after["human_labels"]), int(iso_after["ai_confirmed"])
    isolation_ok = hl1 == EXPECT_HL and ac1 == EXPECT_AC
    by_label = {}
    by_fp = {}
    by_quality = {}
    actionable = {"true": 0, "false": 0, "null": 0}
    for a in adjudications:
        by_label[a["label"]] = by_label.get(a["label"], 0) + 1
        by_fp[str(a["fp_class"])] = by_fp.get(str(a["fp_class"]), 0) + 1
        by_quality[a["quality_class"]] = by_quality.get(a["quality_class"], 0) + 1
        if a["commercially_actionable"] is True:
            actionable["true"] += 1
        elif a["commercially_actionable"] is False:
            actionable["false"] += 1
        else:
            actionable["null"] += 1

    payload = {
        "evidence_id": EV,
        "generated_at": now.isoformat(),
        "head": head,
        "read_only_human_labels": True,
        "dry_run": bool(args.dry_run),
        "sample_batch_id": BATCH_ID,
        "reviewer_id": REVIEWER_ID,
        "validator_kind": "human",
        "non_independent": True,
        "target_definition": TARGET_DEFINITION,
        "source_freeze": str(FREEZE_PATH.relative_to(ROOT)),
        "n_adjudicated": len(adjudications),
        "by_label": by_label,
        "by_fp_class": by_fp,
        "by_quality_class": by_quality,
        "commercially_actionable": actionable,
        "adjudications": adjudications,
        "isolation_before": {"human_labels": hl0, "AI_CONFIRMED": ac0, "label_reviews": lr0},
        "isolation_after": {
            "human_labels": hl1,
            "AI_CONFIRMED": ac1,
            "label_reviews": int(iso_after["label_reviews"]),
            "sample_rows": int(iso_after["sample_rows"]),
            "review_rows": int(iso_after["review_rows"]),
            "isolation_ok": isolation_ok,
        },
        "business": {
            "outreach": False,
            "ml_go": False,
            "threshold_loosen": False,
            "discovery_semantic_change": False,
            "path_b_path_c_reopened": False,
            "human_labels_mutated": hl1 != hl0,
            "ai_confirmed_mutated": ac1 != ac0,
        },
        "classification": "WP2_E118_ADJUDICATED_CONTAMINATION_DOMINANT",
        "next": (
            "Remain OBSERVE. WP3 frozen dataset only after larger adjudicated set; "
            "E118 freezes alone are too small for Optuna/LightGBM. "
            "Do not reopen path_b/c from these contamination-dominant freezes."
        ),
    }

    lines = [
        f"Evidence {EV}: Commercial target + E118 freeze adjudication (WP2)",
        "experiment_id=WP2_E118_LABEL_REVIEWS_ONLY",
        f"decision=OBSERVE / {payload['classification']}",
        "path_freeze=path_b+path_c",
        "writes=label_review_samples+label_reviews only (append)",
        f"generated_at={now.isoformat()} head={head} dry_run={args.dry_run}",
        "",
        "=== 0. TARGET DEFINITION ===",
        TARGET_DEFINITION,
        "",
        "=== 1. ISOLATION ===",
        f"  before human_labels={hl0} AI_CONFIRMED={ac0} label_reviews={lr0}",
        f"  after  human_labels={hl1} AI_CONFIRMED={ac1} "
        f"label_reviews={iso_after['label_reviews']} "
        f"batch_samples={iso_after['sample_rows']} batch_reviews={iso_after['review_rows']}",
        f"  isolation_ok={isolation_ok}",
        "",
        "=== 2. ADJUDICATION SUMMARY ===",
        f"  n={len(adjudications)} batch={BATCH_ID} reviewer={REVIEWER_ID}",
        f"  by_label={by_label}",
        f"  by_fp={by_fp}",
        f"  by_quality={by_quality}",
        f"  commercially_actionable={actionable}",
        "",
        "=== 3. PER-MESSAGE ===",
    ]
    for a in adjudications:
        lines.append(
            f"  mid={a['message_id']} {a['label']} fp={a['fp_class']} "
            f"actionable={a['commercially_actionable']} q={a['quality_class']} "
            f"loss={a['first_loss']}"
        )
        lines.append(f"    rationale: {a['rationale']}")
    lines += [
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
    print(OUT_TXT)
    print(
        json.dumps(
            {
                "isolation_ok": isolation_ok,
                "n": len(adjudications),
                "actionable": actionable,
                "dry_run": args.dry_run,
            },
            indent=2,
        )
    )
    return 0 if isolation_ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
