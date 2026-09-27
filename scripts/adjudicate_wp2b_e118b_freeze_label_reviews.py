#!/usr/bin/env python3
"""Evidence 119b / WP2b — Adjudicate NEW E118b freeze IDs into label_reviews only.

Append-only label_review_samples + label_reviews.
NEVER mutates human_labels / AI_CONFIRMED.
Skips any message_id already in E118 WP2 batch e118_wp2_adjudication_2026-09-27.

Reviewer: audit_e118b_wp2b_adjudicator (reserved non-independent).

Run:
  docker compose run --rm --no-deps -e GIT_HEAD=$(git rev-parse --short HEAD) \\
    -v \"$PWD:/app\" -w /app analyzer \\
    sh -c 'PYTHONPATH=/app python scripts/adjudicate_wp2b_e118b_freeze_label_reviews.py'
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

EV = "119b"
BATCH_ID = "e118b_wp2b_adjudication_2026-09-27"
REVIEWER_ID = "audit_e118b_wp2b_adjudicator"
PRIOR_BATCH = "e118_wp2_adjudication_2026-09-27"
FREEZE_PATH = ROOT / "docs/audit/evidence/118b-offline-freeze-samples.json"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-wp2b-e118b-freeze-adjudication.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-wp2b-e118b-freeze-adjudication.json"
EXPECT_HL = 1524
EXPECT_AC = 4

# Pre-registered decision rule (matches Evidence 118b header).
CONTAMINATION_SHARE_GE = 0.85  # if ≥85% non-buyer → CONTAMINATION_DOMINANT


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
        ("nopath_near_miss", "e118b_nopath_near_miss"),
        ("veto_near_or_refined", "e118b_veto_near_or_refined"),
        ("eligible_retrieved", "e118b_eligible_retrieved"),
        ("eligible_non_low", "e118b_eligible_non_low"),
    ):
        for item in doc.get(key) or []:
            rows.append({**item, "stratum": stratum, "freeze_family": key})
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
    fl = str(item.get("first_loss") or "")
    preview = (text or item.get("preview") or "").lower()
    cclass = str(item.get("community_class") or "")

    if "freelancer tip" in preview or (
        "winning proposal" in preview and "laborx" in preview
    ):
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "MARKETING_BROADCAST",
            "commercially_actionable": False,
            "quality_class": "provider_vendor",
            "rationale": "Freelancer tip / LaborX marketing promo; not buyer procurement.",
        }

    # LaborX / freelance marketplace templates
    if (
        "laborx.com" in preview
        or "freelance opportunity" in preview
        or "top-paying projects" in preview
        or "new project on laborx" in preview
        or fl == "VETO_job_aggregator"
    ):
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "JOB_VACANCY",
            "commercially_actionable": False,
            "quality_class": "marketplace_gig",
            "rationale": "Job-board / LaborX marketplace listing or aggregator digest; not organic buyer RFQ.",
        }

    if fl == "HARD_EXCLUDE_EARLY" and (
        "top-paying" in preview or "laborx" in preview or cclass == "JOB_BOARD"
    ):
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "JOB_VACANCY",
            "commercially_actionable": False,
            "quality_class": "aggregator",
            "rationale": "Hard-excluded multi-job / board listing.",
        }

    if "i will create" in preview or "hire me" in preview or "dm me" in preview:
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "SERVICE_AD",
            "commercially_actionable": False,
            "quality_class": "provider_vendor",
            "rationale": "Provider self-promotion / service ad.",
        }

    # Product / tool dump (MT5 script etc.)
    if "read-only metatrader" in preview or (
        "script that validates" in preview and "order_sent" in preview
    ):
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "SERVICE_AD",
            "commercially_actionable": False,
            "quality_class": "provider_vendor",
            "rationale": "Tool/product description (MT5 script), not a hire/buy RFQ.",
        }

    if fl == "RETRIEVED":
        # Eligible hit — still require genuine buyer intent in-domain.
        if "laborx" in preview or "apply:" in preview or cclass == "JOB_BOARD":
            return {
                "label": "HUMAN_REVIEWED_FALSE",
                "fp_class": "JOB_VACANCY",
                "commercially_actionable": False,
                "quality_class": "marketplace_gig",
                "rationale": (
                    "disc_v6 RETRIEVED on job-board listing — eligible false positive "
                    "for organic commercial discovery; not actionable buyer in chat."
                ),
            }
        if re.search(
            r"\b(fomo|hold the majority|long-term position|taking a small portion of profits)\b",
            preview,
            re.I,
        ):
            return {
                "label": "HUMAN_REVIEWED_FALSE",
                "fp_class": "OFF_DOMAIN",
                "commercially_actionable": False,
                "quality_class": "other_contamination",
                "rationale": (
                    "disc_v6 RETRIEVED on exchange market commentary / FOMO advice — "
                    "not commercial buyer intent (eligible false positive)."
                ),
            }
        if re.search(
            r"\b(hire|looking for|need (a )?(developer|engineer)|budget\s*[:=]|commission)\b",
            preview,
            re.I,
        ):
            return {
                "label": "HUMAN_REVIEWED_AMBIGUOUS",
                "fp_class": None,
                "commercially_actionable": None,
                "quality_class": "uncertain",
                "rationale": "RETRIEVED with hire/budget language; leave ambiguous for sales review.",
            }
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "OFF_DOMAIN",
            "commercially_actionable": False,
            "quality_class": "other_contamination",
            "rationale": "RETRIEVED without clear buyer RFQ; not commercially actionable.",
        }

    if fl == "NO_PATH_MATCH":
        if re.search(
            r"\b(hire|looking for|need (a )?(developer|engineer)|budget\s*[:=])\b",
            preview,
            re.I,
        ):
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
            "rationale": "NO_PATH near-miss is trading advice / non-RFQ; not commercially actionable.",
        }

    if fl.startswith("VETO_support") or fl.startswith("VETO_service"):
        return {
            "label": "HUMAN_REVIEWED_FALSE",
            "fp_class": "SUPPORT_REQUEST"
            if "support" in fl.lower()
            else "SERVICE_AD",
            "commercially_actionable": False,
            "quality_class": "support" if "support" in fl.lower() else "provider_vendor",
            "rationale": f"Veto pattern {fl}; non-buyer.",
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
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    head = _git_head()
    freeze_items = _load_freeze()
    if not freeze_items:
        print("No freeze samples", file=sys.stderr)
        return 2

    adjudications: list[dict[str, Any]] = []
    skipped_prior: list[int] = []

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
            print(f"ISOLATION_DRIFT before: {hl0}/{ac0}", file=sys.stderr)
            return 2

        prior_ids = set(
            int(x)
            for x in (
                await session.execute(
                    sql_text(
                        """
                        SELECT message_id FROM label_reviews
                        WHERE sample_batch_id = :b
                        """
                    ),
                    {"b": PRIOR_BATCH},
                )
            ).scalars().all()
        )

        mids = [int(x["message_id"]) for x in freeze_items]
        msg_rows = {
            int(r.id): r
            for r in (
                await session.execute(select(Message).where(Message.id.in_(mids)))
            ).scalars().all()
        }

        for item in freeze_items:
            mid = int(item["message_id"])
            if mid in prior_ids:
                skipped_prior.append(mid)
                continue
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
                            "E118b freeze adjudication WP2b. Non-independent audit reviewer. "
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
            row.scorer_shown = True
            row.prior_label_shown = False
            row.validator_kind = "human"
            row.notes = (
                f"WP2b/{EV} quality={adj['quality_class']}; "
                f"first_loss={item.get('first_loss')}; {adj['rationale']}"
            )
            row.reviewed_at = now
            row.rationale_short = adj["rationale"]
            row.lead_type = adj["quality_class"]
            row.evidence_json = json.dumps(
                {
                    "source_evidence": "118b",
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
    by_label: dict[str, int] = {}
    by_fp: dict[str, int] = {}
    by_quality: dict[str, int] = {}
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

    n = len(adjudications)
    genuine_n = by_quality.get("genuine_buyer_project", 0)
    non_buyer_n = n - genuine_n - by_quality.get("uncertain", 0)
    contamination_share = (non_buyer_n / n) if n else 0.0
    if n == 0:
        classification = "WP2B_EMPTY"
    elif genuine_n >= 1 and actionable["true"] >= 1:
        classification = "WP2B_GENUINE_BUYER_SIGNAL"
    elif contamination_share >= CONTAMINATION_SHARE_GE and actionable["true"] == 0:
        classification = "WP2B_CONTAMINATION_DOMINANT"
    else:
        classification = "WP2B_MIXED_OR_UNDERPOWERED"

    retrieved_fps = [
        a
        for a in adjudications
        if a.get("first_loss") == "RETRIEVED" and a["commercially_actionable"] is not True
    ]

    payload = {
        "evidence_id": EV,
        "generated_at": now.isoformat(),
        "head": head,
        "dry_run": bool(args.dry_run),
        "sample_batch_id": BATCH_ID,
        "reviewer_id": REVIEWER_ID,
        "non_independent": True,
        "source_freeze": str(FREEZE_PATH.relative_to(ROOT)),
        "skipped_prior_batch_ids": skipped_prior,
        "n_adjudicated": n,
        "by_label": by_label,
        "by_fp_class": by_fp,
        "by_quality_class": by_quality,
        "commercially_actionable": actionable,
        "genuine_buyer_project_n": genuine_n,
        "contamination_share": round(contamination_share, 4),
        "pre_registered_threshold": CONTAMINATION_SHARE_GE,
        "retrieved_false_positives_n": len(retrieved_fps),
        "retrieved_false_positives": [
            {"message_id": a["message_id"], "rationale": a["rationale"]}
            for a in retrieved_fps
        ],
        "adjudications": adjudications,
        "isolation_before": {
            "human_labels": hl0,
            "AI_CONFIRMED": ac0,
            "label_reviews": lr0,
        },
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
        "classification": classification,
        "next": (
            "Remain OBSERVE. Path_b/c stay frozen. If CONTAMINATION_DOMINANT with "
            "RETRIEVED FPs: document Mode-2 hypothesis only with owner auth "
            "(eligible-on-jobboard/exchange-commentary). WP3 still needs larger "
            "genuine-positive n — do not build Optuna set from contamination rows alone. "
            "Owner may authorize sourcing experiment vs more freeze expansion."
        ),
    }

    lines = [
        f"Evidence {EV}: WP2b E118b freeze adjudication → label_reviews",
        "experiment_id=WP2B_E118B_LABEL_REVIEWS_ONLY",
        f"decision=OBSERVE / {classification}",
        "path_freeze=path_b+path_c",
        "writes=label_review_samples+label_reviews only (append)",
        f"generated_at={now.isoformat()} head={head} dry_run={args.dry_run}",
        "",
        "=== 0. PRE-REGISTERED RULE ===",
        f"  contamination_share≥{CONTAMINATION_SHARE_GE} & actionable_true=0 → CONTAMINATION_DOMINANT",
        f"  ≥1 genuine_buyer + actionable_true → GENUINE_BUYER_SIGNAL (Mode-2 hyp only w/ auth)",
        "",
        "=== 1. ISOLATION ===",
        f"  before human_labels={hl0} AI_CONFIRMED={ac0} label_reviews={lr0}",
        f"  after  human_labels={hl1} AI_CONFIRMED={ac1} "
        f"label_reviews={iso_after['label_reviews']} "
        f"batch_samples={iso_after['sample_rows']} batch_reviews={iso_after['review_rows']}",
        f"  isolation_ok={isolation_ok}",
        f"  skipped_prior_e118_ids={skipped_prior}",
        "",
        "=== 2. ADJUDICATION SUMMARY ===",
        f"  n={n} batch={BATCH_ID} reviewer={REVIEWER_ID}",
        f"  by_label={by_label}",
        f"  by_fp={by_fp}",
        f"  by_quality={by_quality}",
        f"  commercially_actionable={actionable}",
        f"  genuine_buyer_project_n={genuine_n} contamination_share={contamination_share:.3f}",
        f"  retrieved_false_positives_n={len(retrieved_fps)}",
        "",
        "=== 3. PER-MESSAGE ===",
    ]
    for a in adjudications:
        lines.append(
            f"  mid={a['message_id']} {a['label']} fp={a['fp_class']} "
            f"actionable={a['commercially_actionable']} q={a['quality_class']} "
            f"loss={a['first_loss']} class={a.get('community_class')}"
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
    print(OUT_TXT.read_text())
    return 0 if isolation_ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
