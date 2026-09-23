#!/usr/bin/env python3
"""Re-score already stored messages using community-aware commercial semantics."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from services.analyzer.app.scoring import LeadScorer
from shared.db import SessionLocal
from shared.lead_write import upsert_opportunity_lead
from shared.models import Author, Community, Lead, Message, MessageScore
from shared.score_persist import persist_message_score
from shared.settings import get_settings


async def reprocess(
    since_days: int | None,
    batch_size: int,
    max_messages: int | None,
    *,
    only_unscored: bool = False,
    start_after_id: int = 0,
) -> None:
    settings = get_settings()

    scoring_path = Path(settings.scoring_config)

    if not scoring_path.exists():
        project_root = Path(__file__).resolve().parents[1]
        scoring_path = project_root / "config" / "scoring.yaml"

    if not scoring_path.exists():
        raise FileNotFoundError(
            f"Scoring config not found: {settings.scoring_config} "
            f"or fallback {scoring_path}"
        )

    scorer = LeadScorer(str(scoring_path))

    since = (
        datetime.now(timezone.utc) - timedelta(days=since_days)
        if since_days
        else None
    )

    async with SessionLocal() as db:
        # Freeze the processing boundary so newly arriving Telegram messages
        # are not pulled into this reprocess run.
        max_stmt = select(Message.id).order_by(Message.id.desc()).limit(1)
        if since is not None:
            max_stmt = max_stmt.where(Message.message_date >= since)

        max_message_id = await db.scalar(max_stmt)
        if not max_message_id:
            print(
                "DONE processed=0 high=0 medium=0 low=0 "
                "created=0 updated=0 unchanged=0 deleted=0 deduped=0",
                flush=True,
            )
            return

        processed = high = medium = low = 0
        created = updated = unchanged = deleted = deduped = 0
        last_message_id = max(0, int(start_after_id or 0))

        while True:
            remaining = None if max_messages is None else max_messages - processed
            if remaining is not None and remaining <= 0:
                break

            take = batch_size if remaining is None else min(batch_size, remaining)
            stmt = (
                select(Message, Community, Author)
                .join(Community, Message.community_id == Community.id)
                .outerjoin(Author, Message.author_id == Author.id)
                .where(Message.id > last_message_id, Message.id <= max_message_id)
                .order_by(Message.id.asc())
                .limit(take)
            )
            if since is not None:
                stmt = stmt.where(Message.message_date >= since)
            if only_unscored:
                stmt = stmt.outerjoin(
                    MessageScore, MessageScore.message_id == Message.id
                ).where(MessageScore.id.is_(None))

            rows = (await db.execute(stmt)).all()
            if not rows:
                break

            for message, community, _author in rows:
                result = scorer.score(
                    message.text,
                    community_username=community.username,
                    community_name=community.name,
                )
                had_lead = await db.scalar(
                    select(Lead.id).where(Lead.message_id == message.id)
                )
                action, _lead = await upsert_opportunity_lead(
                    db,
                    message,
                    result,
                    status_default="NEW",
                )
                await persist_message_score(
                    db,
                    message,
                    result,
                    rule_version=scorer.rule_version,
                )

                if action == "deleted":
                    if had_lead:
                        deleted += 1
                    low += 1
                elif action == "noop":
                    low += 1
                elif action == "demoted":
                    updated += 1
                    low += 1
                elif action == "created":
                    created += 1
                    if result.tier == "HIGH":
                        high += 1
                    else:
                        medium += 1
                elif action == "updated":
                    updated += 1
                    if result.tier == "HIGH":
                        high += 1
                    else:
                        medium += 1
                elif action == "unchanged":
                    unchanged += 1
                    if result.tier == "HIGH":
                        high += 1
                    elif result.tier == "MEDIUM":
                        medium += 1
                    else:
                        low += 1
                elif action in {"deduped_updated", "deduped_skipped"}:
                    deduped += 1
                    if had_lead and action == "deduped_skipped":
                        deleted += 1
                    if result.tier == "HIGH":
                        high += 1
                    else:
                        medium += 1
                else:
                    unchanged += 1

                processed += 1
                last_message_id = message.id

            await db.commit()

            print(
                f"processed={processed} "
                f"high={high} "
                f"medium={medium} "
                f"low={low} "
                f"created={created} "
                f"updated={updated} "
                f"unchanged={unchanged} "
                f"deleted={deleted} "
                f"deduped={deduped} "
                f"last_message_id={last_message_id}/{max_message_id}",
                flush=True,
            )

        print(
            f"DONE "
            f"processed={processed} "
            f"high={high} "
            f"medium={medium} "
            f"low={low} "
            f"created={created} "
            f"updated={updated} "
            f"unchanged={unchanged} "
            f"deleted={deleted} "
            f"deduped={deduped}",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-days", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Process at most this many messages for controlled smoke tests.",
    )
    parser.add_argument(
        "--only-unscored",
        action="store_true",
        help="Skip messages that already have a message_scores row.",
    )
    parser.add_argument(
        "--start-after-id",
        type=int,
        default=0,
        help="Resume after this messages.id (exclusive).",
    )
    args = parser.parse_args()
    if args.since_days is not None and args.since_days <= 0:
        parser.error("--since-days must be > 0")
    if args.batch_size <= 0:
        parser.error("--batch-size must be > 0")
    if args.max_messages is not None and args.max_messages <= 0:
        parser.error("--max-messages must be > 0")
    if args.start_after_id < 0:
        parser.error("--start-after-id must be >= 0")
    asyncio.run(
        reprocess(
            args.since_days,
            args.batch_size,
            args.max_messages,
            only_unscored=args.only_unscored,
            start_after_id=args.start_after_id,
        )
    )
