"""At-least-once dispatch. Consumers must be idempotent across publish/commit crashes."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from shared.models import DispatchOutbox


async def enqueue(session, stream: str, payload: dict, *, key: str) -> None:
    await session.execute(
        insert(DispatchOutbox).values(
            event_key=key, stream=stream,
            payload=json.dumps(payload, ensure_ascii=False, default=str),
        ).on_conflict_do_nothing(index_elements=[DispatchOutbox.event_key])
    )


async def dispatch_one(session, bus) -> bool:
    now = datetime.now(timezone.utc)
    row = await session.scalar(
        select(DispatchOutbox)
        .where(DispatchOutbox.published_at.is_(None), DispatchOutbox.available_at <= now)
        .order_by(DispatchOutbox.available_at, DispatchOutbox.id)
        .with_for_update(skip_locked=True).limit(1)
    )
    if row is None:
        return False
    row.attempts += 1
    try:
        # Keep unprocessed stream payloads: MAXLEN can remove pending work.
        await asyncio.wait_for(bus.publish(row.stream, json.loads(row.payload), maxlen=None), 5)
    except Exception as exc:
        row.last_error = type(exc).__name__
        row.available_at = now + timedelta(seconds=min(300, 2 ** min(row.attempts, 8)))
    else:
        row.published_at = datetime.now(timezone.utc)
        row.last_error = None
    await session.commit()
    return True
