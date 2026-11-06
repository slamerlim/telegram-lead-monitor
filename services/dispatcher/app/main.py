import asyncio
import logging
import time

from shared.db import SessionLocal
from shared.logging import configure_logging
from shared.outbox import dispatch_one
from shared.redis_bus import RedisBus
from shared.redis_bus import MESSAGES_STREAM, SCAN_STREAM, COMMERCIAL_AI_REVIEW_STREAM
from shared.commercial_ai.streams import COMMERCIAL_DISCOVERY_STREAM, COMMERCIAL_EPISODE_SHADOW_STREAM
from shared.settings import get_settings


async def main():
    configure_logging()
    bus = RedisBus(get_settings().redis_url)
    next_cleanup = 0.0
    try:
        while True:
            try:
                if time.monotonic() >= next_cleanup:
                    for stream in (MESSAGES_STREAM, SCAN_STREAM, COMMERCIAL_AI_REVIEW_STREAM,
                                   COMMERCIAL_DISCOVERY_STREAM, COMMERCIAL_EPISODE_SHADOW_STREAM):
                        await bus.trim_acknowledged(stream)
                    next_cleanup = time.monotonic() + 60
                async with SessionLocal() as session:
                    worked = await dispatch_one(session, bus)
                if not worked:
                    await asyncio.sleep(1)
            except Exception:
                logging.getLogger(__name__).exception("dispatch_failed")
                await asyncio.sleep(2)
    finally:
        await bus.close()


if __name__ == "__main__":
    asyncio.run(main())
