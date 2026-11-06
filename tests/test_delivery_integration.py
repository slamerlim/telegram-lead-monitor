"""Native ephemeral PostgreSQL/Redis; never uses the configured application DB.

TLM_TEST_PG_BIN can point at a PostgreSQL bin directory. No Docker required.
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from shared.models import DispatchOutbox, Lead, Message
from shared.outbox import dispatch_one, enqueue
from shared.redis_bus import RedisBus, RetryLater


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def redis_url(tmp_path_factory):
    if not shutil.which("redis-server"):
        pytest.skip("native redis-server required")
    port = free_port()
    directory = tmp_path_factory.mktemp("tlm-test-redis")
    proc = subprocess.Popen([
        "redis-server", "--port", str(port), "--bind", "127.0.0.1",
        "--save", "", "--appendonly", "no", "--dir", str(directory),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = Redis(host="127.0.0.1", port=port)
    try:
        for _ in range(100):
            try:
                if client.ping(): break
            except Exception: time.sleep(.02)
        else: pytest.fail("ephemeral Redis did not start")
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        client.close()
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory):
    fallback = Path.home() / ".local/opt/tlm-postgresql/usr/lib/postgresql/16/bin"
    binary = Path(os.environ.get("TLM_TEST_PG_BIN", str(fallback)))
    if not (binary / "initdb").exists():
        pytest.skip("set TLM_TEST_PG_BIN to run native PostgreSQL integration tests")
    directory = tmp_path_factory.mktemp("tlm-test-postgres")
    data = directory / "data"
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(binary.parents[3] / "lib/x86_64-linux-gnu")
    init = subprocess.run([str(binary / "initdb"), "-D", str(data), "-U", "tlm_test",
                           "--auth=trust", "--encoding=UTF8", "--locale=C.UTF-8"],
                          env=env, capture_output=True, text=True)
    assert init.returncode == 0, init.stderr
    port = free_port()
    log = (directory / "postgres.log").open("w")
    proc = subprocess.Popen([str(binary / "postgres"), "-D", str(data), "-p", str(port),
                             "-k", str(directory), "-h", "127.0.0.1"],
                            env=env, stdout=log, stderr=log)
    try:
        for _ in range(100):
            ready = subprocess.run([str(binary / "pg_isready"), "-h", "127.0.0.1",
                                    "-p", str(port)], env=env, capture_output=True)
            if ready.returncode == 0: break
            time.sleep(.05)
        else: pytest.fail("ephemeral PostgreSQL did not start")
        env.update(POSTGRES_HOST="127.0.0.1", POSTGRES_PORT=str(port), POSTGRES_DB="postgres",
                   POSTGRES_USER="tlm_test", POSTGRES_PASSWORD="test")
        migrated = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                                  env=env, capture_output=True, text=True)
        assert migrated.returncode == 0, migrated.stderr
        yield f"postgresql+asyncpg://tlm_test:test@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([str(binary / "pg_ctl"), "-D", str(data), "stop", "-m", "fast"],
                       env=env, capture_output=True, timeout=10)
        proc.wait(timeout=10)
        log.close()


@pytest_asyncio.fixture
async def sessions(pg_url):
    engine = create_async_engine(pg_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_worker_recovered_by_new_consumer(redis_url):
    bus = RedisBus(redis_url, reclaim_idle_ms=30)
    stream = f"test:{uuid.uuid4().hex}"
    msg = await bus.publish(stream, {"id": 1})
    rows = await bus.consume(stream, "g", "old", block_ms=1, count=1)
    broken = AsyncMock(side_effect=RuntimeError("injected crash"))
    assert not await bus.handle(stream, "g", "old", msg, rows[0][1][0][1], broken)
    await asyncio.sleep(.06)
    rows = await bus.consume(stream, "g", "new", block_ms=1, count=1)
    assert rows[0][1][0][0] == msg
    recovered = AsyncMock()
    assert await bus.handle(stream, "g", "new", msg, rows[0][1][0][1], recovered)
    recovered.assert_awaited_once_with({"id": 1})
    assert (await bus.redis.xpending(stream, "g"))["pending"] == 0
    await bus.close()


@pytest.mark.asyncio
async def test_heartbeat_protects_slow_review(redis_url):
    bus = RedisBus(redis_url, reclaim_idle_ms=90)
    stream = f"test:{uuid.uuid4().hex}"
    msg = await bus.publish(stream, {"id": 1})
    await bus.consume(stream, "g", "working", block_ms=1, count=1)
    async with bus.processing(stream, "g", "working", msg):
        await asyncio.sleep(.22)
        assert not await bus.consume(stream, "g", "competitor", block_ms=1, count=1)
    await asyncio.sleep(.12)
    assert (await bus.consume(stream, "g", "competitor", block_ms=1))[0][1][0][0] == msg
    await bus.close()


@pytest.mark.asyncio
async def test_deferral_survives_more_than_poison_retry_budget(redis_url):
    bus = RedisBus(redis_url, reclaim_idle_ms=15)
    stream = f"test:{uuid.uuid4().hex}"
    msg = await bus.publish(stream, {"id": 1})
    limited = AsyncMock(side_effect=RetryLater("capacity"))
    for i in range(7):
        await asyncio.sleep(.03)
        consumer = f"consumer-{i}"
        rows = await bus.consume(stream, "g", consumer, block_ms=1, count=1)
        assert not await bus.handle(stream, "g", consumer, msg, rows[0][1][0][1], limited)
    assert (await bus.redis.xpending(stream, "g"))["pending"] == 1
    assert await bus.redis.xlen(stream + ":dead_letter") == 0
    assert await bus.redis.hlen(stream + ":failures:g") == 0
    await bus.close()


@pytest.mark.asyncio
async def test_poison_payload_preserved_across_consumer_restarts(redis_url):
    bus = RedisBus(redis_url, reclaim_idle_ms=15)
    stream = f"test:{uuid.uuid4().hex}"
    msg = await bus.redis.xadd(stream, {"payload": "not-json"})
    for i in range(5):
        await asyncio.sleep(.03)
        consumer = f"consumer-{i}"
        rows = await bus.consume(stream, "g", consumer, block_ms=1, count=1)
        assert not await bus.handle(stream, "g", consumer, msg, rows[0][1][0][1], AsyncMock())
    dead = await bus.redis.xrange(stream + ":dead_letter")
    assert len(dead) == 1 and dead[0][1]["payload"] == "not-json"
    assert dead[0][1]["original_id"] == msg
    assert (await bus.redis.xpending(stream, "g"))["pending"] == 0
    await bus.close()


@pytest.mark.asyncio
async def test_outbox_atomic_rollback_dedup_and_publish_retry(sessions, redis_url):
    key = uuid.uuid4().hex
    stream = f"test:{key}"
    async with sessions() as db:
        await enqueue(db, stream, {"hello": "world"}, key=key)
        await db.rollback()
        assert await db.scalar(select(DispatchOutbox.id).where(DispatchOutbox.event_key == key)) is None
        await enqueue(db, stream, {"hello": "world"}, key=key)
        await enqueue(db, stream, {"hello": "world"}, key=key)
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(DispatchOutbox).where(
            DispatchOutbox.event_key == key)) == 1
        assert await dispatch_one(db, SimpleNamespace(publish=AsyncMock(side_effect=ConnectionError())))
        row = await db.scalar(select(DispatchOutbox).where(DispatchOutbox.event_key == key))
        assert row.published_at is None and row.attempts == 1
        row.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
        bus = RedisBus(redis_url)
        assert await dispatch_one(db, bus)
        assert await bus.redis.xlen(stream) == 1
        await db.refresh(row)
        assert row.published_at is not None and row.attempts == 2
        await bus.close()


@pytest.mark.asyncio
async def test_analyzer_replay_keeps_one_message_lead_and_review(sessions, monkeypatch):
    import services.analyzer.app.main as am
    from services.analyzer.app.scoring import LeadScorer
    from shared.events import MessageEvent
    from shared.models import Community
    monkeypatch.setattr(am, "SessionLocal", sessions)
    monkeypatch.setattr(am.settings, "commercial_ai_enabled", True)
    monkeypatch.setattr(am.settings, "commercial_discovery_enabled", False)
    async with sessions() as db:
        community = Community(telegram_ref="@delivery_test")
        db.add(community)
        await db.commit()
        cid = community.id
    worker = am.Analyzer.__new__(am.Analyzer)
    worker.scorer = LeadScorer("config/scoring.yaml")
    worker.semantic = SimpleNamespace(score=lambda text: None)
    event = MessageEvent(community_id=cid, telegram_chat_id=999, community_name="Delivery test",
                         message_id=1, message_date=datetime.now(timezone.utc),
                         message_text="Looking for a developer to build a Binance trading bot. Budget $3000.")
    await worker.process(event)
    await worker.process(event)
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 1
        assert await db.scalar(select(func.count()).select_from(Lead)) == 1
        assert await db.scalar(select(func.count()).select_from(DispatchOutbox).where(
            DispatchOutbox.event_key.like("review:%"))) == 1


@pytest.mark.asyncio
async def test_commercial_limit_is_deferral_not_ack_or_decision(sessions, monkeypatch):
    import services.commercial_ai.app.main as ca
    from shared.models import Community
    monkeypatch.setattr(ca, "SessionLocal", sessions)
    monkeypatch.setattr(ca.get_settings(), "commercial_ai_enabled", True)
    monkeypatch.setattr(ca.get_settings(), "commercial_ai_max_reviews_per_hour", 1)
    monkeypatch.setattr(ca, "_reviews_last_hour", AsyncMock(return_value=1))
    async with sessions() as db:
        community = Community(telegram_ref="@rate_limit_test")
        db.add(community)
        await db.flush()
        message = Message(community_id=community.id, telegram_chat_id=888, telegram_message_id=1,
                          message_date=datetime.now(timezone.utc), text="Need a trading bot developer")
        db.add(message)
        await db.flush()
        lead = Lead(message_id=message.id, tier="HIGH", status="NEW")
        db.add(lead)
        await db.commit()
        payload = {"lead_id": lead.id, "message_id": lead.message_id}
    backend = SimpleNamespace(validate=AsyncMock())
    with pytest.raises(RetryLater):
        await ca.process_payload(payload, backend)
    backend.validate.assert_not_called()


@pytest.mark.asyncio
async def test_trim_retains_pending_and_undelivered_for_every_group(redis_url):
    bus = RedisBus(redis_url)
    stream = f"test:{uuid.uuid4().hex}"
    ids = [await bus.publish(stream, {"id": i}) for i in range(5)]
    await bus.consume(stream, "fast", "fast-consumer", count=4, block_ms=1)
    await bus.ack(stream, "fast", *ids[:4])
    await bus.consume(stream, "slow", "slow-consumer", count=3, block_ms=1)
    await bus.ack(stream, "slow", ids[0])
    assert await bus.trim_acknowledged(stream) == 1
    assert [row[0] for row in await bus.redis.xrange(stream)] == ids[1:]
    await bus.ack(stream, "slow", ids[1], ids[2])
    assert await bus.trim_acknowledged(stream) == 1
    assert [row[0] for row in await bus.redis.xrange(stream)] == ids[2:]
    await bus.close()
