#!/usr/bin/env python3
"""Controlled re-score of known lead / FP slices (Phase 7).

Does NOT scan the full 8.5M corpus. Modes:
  --leads-only          re-score every current lead opportunity
  --communities a,b     re-score recent messages from named communities
  --message-ids 1,2     re-score specific internal message ids

Prints before/after summary suitable for acceptance evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from services.analyzer.app.scoring import LeadScorer
from shared.db import SessionLocal
from shared.lead_write import upsert_opportunity_lead
from shared.models import Community, Lead, Message
from shared.score_persist import persist_message_score
from shared.settings import get_settings


async def run(
    *,
    leads_only: bool,
    communities: list[str],
    message_ids: list[int],
    limit: int | None,
    batch_size: int,
) -> None:
    settings = get_settings()
    scoring_path = Path(settings.scoring_config)
    if not scoring_path.exists():
        scoring_path = Path(__file__).resolve().parents[1] / "config" / "scoring.yaml"
    scorer = LeadScorer(str(scoring_path))

    async with SessionLocal() as db:
        rows: list[tuple[Message, Community, Lead | None]] = []

        if message_ids:
            stmt = (
                select(Message, Community, Lead)
                .join(Community, Message.community_id == Community.id)
                .outerjoin(Lead, Lead.message_id == Message.id)
                .where(Message.id.in_(message_ids))
            )
            rows = list((await db.execute(stmt)).all())
        elif leads_only:
            stmt = (
                select(Message, Community, Lead)
                .join(Lead, Lead.message_id == Message.id)
                .join(Community, Message.community_id == Community.id)
                .order_by(Lead.score.desc(), Lead.id.asc())
            )
            if limit:
                stmt = stmt.limit(limit)
            rows = list((await db.execute(stmt)).all())
        elif communities:
            wanted = {c.lstrip("@").lower() for c in communities}
            stmt = (
                select(Message, Community, Lead)
                .join(Community, Message.community_id == Community.id)
                .outerjoin(Lead, Lead.message_id == Message.id)
                .where(Community.username.is_not(None))
                .order_by(Message.id.desc())
            )
            if limit:
                stmt = stmt.limit(max(limit * 20, 5000))  # oversample then filter
            raw = list((await db.execute(stmt)).all())
            rows = [
                (m, c, l)
                for m, c, l in raw
                if (c.username or "").lstrip("@").lower() in wanted
            ]
            if limit:
                rows = rows[:limit]
        else:
            raise SystemExit("Specify --leads-only and/or --communities and/or --message-ids")

        before_tier = Counter()
        after_tier = Counter()
        demoted = kept_high = created = deleted = unchanged = 0
        transitions: Counter[str] = Counter()

        processed = 0
        for i, (message, community, prior_lead) in enumerate(rows, 1):
            before = prior_lead.tier if prior_lead else "NONE"
            before_tier[before] += 1
            result = scorer.score(
                message.text,
                community_username=community.username,
                community_name=community.name,
            )
            action, lead = await upsert_opportunity_lead(db, message, result)
            await persist_message_score(db, message, result, rule_version=scorer.rule_version)
            after = result.tier
            after_tier[after] += 1
            transitions[f"{before}->{after}"] += 1
            if action == "deleted":
                deleted += 1
            elif action == "unchanged":
                unchanged += 1
            elif action == "created":
                created += 1
            if before in {"HIGH", "MEDIUM"} and after == "LOW":
                demoted += 1
            if before in {"HIGH", "MEDIUM"} and after in {"HIGH", "MEDIUM"}:
                kept_high += 1

            if i % batch_size == 0:
                await db.commit()
                print(
                    f"processed={i}/{len(rows)} demoted={demoted} kept={kept_high} "
                    f"deleted={deleted} created={created} unchanged={unchanged}",
                    flush=True,
                )
            processed += 1

        await db.commit()

        from sqlalchemy import func

        remaining = int((await db.scalar(select(func.count(Lead.id)))) or 0)
        remaining_high = int(
            (await db.scalar(select(func.count(Lead.id)).where(Lead.tier == "HIGH"))) or 0
        )

        summary = {
            "rule_version": scorer.rule_version,
            "processed": processed,
            "before_tier": dict(before_tier),
            "after_tier": dict(after_tier),
            "transitions": dict(transitions),
            "demoted_to_low": demoted,
            "kept_medium_or_high": kept_high,
            "deleted_leads": deleted,
            "created_leads": created,
            "unchanged": unchanged,
            "remaining_lead_rows": remaining,
            "remaining_high": remaining_high,
        }
        print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
        print(
            f"DONE processed={processed} demoted={demoted} kept={kept_high} "
            f"deleted={deleted} created={created} remaining_leads={remaining}",
            flush=True,
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--leads-only", action="store_true")
    p.add_argument("--communities", type=str, default="", help="comma-separated usernames")
    p.add_argument("--message-ids", type=str, default="", help="comma-separated message ids")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--batch-size", type=int, default=100)
    args = p.parse_args()
    communities = [c.strip() for c in args.communities.split(",") if c.strip()]
    message_ids = [int(x) for x in args.message_ids.split(",") if x.strip()]
    asyncio.run(
        run(
            leads_only=args.leads_only,
            communities=communities,
            message_ids=message_ids,
            limit=args.limit,
            batch_size=args.batch_size,
        )
    )


if __name__ == "__main__":
    main()
