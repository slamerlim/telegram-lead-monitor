"""Collector incremental cursor invariants."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.collector.app.main import Collector


class _FakeMessage:
    def __init__(self, message_id: int, days_ago: int = 0):
        self.id = message_id
        self.message = f"text-{message_id}"
        self.date = datetime(2026, 9, 20, tzinfo=timezone.utc)
        self.sender_id = None
        self.is_reply = False


@pytest.mark.asyncio
async def test_iter_messages_passes_min_id_to_telethon():
    collector = Collector.__new__(Collector)
    collector.client = MagicMock()

    async def _fake_iter(*_args, **kwargs):
        assert kwargs.get("min_id") == 12835520
        for message in [_FakeMessage(12835521), _FakeMessage(12835522)]:
            yield message

    collector.client.iter_messages = _fake_iter

    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ids = [
        message.id
        async for message in collector._iter_messages(
            entity=object(),
            since=since,
            min_id=12835520,
        )
    ]
    assert ids == [12835521, 12835522]


@pytest.mark.asyncio
async def test_scan_community_advances_cursor_monotonically():
    collector = Collector.__new__(Collector)
    collector.client = MagicMock()
    collector.bus = SimpleNamespace(publish=AsyncMock())
    collector.sender_cache = {}

    entity = SimpleNamespace(id=99, username="KuCoin_Exchange", title="KuCoin", broadcast=False)
    collector._get_entity = AsyncMock(return_value=entity)

    async def _fake_iter(_entity, _since, min_id=0):
        assert min_id == 100
        for message_id in (101, 105):
            yield _FakeMessage(message_id)

    collector._iter_messages = _fake_iter

    community_row = SimpleNamespace(
        last_message_id=100,
        telegram_chat_id=None,
        name=None,
        username=None,
        url=None,
        kind=None,
        resolve_status=None,
        last_error="old",
    )

    class _Session:
        async def get(self, _model, _id):
            return community_row

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    import services.collector.app.main as collector_mod

    original = collector_mod.SessionLocal
    collector_mod.SessionLocal = lambda: _Session()
    try:
        seen, published = await collector.scan_community(
            community_id=1,
            telegram_ref="@KuCoin_Exchange",
            since=datetime(2026, 9, 1, tzinfo=timezone.utc),
            run_id=1,
        )
    finally:
        collector_mod.SessionLocal = original

    assert seen == 2
    assert published == 2
    assert community_row.last_message_id == 105
    assert collector.bus.publish.await_count == 2
