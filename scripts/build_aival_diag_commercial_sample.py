#!/usr/bin/env python3
"""Build a gate-isolated commercial-candidate diagnostic sample.

Requires sample_batch_id starting with aival_diag_ (server-side gate exclusion).
Sampling metadata only — NOT ground truth. Does not change the scorer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, text

from shared.db import SessionLocal
from shared.models import LabelReviewSample
from shared.validation.definition import COMMERCIAL_LEAD_TYPES
from shared.validation.diagnostic import is_diagnostic_batch

METHODOLOGY = """
Diagnostic commercial-candidate sample for Mode-B measurement.
Gate-isolated via aival_diag_ batch prefix (excluded from ai_validated_* numerators).
CRM/provisional labels are sampling metadata only — not ground truth, not sales truth.
Validators remain blind (text + task_token only); prompts unchanged.
"""

SUPPORT_COMMUNITIES = ("BybitAPI", "BinanceAPI", "BybitEnglish", "bitunixglobal")


async def main(batch_id: str, n: int, seed: str, out: Path) -> None:
    if not is_diagnostic_batch(batch_id):
        raise SystemExit(f"batch_id must be diagnostic (aival_diag_*); got {batch_id!r}")
    if n < 15 or n > 30:
        raise SystemExit(f"--n must be 15..30; got {n}")

    types = sorted(COMMERCIAL_LEAD_TYPES)
    types_sql = ", ".join(f"'{t}'" for t in types)
    support_sql = ", ".join(f"'{u}'" for u in SUPPORT_COMMUNITIES)

    async with SessionLocal() as db:
        existing = int(
            (
                await db.scalar(
                    select(func.count(LabelReviewSample.id)).where(
                        LabelReviewSample.sample_batch_id == batch_id
                    )
                )
            )
            or 0
        )
        if existing:
            raise SystemExit(f"batch {batch_id} already has {existing} sample rows; refuse overwrite")

        # Anchors: all CRM HIGH/MEDIUM commercial lead_types (may already be AI-validated).
        anchors = [
            int(r[0])
            for r in (
                await db.execute(
                    text(
                        f"""
                        SELECT l.message_id FROM leads l
                        WHERE l.tier IN ('HIGH','MEDIUM')
                          AND l.lead_type IN ({types_sql})
                        ORDER BY l.score DESC, l.id ASC
                        """
                    )
                )
            ).all()
        ]

        # Holdout: provisional TRUE_LEAD not previously AI-validated (non-diag), not anchors,
        # not job FP, not support communities.
        holdout_needed = max(0, n - len(anchors))
        holdout = [
            int(r[0])
            for r in (
                await db.execute(
                    text(
                        f"""
                        SELECT h.message_id
                        FROM human_labels h
                        JOIN messages m ON m.id = h.message_id
                        JOIN communities c ON c.id = m.community_id
                        WHERE h.label = 'TRUE_LEAD'
                          AND h.labeled_by IN (
                            'agent_business_review_2026-09-22',
                            'audit_census_2026-09-22'
                          )
                          AND COALESCE(h.fp_class, '') NOT IN ('JOB_VACANCY', 'JOB_SEEKER')
                          AND c.username NOT IN ({support_sql})
                          AND NOT (h.message_id = ANY(:anchors))
                          AND h.message_id NOT IN (
                            SELECT message_id FROM ai_validation_attempts
                            WHERE sample_batch_id NOT LIKE 'aival\\_diag\\_%' ESCAPE '\\'
                            UNION
                            SELECT message_id FROM label_reviews
                            WHERE validator_kind = 'ai'
                              AND COALESCE(sample_batch_id, '') NOT LIKE 'aival\\_diag\\_%' ESCAPE '\\'
                            UNION
                            SELECT message_id FROM validation_consensus
                            WHERE synthetic IS FALSE
                              AND sample_batch_id NOT LIKE 'aival\\_diag\\_%' ESCAPE '\\'
                          )
                        ORDER BY md5(h.message_id::text || :seed), h.message_id ASC
                        LIMIT :lim
                        """
                    ),
                    {"anchors": anchors or [-1], "seed": seed, "lim": holdout_needed},
                )
            ).all()
        ]

        picks: list[tuple[str, int]] = [("crm_commercial_anchor", mid) for mid in anchors]
        picks += [("provisional_true_holdout", mid) for mid in holdout]
        if len(picks) < 15:
            raise SystemExit(f"only {len(picks)} candidates available; need >=15")
        picks = picks[:n]

        for stratum, mid in picks:
            db.add(
                LabelReviewSample(
                    sample_batch_id=batch_id,
                    message_id=mid,
                    stratum=stratum,
                    methodology_notes=METHODOLOGY.strip(),
                )
            )
        await db.commit()

        # Objective coverage from CRM lead_type where available (metadata only).
        obj_rows = (
            await db.execute(
                text(
                    f"""
                    SELECT COALESCE(l.lead_type, 'unknown') AS lt, COUNT(*)
                    FROM label_review_samples s
                    LEFT JOIN leads l ON l.message_id = s.message_id
                    WHERE s.sample_batch_id = :batch
                    GROUP BY 1 ORDER BY 2 DESC
                    """
                ),
                {"batch": batch_id},
            )
        ).all()

        strata_counts: dict[str, int] = {}
        for stratum, _mid in picks:
            strata_counts[stratum] = strata_counts.get(stratum, 0) + 1

        report = {
            "sample_batch_id": batch_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "diagnostic_only": True,
            "gate_isolation": "aival_diag_ prefix excluded from gate numerators",
            "n_requested": n,
            "unique_messages": len(picks),
            "seed": seed,
            "stratum_final_counts": strata_counts,
            "message_ids": [mid for _, mid in picks],
            "crm_lead_type_counts": {str(k): int(v) for k, v in obj_rows},
            "methodology": METHODOLOGY.strip(),
            "caveats": [
                "CRM/provisional labels are sampling metadata only.",
                "Not ground truth; not sales qualification.",
                "Diagnostic results must not enter ai_validated_* numerators.",
            ],
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--batch-id", default="aival_diag_commercial_candidates_20260923")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--seed", default="aival_diag_commercial_20260923")
    p.add_argument(
        "--out",
        default="docs/audit/evidence/83-aival-diag-commercial-sample-20260923.json",
    )
    args = p.parse_args()
    asyncio.run(main(args.batch_id, args.n, args.seed, Path(args.out)))
