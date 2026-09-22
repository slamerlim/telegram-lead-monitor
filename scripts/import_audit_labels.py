#!/usr/bin/env python3
"""Import audit census TSV into human_labels (provisional M1 seeding).

labeled_by = audit_census_2026-09-22 — NOT independent human ground truth.
Advances labeling workflow coverage; M1 still requires fresh human review volume.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from shared.db import SessionLocal
from shared.models import HumanLabel, Message
from shared.opportunity import opportunity_key as make_opportunity_key

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
    "vendor_cold_outreach": "SERVICE_AD",
    "market_commentary": "NEWS_DIGEST",
    "support_or_howto": "SUPPORT_REQUEST",
    "job_channel_no_clear_contract": "JOB_VACANCY",
    "job_channel_insufficient_buyer_rfq": "JOB_VACANCY",
}


async def main(tsv: str) -> None:
    path = Path(tsv)
    rows = list(csv.DictReader(path.open(encoding="utf-8"), delimiter="\t"))
    created = updated = skipped = missing = 0
    async with SessionLocal() as db:
        for r in rows:
            mid = r.get("example_message_id")
            if not mid:
                skipped += 1
                continue
            try:
                message_id = int(mid)
            except ValueError:
                skipped += 1
                continue
            message = await db.get(Message, message_id)
            if not message:
                missing += 1
                continue
            label = (r.get("human_label") or "").strip().upper()
            if label not in {"TRUE_LEAD", "FALSE_POSITIVE", "AMBIGUOUS"}:
                skipped += 1
                continue
            fp_raw = (r.get("human_target_or_fp_class") or "").strip().lower()
            fp_class = None
            if label == "FALSE_POSITIVE":
                fp_class = FP_MAP.get(fp_raw, "OFF_DOMAIN")
            existing = await db.scalar(select(HumanLabel).where(HumanLabel.message_id == message_id))
            if not existing:
                existing = HumanLabel(message_id=message_id, community_id=message.community_id)
                db.add(existing)
                created += 1
            else:
                updated += 1
            existing.community_id = message.community_id
            existing.opportunity_key = make_opportunity_key(message.community_id, message.text)
            existing.label = label
            existing.fp_class = fp_class
            existing.commercially_actionable = True if label == "TRUE_LEAD" else False if label == "FALSE_POSITIVE" else None
            existing.language = "mixed" if (r.get("script_class") or "") == "MIXED" else "en"
            existing.notes = (r.get("reviewer_reason") or "")[:500]
            existing.labeled_by = "audit_census_2026-09-22"
            existing.labeled_at = datetime.now(timezone.utc)
        await db.commit()
    print(f"DONE created={created} updated={updated} missing_msg={missing} skipped={skipped} total_rows={len(rows)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tsv", default="docs/audit/evidence/09-human-audit-labels.tsv")
    args = p.parse_args()
    asyncio.run(main(args.tsv))
