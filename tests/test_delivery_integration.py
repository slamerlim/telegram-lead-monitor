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
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.community_id == cid)) == 1
        assert await db.scalar(select(func.count()).select_from(Lead).join(Message).where(Message.community_id == cid)) == 1
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


async def seed_sales_lead(sessions, *, age_hours=1, body=None, status="AI_CONFIRMED"):
    from shared.models import Community
    from shared.opportunity import opportunity_key
    async with sessions() as db:
        community = Community(telegram_ref="@sales_" + uuid.uuid4().hex[:12], name="Synthetic test source")
        db.add(community)
        await db.flush()
        message = Message(community_id=community.id, telegram_chat_id=999,
            telegram_message_id=1, message_date=datetime.now(timezone.utc) - timedelta(hours=age_hours),
            message_url="https://t.me/synthetic_test/1", text=body or "Paid trading bot project " + uuid.uuid4().hex)
        db.add(message)
        await db.flush()
        lead = Lead(message_id=message.id, tier="HIGH", status=status, lead_type="BOT_PURCHASE",
                    opportunity_key=opportunity_key(community.id, message.text))
        db.add(lead)
        await db.commit()
        return lead.id, message.id


@pytest.mark.asyncio
async def test_owner_alert_atomicity_and_cross_community_dedup(sessions, monkeypatch):
    from shared.models import LeadNotification
    from shared.sales import queue_owner_alert
    from shared.settings import get_settings
    monkeypatch.setattr(get_settings(), "owner_notifications_enabled", True)
    body = "Need a paid trading bot " + uuid.uuid4().hex
    first, second = await seed_sales_lead(sessions, body=body), await seed_sales_lead(sessions, body=body)
    async with sessions() as db:
        lead, message = await db.get(Lead, first[0]), await db.get(Message, first[1])
        alert = await queue_owner_alert(db, lead, message)
        rolled_id = alert.id
        await db.rollback()
        assert await db.get(LeadNotification, rolled_id) is None
        assert await db.scalar(select(DispatchOutbox.id).where(DispatchOutbox.event_key == f"owner-alert:{rolled_id}")) is None
        lead, message = await db.get(Lead, first[0]), await db.get(Message, first[1])
        alert = await queue_owner_alert(db, lead, message)
        await db.commit()
        assert alert.status == "PENDING" and alert.target == "me"
        assert await queue_owner_alert(db, await db.get(Lead, second[0]), await db.get(Message, second[1])) is None
        await db.commit()


@pytest.mark.asyncio
async def test_stale_leads_never_queue_or_send(sessions, monkeypatch):
    from shared.models import LeadNotification
    from shared.sales import queue_owner_alert, send_owner_alert
    from shared.settings import get_settings
    monkeypatch.setattr(get_settings(), "owner_notifications_enabled", True)
    old = await seed_sales_lead(sessions, age_hours=49)
    fresh = await seed_sales_lead(sessions)
    client = AsyncMock()
    async with sessions() as db:
        assert await queue_owner_alert(db, await db.get(Lead, old[0]), await db.get(Message, old[1])) is None
        message = await db.get(Message, fresh[1])
        alert = await queue_owner_alert(db, await db.get(Lead, fresh[0]), message)
        message.message_date -= timedelta(days=3)
        await db.commit()
        await send_owner_alert(db, client, {"notification_id": alert.id})
        assert alert.status == "SUPPRESSED" and alert.reason == "stale_before_delivery"
    client.assert_not_called()


@pytest.mark.asyncio
async def test_alert_retry_uses_same_telegram_random_id_after_commit_failure(sessions, monkeypatch):
    from shared.models import LeadNotification
    from shared.sales import queue_owner_alert, send_owner_alert
    from shared.settings import get_settings
    monkeypatch.setattr(get_settings(), "owner_notifications_enabled", True)
    ids = await seed_sales_lead(sessions)
    client = AsyncMock(return_value=SimpleNamespace(updates=[]))
    client.get_input_entity.return_value = "saved-messages"
    async with sessions() as db:
        alert = await queue_owner_alert(db, await db.get(Lead, ids[0]), await db.get(Message, ids[1]))
        await db.commit()
        alert_id, random_id = alert.id, alert.random_id
        monkeypatch.setattr(db, "commit", AsyncMock(side_effect=ConnectionError("injected DB failure after send")))
        with pytest.raises(ConnectionError):
            await send_owner_alert(db, client, {"notification_id": alert_id})
    async with sessions() as db:
        await send_owner_alert(db, client, {"notification_id": alert_id})
        await send_owner_alert(db, client, {"notification_id": alert_id})
        assert (await db.get(LeadNotification, alert_id)).status == "SENT"
    assert client.await_count == 2
    assert [call.args[0].random_id for call in client.await_args_list] == [random_id, random_id]
    client.get_input_entity.assert_awaited_with("me")


