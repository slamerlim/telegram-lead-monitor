import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Any

from redis.asyncio import Redis


MESSAGES_STREAM = "telegram:messages"
SCAN_STREAM = "telegram:scan_requests"

# Re-export commercial AI stream for analyzer publish hook.
from shared.commercial_ai.streams import COMMERCIAL_AI_REVIEW_STREAM  # noqa: E402

log = logging.getLogger(__name__)


class RetryLater(Exception):
    """Keep the event pending without charging the poison-message retry budget."""


def consumer_name(prefix: str) -> str:
    # PIDs repeat across containers and restarts; Redis ownership must not.
    return f"{prefix}-{uuid.uuid4().hex}"


_HEARTBEAT = """
local p = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #p == 0 or p[1][2] ~= ARGV[3] then return 0 end
redis.call('XCLAIM', KEYS[1], ARGV[1], ARGV[3], 0, ARGV[2], 'JUSTID')
return 1
"""

_FAIL = """
local p = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #p == 0 or p[1][2] ~= ARGV[3] then return 0 end
local n = redis.call('HINCRBY', KEYS[2], ARGV[2], 1)
if n >= tonumber(ARGV[4]) then
  redis.call('XADD', KEYS[3], '*', 'payload', ARGV[5],
    'original_stream', KEYS[1], 'original_id', ARGV[2],
    'group', ARGV[1], 'error_type', ARGV[6], 'failures', n)
  redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
  redis.call('HDEL', KEYS[2], ARGV[2])
end
return n
"""


class RedisBus:
    def __init__(self, url: str, *, reclaim_idle_ms: int = 60_000) -> None:
        self.redis = Redis.from_url(url, decode_responses=True)
        self.reclaim_idle_ms = reclaim_idle_ms
        self._claim_cursors: dict[tuple[str, str], str] = {}

    async def close(self) -> None:
        await self.redis.aclose()

    async def publish(self, stream: str, payload: dict[str, Any], maxlen: int | None = None) -> str:
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
        key = (stream, group)
        claimed = await self.redis.xautoclaim(
            stream, group, consumer, self.reclaim_idle_ms,
            start_id=self._claim_cursors.get(key, "0-0"), count=count,
        )
        self._claim_cursors[key] = claimed[0]
        messages = claimed[1]
        if len(claimed) > 2 and claimed[2]:
            # Redis reports IDs whose payloads were previously trimmed. Never
            # describe these as successfully processed; operator recovery is needed.
            log.error("stream_payloads_missing stream=%s group=%s ids=%s", stream, group, claimed[2])
        if messages:
            return [(stream, messages)]
        return await self.redis.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)

    async def ack(self, stream: str, group: str, *message_ids: str) -> int:
        if not message_ids:
            return 0
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.xack(stream, group, *message_ids)
            pipe.hdel(f"{stream}:failures:{group}", *message_ids)
            result = await pipe.execute()
        return result[0]

    @asynccontextmanager
    async def processing(self, stream: str, group: str, consumer: str, message_id: str):
        """Renew ownership while a slow Telegram/SDK call is in flight.

        Workers consume one event at a time so undrained prefetch cannot expire.
        If ownership cannot be renewed, cancel processing before it commits.
        """
        owner = asyncio.current_task()

        async def renew():
            ok = await self.redis.eval(_HEARTBEAT, 1, stream, group, message_id, consumer)
            if not ok:
                raise RuntimeError("stream event ownership lost")

        async def heartbeat():
            try:
                while True:
                    await asyncio.sleep(max(0.01, self.reclaim_idle_ms / 3000))
                    await renew()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("stream_lease_failed stream=%s id=%s", stream, message_id)
                owner.cancel()

        await renew()
        task = asyncio.create_task(heartbeat())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def handle(self, stream, group, consumer, message_id, fields, handler) -> bool:
        try:
            async with self.processing(stream, group, consumer, message_id):
                payload = json.loads(fields["payload"])
                await handler(payload)
                await self.ack(stream, group, message_id)
            return True
        except RetryLater as exc:
            log.info("stream_deferred stream=%s id=%s reason=%s", stream, message_id, exc)
            return False

        except Exception as exc:
            log.exception("stream_processing_failed stream=%s id=%s", stream, message_id)
            failures = await self.redis.eval(
                _FAIL, 3, stream, f"{stream}:failures:{group}", f"{stream}:dead_letter",
                group, message_id, consumer, 5, fields.get("payload", ""), type(exc).__name__,
            )
            if failures >= 5:
                log.error("stream_dead_lettered stream=%s id=%s failures=%s", stream, message_id, failures)
            return False

    async def trim_acknowledged(self, stream: str) -> int:
        """Remove only delivered, acknowledged transport copies for ALL groups.

        Pending and never-delivered work is retained. Raw messages live in Postgres.
        Streams with no consumer group (including disabled experiments) stay intact.
        """
        from redis.exceptions import ResponseError

        try:
            groups = await self.redis.xinfo_groups(stream)
        except ResponseError as exc:
            if "no such key" in str(exc).lower():
                return 0
            raise
        boundaries = []
        for group in groups:
            pending = await self.redis.xpending_range(stream, group["name"], "-", "+", 1)
            boundaries.append(pending[0]["message_id"] if pending else group["last-delivered-id"])
        if not boundaries:
            return 0
        bound = min(boundaries, key=lambda value: tuple(map(int, value.split("-"))))
        if bound == "0-0":
            return 0
        return await self.redis.xtrim(stream, minid=bound, approximate=False)
