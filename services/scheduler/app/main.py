import asyncio

from sqlalchemy import select

from shared.db import SessionLocal
from shared.logging import configure_logging
from shared.models import Community, ScanRun
from shared.outbox import enqueue
from shared.redis_bus import RedisBus, SCAN_STREAM
from shared.settings import get_settings
from shared.capacity import ensure_capacity

settings = get_settings()
configure_logging(settings.log_level)


async def enqueue_scan(bus: RedisBus, days: int) -> None:
    async with SessionLocal() as db:
        await ensure_capacity(db, bus.redis)
        communities = list((await db.scalars(select(Community).where(Community.enabled.is_(True)))).all())
        if not communities:
            return
        for community in communities:
            # Avoid piling up scans when the previous one is still queued/running.
            existing = await db.scalar(
                select(ScanRun.id).where(
                    ScanRun.community_id == community.id,
                    ScanRun.status.in_(["queued", "running"]),
                ).limit(1)
            )
            if existing:
                continue
            run = ScanRun(community_id=community.id, days=days, status="queued")
            db.add(run)
            await db.flush()
            await enqueue(db, SCAN_STREAM,
                          {"run_id": run.id, "community_id": community.id, "days": days},
                          key=f"scan:{run.id}")
        await db.commit()


async def main() -> None:
    bus = RedisBus(settings.redis_url)
    try:
        while True:
            try:
                await enqueue_scan(bus, settings.scheduler_scan_days)
            except Exception as exc:
                import structlog
                structlog.get_logger().exception("scheduler_cycle_failed", error=str(exc))
            await asyncio.sleep(settings.scheduler_interval_seconds)
    finally:
        await bus.close()


if __name__ == "__main__":
    asyncio.run(main())
