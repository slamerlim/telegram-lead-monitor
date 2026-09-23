#!/usr/bin/env python3
"""Build a stratified independent-review sample (not scorer-preferred-only).

Strata (default n each, exclusive message IDs across strata):
  high_leads          — current CRM HIGH/MEDIUM leads
  agent_true          — agent TRUE_LEAD (agreement check only)
  agent_fp            — agent FP excluding JOB_* (those go to recruiter_job)
  recruiter_job       — JOB_VACANCY / JOB_SEEKER from human_labels
  support_like        — exchange communities + support-ish text
  random_negatives    — message_scores NEGATIVE

Writes label_review_samples + methodology JSON evidence.
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


METHODOLOGY = """
Independent review sample — stratified, not scorer-preferred-only.

Purpose: estimate agreement / scorer precision without circular validation.
Reviewers SHOULD use GET /labels/independent-queue?blind=true so scorer output
and prior agent labels are hidden.

Message IDs are exclusive across strata. high_leads may be thin when few CRM
leads exist. This is NOT a production precision estimator until human
adjudication completes at adequate n.
"""

STRATA_SQL: list[tuple[str, str, str]] = [
    # name, exclude_column, query with {exclude} placeholder
    (
        "high_leads",
        "l.message_id",
        """
        SELECT l.message_id FROM leads l
        WHERE l.tier IN ('HIGH','MEDIUM')
        {exclude}
        ORDER BY l.score DESC, l.id ASC
        LIMIT :lim
        """,
    ),
    (
        "agent_true",
        "h.message_id",
        """
        SELECT h.message_id FROM human_labels h
        WHERE h.label='TRUE_LEAD'
          AND h.labeled_by IN ('agent_business_review_2026-09-22','audit_census_2026-09-22')
        {exclude}
        ORDER BY h.id DESC
        LIMIT :lim
        """,
    ),
    (
        "agent_fp",
        "h.message_id",
        """
        SELECT h.message_id FROM human_labels h
        WHERE h.label='FALSE_POSITIVE'
          AND h.labeled_by IN ('agent_business_review_2026-09-22','audit_census_2026-09-22')
          AND COALESCE(h.fp_class, '') NOT IN ('JOB_VACANCY','JOB_SEEKER')
        {exclude}
        ORDER BY h.id DESC
        LIMIT :lim
        """,
    ),
    (
        "recruiter_job",
        "h.message_id",
        """
        SELECT h.message_id FROM human_labels h
        WHERE h.fp_class IN ('JOB_VACANCY','JOB_SEEKER')
        {exclude}
        ORDER BY h.id DESC
        LIMIT :lim
        """,
    ),
    (
        "support_like",
        "m.id",
        """
        SELECT m.id FROM messages m
        JOIN communities c ON c.id=m.community_id
        WHERE c.username IN ('BybitAPI','BinanceAPI','BybitEnglish','bitunixglobal')
          AND (
            m.text ILIKE '%please help%'
            OR m.text ILIKE '%API%'
            OR m.text ILIKE '%подскажите%'
            OR m.text ILIKE '%documentation%'
          )
        {exclude}
        ORDER BY m.id DESC
        LIMIT :lim
        """,
    ),
    (
        "random_negatives",
        "ms.message_id",
        """
        SELECT ms.message_id FROM message_scores ms
        WHERE ms.decision='NEGATIVE'
        {exclude}
        ORDER BY ms.scored_at DESC
        LIMIT :lim
        """,
    ),
]


async def main(batch_id: str, per_stratum: int, out: Path, replace: bool, exclude_ai_validated: bool) -> None:
    async with SessionLocal() as db:
        if replace:
            await db.execute(
                text(
                    """
                    DELETE FROM label_review_samples s
                    WHERE s.sample_batch_id = :batch
                      AND NOT EXISTS (
                        SELECT 1 FROM label_reviews r
                        WHERE r.sample_batch_id = s.sample_batch_id
                          AND r.message_id = s.message_id
                      )
                    """
                ),
                {"batch": batch_id},
            )
            await db.commit()

        seen = set(
            int(x)
            for x in (
                await db.scalars(
                    select(LabelReviewSample.message_id).where(
                        LabelReviewSample.sample_batch_id == batch_id
                    )
                )
            ).all()
        )
        excluded_ai = 0
        if exclude_ai_validated:
            already = {
                int(r[0])
                for r in (
                    await db.execute(
                        text(
                            """
                            SELECT message_id FROM ai_validation_attempts
                            UNION
                            SELECT message_id FROM label_reviews WHERE validator_kind='ai'
                            UNION
                            SELECT message_id FROM validation_consensus WHERE synthetic IS FALSE
                            """
                        )
                    )
                ).all()
            }
            excluded_ai = len(already)
            seen.update(already)

        strata: dict[str, list[int]] = {}
        for stratum, excl_col, sql in STRATA_SQL:
            excl = ""
            if seen:
                ids = ", ".join(str(i) for i in sorted(seen))
                excl = f" AND {excl_col} NOT IN ({ids})"
            rows = list((await db.execute(text(sql.replace("{exclude}", excl)), {"lim": per_stratum})).all())
            picked = [int(r[0]) for r in rows]
            strata[stratum] = picked
            seen.update(picked)

        inserted = 0
        for stratum, ids in strata.items():
            for mid in ids:
                exists = await db.scalar(
                    select(LabelReviewSample.id).where(
                        LabelReviewSample.sample_batch_id == batch_id,
                        LabelReviewSample.message_id == mid,
                    )
                )
                if exists:
                    continue
                db.add(
                    LabelReviewSample(
                        sample_batch_id=batch_id,
                        message_id=mid,
                        stratum=stratum,
                        methodology_notes=METHODOLOGY.strip(),
                    )
                )
                inserted += 1
        await db.commit()

        count_rows = (
            await db.execute(
                select(LabelReviewSample.stratum, func.count(LabelReviewSample.id))
                .where(LabelReviewSample.sample_batch_id == batch_id)
                .group_by(LabelReviewSample.stratum)
            )
        ).all()
        final_counts = {str(k): int(v) for k, v in count_rows}
        total = int(
            (
                await db.scalar(
                    select(func.count(LabelReviewSample.id)).where(
                        LabelReviewSample.sample_batch_id == batch_id
                    )
                )
            )
            or 0
        )

        report = {
            "sample_batch_id": batch_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "per_stratum_requested": per_stratum,
            "unique_messages": total,
            "rows_inserted_this_run": inserted,
            "excluded_already_ai_validated": excluded_ai if exclude_ai_validated else 0,
            "stratum_candidate_counts": {k: len(v) for k, v in strata.items()},
            "stratum_final_counts": final_counts,
            "methodology": METHODOLOGY.strip(),
            "exclude_ai_validated": exclude_ai_validated,
            "caveats": [
                "Agent TRUE/FP strata exist for agreement measurement, not as ground truth.",
                "Blind AI validation recommended (AI queue shows text only).",
                "Do not treat AI consensus as sales validation or human ground truth.",
                "high_leads stratum is limited by current CRM lead count (may be << per_stratum).",
            ],
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--batch-id", default="indep_review_2026-09-22")
    p.add_argument("--per-stratum", type=int, default=25)
    p.add_argument("--out", default="docs/audit/evidence/47-independent-review-sample.json")
    p.add_argument(
        "--replace",
        action="store_true",
        help="Delete unreviewed sample rows for this batch before rebuilding (keeps reviewed).",
    )
    p.add_argument(
        "--exclude-ai-validated",
        action="store_true",
        help="Exclude message_ids already present in AI attempts/reviews/non-synthetic consensus.",
    )
    args = p.parse_args()
    asyncio.run(
        main(args.batch_id, args.per_stratum, Path(args.out), args.replace, args.exclude_ai_validated)
    )