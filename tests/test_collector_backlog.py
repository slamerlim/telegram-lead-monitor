"""History simulation follows Telethon's limit/order/date semantics, no Telegram calls."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import services.collector.app.main as cm


def make_collector(monkeypatch, *, cursor=100, total=500, old=False, fail_at=None, empty=False):
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(last_message_id=cursor)
    ids = list(range(101, 101 + total))
    records = [SimpleNamespace(
        id=i, date=now - timedelta(days=10 if old else 0),
        message="" if empty else f"message {i}", sender_id=None, is_reply=False,
    ) for i in ids]
    sent = []
    calls = []

    class Session:
        async def get(self, *args, **kwargs): return row
        async def commit(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False

    class Client:
        async def iter_messages(self, entity, **kwargs):
            calls.append(kwargs)
            filtered = [m for m in records if m.id > kwargs.get("min_id", 0)]
            if "offset_date" in kwargs:
                filtered = [m for m in filtered if m.date > kwargs["offset_date"]]
            filtered.sort(key=lambda m: m.id, reverse=not kwargs.get("reverse", False))
            for message in filtered[:kwargs["limit"]]: yield message

    async def publish(stream, event):
        if fail_at is not None and event["message_id"] == fail_at:
            raise ConnectionError("injected Redis failure")
        sent.append(event["message_id"])

    monkeypatch.setattr(cm, "SessionLocal", Session)
    monkeypatch.setattr(cm.settings, "collector_batch_size", 200)
    c = cm.Collector.__new__(cm.Collector)
    c.client = Client()
    c.bus = SimpleNamespace(publish=publish)
    c.sender_cache = {}
    c._get_entity = AsyncMock(return_value=SimpleNamespace(id=99, username="example", title="Example"))
    return c, row, sent, calls, now


@pytest.mark.asyncio
async def test_all_500_messages_collected_and_next_scan_empty(monkeypatch):
    c, row, sent, calls, now = make_collector(monkeypatch)
    assert await c.scan_community(1, "@example", now - timedelta(days=2), 1) == (500, 500)
    assert sent == list(range(101, 601))
    assert row.last_message_id == 600
    assert len(calls) == 3
    assert await c.scan_community(1, "@example", now - timedelta(days=2), 2) == (0, 0)


@pytest.mark.asyncio
async def test_existing_cursor_drains_outage_older_than_date_window(monkeypatch):
    c, row, sent, calls, now = make_collector(monkeypatch, old=True)
    await c.scan_community(1, "@example", now - timedelta(days=2), 1)
    assert len(sent) == 500
    assert all("offset_date" not in kwargs for kwargs in calls)


@pytest.mark.asyncio
async def test_first_scan_respects_history_window(monkeypatch):
    c, row, sent, calls, now = make_collector(monkeypatch, cursor=0, old=True)
    await c.scan_community(1, "@example", now - timedelta(days=2), 1)
    assert sent == [] and row.last_message_id == 0


@pytest.mark.asyncio
async def test_failure_keeps_completed_page_and_replays_partial_page(monkeypatch):
    c, row, sent, calls, now = make_collector(monkeypatch, fail_at=350)
    with pytest.raises(ConnectionError):
        await c.scan_community(1, "@example", now - timedelta(days=2), 1)
    assert row.last_message_id == 300
    assert max(sent) == 349
    initial = set(sent)
    c, recovered, sent, _, now = make_collector(monkeypatch, cursor=row.last_message_id)
    await c.scan_community(1, "@example", now - timedelta(days=2), 2)
    assert initial | set(sent) == set(range(101, 601))
    assert recovered.last_message_id == 600


@pytest.mark.asyncio
async def test_empty_messages_advance_contiguous_cursor(monkeypatch):
    c, row, sent, _, now = make_collector(monkeypatch, empty=True)
    assert await c.scan_community(1, "@example", now - timedelta(days=2), 1) == (500, 0)
    assert row.last_message_id == 600