@pytest.mark.asyncio
async def test_flood_wait_defers_until_telegram_deadline(sessions, monkeypatch):
    from telethon.errors import FloodWaitError
    from shared.sales import queue_owner_alert, send_owner_alert
    from shared.settings import get_settings
    monkeypatch.setattr(get_settings(), "owner_notifications_enabled", True)
    ids = await seed_sales_lead(sessions)
    client = AsyncMock(side_effect=FloodWaitError(request=None, capture=120))
    async with sessions() as db:
        alert = await queue_owner_alert(db, await db.get(Lead, ids[0]), await db.get(Message, ids[1]))
        await db.commit()
        with pytest.raises(RetryLater): await send_owner_alert(db, client, {"notification_id": alert.id})
        assert alert.retry_after > datetime.now(timezone.utc) + timedelta(seconds=120)
        with pytest.raises(RetryLater): await send_owner_alert(db, client, {"notification_id": alert.id})
    assert client.await_count == 1


@pytest.mark.asyncio
async def test_inbox_auth_feedback_and_pilot_keep_validation_untouched(sessions, monkeypatch, redis_url):
    import httpx
    from services.api.app.main import app
    from shared.db import get_session
    from shared.models import HumanLabel, LeadNotification
    from shared.sales import queue_owner_alert
    from shared.settings import get_settings
    cfg = get_settings()
    for name, value in {"commercial_ops_enabled": True, "commercial_operator_ids": "test_owner",
                        "label_write_token": "test-only", "owner_notifications_enabled": True,
                        "redis_url": redis_url}.items():
        monkeypatch.setattr(cfg, name, value)
    async def override():
        async with sessions() as db: yield db
    app.dependency_overrides[get_session] = override
    ids = await seed_sales_lead(sessions)
    try:
        async with sessions() as db:
            duplicate_body = (await db.get(Message, ids[1])).text
            alert = await queue_owner_alert(db, await db.get(Lead, ids[0]), await db.get(Message, ids[1]))
            alert.status, alert.sent_at = "SENT", datetime.now(timezone.utc)
            await db.commit()
            labels_before = await db.scalar(select(func.count()).select_from(HumanLabel))
        duplicate_ids = await seed_sales_lead(sessions, body=duplicate_body)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get('/ops/inbox/')).status_code == 200
            assert (await client.get('/ops/inbox/data')).status_code == 401
            client.headers.update({"X-Operator-Id": "test_owner", "X-Label-Token": "test-only"})
            data = (await client.get('/ops/inbox/data')).json()
            assert any(item['id'] == ids[0] for item in data['items'])
            assert all(item['id'] != duplicate_ids[0] for item in data['items'])
            duplicates = (await client.get('/ops/inbox/data?view=duplicates')).json()
            copy = next(item for item in duplicates['items'] if item['id'] == duplicate_ids[0])
            assert copy['duplicate_of_lead_id'] == ids[0]
            assert data['pilot']['target_met'] is False
            rejected = await client.post(f'/ops/leads/{ids[0]}/feedback', json={'decision':'rejected'})
            assert rejected.status_code == 422
            for _ in range(2):
                accepted = await client.post(f'/ops/leads/{ids[0]}/feedback', json={'decision':'accepted'})
                assert accepted.status_code == 200, accepted.text
            data = (await client.get('/ops/inbox/data')).json()
            assert data['pilot']['accepted'] == 1 and data['pilot']['reviewed'] == 1
            assert data['pilot']['conversations'] == 0 and data['pilot']['wins'] == 0
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(HumanLabel)) == labels_before
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_sdk_plan_failure_defers_without_recording_false_decision(sessions, monkeypatch):
    from unittest.mock import Mock
    import services.commercial_ai.app.main as worker
    from shared.models import CommercialAIReview
    cfg = worker.get_settings()
    for name, value in {"commercial_ai_enabled": True, "commercial_ai_backend": "sdk",
                        "scoring_config": "config/scoring.yaml"}.items():
        monkeypatch.setattr(cfg, name, value)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(worker, "sdk_health", lambda: {"ai_status": "unverified"})
    record_health = Mock()
    monkeypatch.setattr(worker, "record_sdk_health", record_health)
    ids = await seed_sales_lead(sessions, status="NEW")
    backend = SimpleNamespace(validate=Mock(return_value=SimpleNamespace(status="error", error_detail="[plan_required]")))
    with pytest.raises(RetryLater):
        await worker.process_payload({"lead_id": ids[0], "message_id": ids[1]}, backend)
    record_health.assert_called_once_with("blocked_plan")
    async with sessions() as db:
        assert (await db.get(Lead, ids[0])).status == "NEW"
        assert await db.scalar(select(func.count()).select_from(CommercialAIReview).where(CommercialAIReview.lead_id == ids[0])) == 0
