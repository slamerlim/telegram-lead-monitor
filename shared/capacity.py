"""Pause intake before disk exhaustion. Never delete messages or reset cursors."""

import shutil
from sqlalchemy import text

from shared.redis_bus import RetryLater
from shared.settings import get_settings


async def storage_snapshot(session) -> dict:
    settings = get_settings()
    disk = shutil.disk_usage(settings.storage_data_path)
    size = int(await session.scalar(text("SELECT pg_database_size(current_database())")))
    free_gb = disk.free / (1024 ** 3)
    database_gb = size / (1024 ** 3)
    reasons = []
    if free_gb < settings.storage_min_free_gb:
        reasons.append("disk_free_below_reserve")
    if settings.storage_max_database_gb > 0 and database_gb >= settings.storage_max_database_gb:
        reasons.append("database_budget_reached")
    return {"free_gb": round(free_gb, 3), "database_gb": round(database_gb, 3),
            "intake_allowed": not reasons, "reasons": reasons}


async def ensure_capacity(session, redis=None) -> None:
    snapshot = await storage_snapshot(session)
    if not snapshot["intake_allowed"]:
        raise RetryLater(",".join(snapshot["reasons"]))
    if redis is not None:
        memory = await redis.info("memory")
        limit = get_settings().redis_intake_max_memory_mb * 1024 ** 2
        if limit > 0 and memory["used_memory"] >= limit:
            raise RetryLater("redis_memory_budget_reached")
