import json
from typing import Any

from redis.asyncio import Redis


MESSAGES_STREAM = "telegram:messages"
SCAN_STREAM = "telegram:scan_requests"

# Re-export commercial AI stream for analyzer publish hook.
from shared.commercial_ai.streams import COMMERCIAL_AI_REVIEW_STREAM  # noqa: E402


class RedisBus:
    def __init__(self, url: str) -> None:
        self.redis = Redis.from_url(url, decode_responses=True)

    async def close(self) -> None:
        await self.redis.aclose()

    async def publish(self, stream: str, payload: dict[str, Any], maxlen: int | None = 100_000) -> str:
        fields = {"payload": json.dumps(payload, ensure_ascii=False, default=str)}
        return await self.redis.xadd(stream, fields, maxlen=maxlen, approximate=True)

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        block_ms: int = 5000,
        count: int = 20,
    ):
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        return await self.redis.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)

    async def ack(self, stream: str, group: str, *message_ids: str) -> int:
        if not message_ids:
            return 0
        return await self.redis.xack(stream, group, *message_ids)
