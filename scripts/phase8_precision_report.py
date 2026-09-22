#!/usr/bin/env python3
"""Phase 8 precision report: audit census labels vs current scorer outcomes.

Uses docs/audit/evidence/09-human-audit-labels.tsv (unit-level census).
Scores the FULL message text from Postgres when example_message_id is present;
falls back to text_preview only if the message row is missing.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from services.analyzer.app.scoring import LeadScorer
from shared.db import SessionLocal
from shared.models import Community, Message


FP_MAP = {
    "exchange_or_product_promo": "MARKETING_BROADCAST",
    "exchange_product_update": "MARKETING_BROADCAST",
    "exchange_channel_no_buyer": "MARKETING_BROADCAST",
    "job_board_or_vacancy": "JOB_VACANCY",
    "employment_vacancy": "JOB_VACANCY",
    "job_aggregator_digest": "JOB_VACANCY",
    "job_seeker": "JOB_SEEKER",
    "vendor_promo": "SERVICE_AD",
    "service_ad": "SERVICE_AD",
    "news_digest": "NEWS_DIGEST",
    "off_domain": "OFF_DOMAIN",
    "no_commercial_buyer_evidence": "OFF_DOMAIN",
    "end_user_ea_access": "OFF_DOMAIN",
}


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - margin) / denom, (centre + margin) / denom)


async def load_messages(ids: list[int]) -> dict[int, tuple[str, str | None]]:
    out: dict[int, tuple[str, str | None]] = {}
    if not ids:
        return out
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Message.id, Message.text, Community.username)
                .join(Community, Message.community_id == Community.id)
                .where(Message.id.in_(ids))
            )
        ).all()
        for mid, text, username in rows:
            out[int(mid)] = (text or "", username)
    return out


async def amain(args: argparse.Namespace) -> None:
    scorer = LeadScorer(args.scoring)
    path = Path(args.audit_tsv)
    rows = list(csv.DictReader(path.open(encoding="utf-8"), delimiter="\t"))
    ids: list[int] = []
    for r in rows:
        try:
            ids.append(int(r.get("example_message_id") or 0))
        except ValueError:
            pass
    id_map = await load_messages([i for i in ids if i > 0])

    unit_tp = unit_fp = unit_amb = unit_fn = 0
    row_tp = row_fp = row_amb = row_fn = 0
    by_fp: Counter[str] = Counter()
    script_tp: Counter[str] = Counter()
    script_n: Counter[str] = Counter()
    scorer_still_lead_on_fp = 0
    examples: list[dict] = []
    used_full = used_preview = 0

    for r in rows:
        label = (r.get("human_label") or "").strip().upper()
        preview = (r.get("text_preview") or "").replace(" | ", "\n")
        community = (r.get("community") or "").lstrip("@")
        multiplicity = int(float(r.get("row_multiplicity") or 1))
        script = r.get("script_class") or "UNKNOWN"
        fp_raw = r.get("human_target_or_fp_class") or ""
        fp_class = FP_MAP.get(fp_raw, fp_raw.upper() if fp_raw else "OFF_DOMAIN")

        try:
            mid = int(r.get("example_message_id") or 0)
        except ValueError:
            mid = 0
        if mid in id_map:
            body, uname = id_map[mid]
            community = uname or community
            used_full += 1
        else:
            body = preview
            used_preview += 1

        result = scorer.score(body, community_username=community)
        scorer_lead = result.tier in {"HIGH", "MEDIUM"}

        if label == "TRUE_LEAD":
            if scorer_lead:
                unit_tp += 1
                row_tp += multiplicity
                script_tp[script] += 1
            else:
                unit_fn += 1
                row_fn += multiplicity
                examples.append(
                    {
                        "kind": "FN",
                        "community": community,
                        "tier": result.tier,
                        "lead_type": result.lead_type,
                        "preview": body[:180],
                    }
                )
        elif label == "AMBIGUOUS":
            unit_amb += 1
            row_amb += multiplicity
        else:
            by_fp[fp_class] += 1
            if scorer_lead:
                unit_fp += 1
                row_fp += multiplicity
                scorer_still_lead_on_fp += 1
                examples.append(
                    {
                        "kind": "FP_REMAINING",
                        "community": community,
                        "fp_class": fp_class,
                        "tier": result.tier,
                        "score": result.score,
                        "lead_type": result.lead_type,
                        "preview": body[:180],
                    }
                )
        script_n[script] += 1

    unit_reviewed_binary = unit_tp + unit_fp
    row_reviewed_binary = row_tp + row_fp
    unit_precision = (unit_tp / unit_reviewed_binary) if unit_reviewed_binary else 0.0
    row_precision = (row_tp / row_reviewed_binary) if row_reviewed_binary else 0.0
    unit_ci = wilson_ci(unit_tp, unit_reviewed_binary)
    row_ci = wilson_ci(row_tp, row_reviewed_binary)
    fp_units = sum(1 for r in rows if (r.get("human_label") or "").upper() == "FALSE_POSITIVE")
    fp_elimination_rate = 1.0 - (scorer_still_lead_on_fp / fp_units) if fp_units else 0.0
    true_units = sum(1 for r in rows if (r.get("human_label") or "").upper() == "TRUE_LEAD")
    true_retention = (unit_tp / true_units) if true_units else 0.0

    report = {
        "rule_version": scorer.rule_version,
        "audit_source": str(path),
        "text_source": {"full_message_rows": used_full, "preview_fallback_rows": used_preview},
        "note": (
            "Labels are the 2026-09-22 audit census (agent business-rule review), "
            "not a fully independent human M1 dataset. Scored against full DB message "
            "text when available."
        ),
        "units_total": len(rows),
        "unit_weighted": {
            "true_lead_retained": unit_tp,
            "false_positive_remaining": unit_fp,
            "ambiguous": unit_amb,
            "true_lead_lost_fn": unit_fn,
            "precision": round(unit_precision, 4),
            "precision_95ci": [round(unit_ci[0], 4), round(unit_ci[1], 4)],
            "binary_n": unit_reviewed_binary,
        },
        "row_weighted": {
            "true_lead_retained_rows": row_tp,
            "false_positive_remaining_rows": row_fp,
            "ambiguous_rows": row_amb,
            "true_lead_lost_rows": row_fn,
            "precision": round(row_precision, 4),
            "precision_95ci": [round(row_ci[0], 4), round(row_ci[1], 4)],
            "binary_n": row_reviewed_binary,
        },
        "fp_elimination_rate_units": round(fp_elimination_rate, 4),
        "true_lead_retention_rate_units": round(true_retention, 4),
        "fp_taxonomy_remaining_on_scorer_lead": dict(
            Counter(e["fp_class"] for e in examples if e["kind"] == "FP_REMAINING")
        ),
        "fp_taxonomy_in_census": dict(by_fp),
        "by_script": {
            s: {"units": script_n[s], "true_retained": script_tp[s]} for s in sorted(script_n)
        },
        "gate": {
            "precision_70_ready_unit": unit_precision >= 0.70,
            "precision_70_ready_row": row_precision >= 0.70,
            "conditions_40_70_unit": 0.40 <= unit_precision < 0.70,
            "not_ready_under_40_unit": unit_precision < 0.40,
        },
        "examples": examples[:40],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "examples"}, indent=2, ensure_ascii=False))
    print(f"WROTE {out}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--audit-tsv", default="docs/audit/evidence/09-human-audit-labels.tsv")
    p.add_argument("--scoring", default="config/scoring.yaml")
    p.add_argument("--out", default="docs/audit/evidence/25-phase8-precision-fulltext.json")
    args = p.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
