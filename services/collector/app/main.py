import asyncio
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import select
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, Chat, User

from shared.db import SessionLocal
from shared.events import MessageEvent
from shared.logging import configure_logging
from shared.models import Community, ScanRun
from shared.redis_bus import MESSAGES_STREAM, RedisBus, SCAN_STREAM
from shared.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)


def chat_url(entity: Channel | Chat, username: str | None, message_id: int) -> str | None:
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    chat_id = getattr(entity, "id", None)
    if chat_id is not None:
        return f"https://t.me/c/{chat_id}/{message_id}"
    return None


def entity_kind(entity: object) -> str:
    if isinstance(entity, Channel):
        return "channel" if getattr(entity, "broadcast", False) else "group"
    if isinstance(entity, Chat):
        return "group"
    return "unknown"


class Collector:
    def __init__(self) -> None:
        self.bus = RedisBus(settings.redis_url)
        self.client = TelegramClient(
            settings.telegram_session_path,
            settings.telegram_api_id,
            settings.telegram_api_hash,
            flood_sleep_threshold=30,
            sequential_updates=False,
        )
        self.sender_cache: dict[int, tuple[str | None, str | None, str | None, bool | None]] = {}

    async def close(self) -> None:
        await self.client.disconnect()
        await self.bus.close()

    async def ensure_login(self) -> None:
        await self.client.start(phone=settings.telegram_phone)

    async def run_forever(self) -> None:
        await self.ensure_login()
        consumer = f"collector-{os.getpid()}"
        while True:
            entries = await self.bus.consume(SCAN_STREAM, "collector-workers", consumer, block_ms=5000, count=5)
            if not entries:
                continue
            for _stream, messages in entries:
                for redis_message_id, fields in messages:
                    try:
                        import json
                        payload = json.loads(fields["payload"])
                        await self.execute_scan(payload)
                        await self.bus.ack(SCAN_STREAM, "collector-workers", redis_message_id)
                    except Exception as exc:
                        import structlog
                        structlog.get_logger().exception("scan_failed", error=str(exc))
                        # Ack after logging so one poison message does not block the consumer forever.
                        await self.bus.ack(SCAN_STREAM, "collector-workers", redis_message_id)

    async def execute_scan(self, payload: dict) -> None:
        run_id = int(payload["run_id"])
        community_id = payload.get("community_id")
        days = int(payload.get("days", settings.scan_default_days))
        since = datetime.now(timezone.utc) - timedelta(days=days)
        async with SessionLocal() as db:
            run = await db.get(ScanRun, run_id)
            if not run:
                return
            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            await db.commit()

        async with SessionLocal() as db:
            stmt = select(Community).where(Community.enabled.is_(True))
            if community_id is not None:
                stmt = stmt.where(Community.id == int(community_id))
            communities = list((await db.scalars(stmt)).all())

        total_seen = 0
        total_published = 0
        try:
            for community in communities:
                seen, published = await self.scan_community(community.id, community.telegram_ref, since, run_id)
                total_seen += seen
                total_published += published
                async with SessionLocal() as db:
                    row = await db.get(Community, community.id)
                    if row:
                        row.last_scanned_at = datetime.now(timezone.utc)
                        await db.commit()
            async with SessionLocal() as db:
                run = await db.get(ScanRun, run_id)
                if run:
                    run.status = "completed"
                    run.finished_at = datetime.now(timezone.utc)
                    run.messages_seen = total_seen
                    run.messages_published = total_published
                    await db.commit()
        except Exception as exc:
            async with SessionLocal() as db:
                run = await db.get(ScanRun, run_id)
                if run:
                    run.status = "failed"
                    run.finished_at = datetime.now(timezone.utc)
                    run.messages_seen = total_seen
                    run.messages_published = total_published
                    run.error = repr(exc)
                    await db.commit()
            raise

    async def scan_community(self, community_id: int, telegram_ref: str, since: datetime, run_id: int) -> tuple[int, int]:
        entity = await self.client.get_entity(telegram_ref)
        username = getattr(entity, "username", None)
        name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or telegram_ref
        kind = entity_kind(entity)
        seen = 0
        published = 0
        async for message in self._iter_messages(entity, since):
            seen += 1
            text = message.message or ""
            if not text.strip():
                continue
            sender = None
            if message.sender_id:
                sender = await self._get_sender(message.sender_id)
            author_username = author_name = author_bio = None
            author_is_bot = None
            author_id = message.sender_id
            if sender:
                author_username, author_name, author_bio, author_is_bot = sender
            event = MessageEvent(
                community_id=community_id,
                telegram_chat_id=int(entity.id),
                community_name=name,
                community_username=username,
                community_url=(f"https://t.me/{username}" if username else None),
                message_id=int(message.id),
                message_url=chat_url(entity, username, int(message.id)),
                message_date=message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc),
                author_id=int(author_id) if author_id else None,
                author_username=author_username,
                author_name=author_name,
                author_bio=author_bio,
                author_is_bot=author_is_bot,
                message_text=text.strip(),
                is_reply=message.is_reply,
            )
            await self.bus.publish(MESSAGES_STREAM, event.model_dump(mode="json"))
            published += 1
        return seen, published

    async def _iter_messages(self, entity: object, since: datetime):
        while True:
            try:
                async for message in self.client.iter_messages(entity, offset_date=datetime.now(timezone.utc), limit=None):
                    msg_date = message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
                    if msg_date < since:
                        break
                    yield message
                break
            except FloodWaitError as exc:
                await asyncio.sleep(exc.seconds + settings.collector_flood_wait_buffer_seconds)

    async def _get_sender(self, sender_id: int):
        if sender_id in self.sender_cache:
            return self.sender_cache[sender_id]
        try:
            entity = await self.client.get_entity(sender_id)
        except Exception:
            return None
        if isinstance(entity, User):
            first = entity.first_name or ""
            last = entity.last_name or ""
            display = " ".join(part for part in [first, last] if part).strip() or None
            result = (entity.username, display, getattr(entity, "about", None), entity.bot)
        else:
            result = (None, getattr(entity, "title", None), None, False)
        self.sender_cache[sender_id] = result
        return result


async def main() -> None:
    collector = Collector()
    try:
        await collector.run_forever()
    finally:
        await collector.close()


if __name__ == "__main__":
    asyncio.run(main())
