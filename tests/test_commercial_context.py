"""Commercial context builder unit tests (in-memory style via mocks)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.commercial_ai.context import (
    RELATION_SAME_AUTHOR_24H,
    RELATION_SEED,
    build_commercial_context,
)
from shared.commercial_ai.discovery import fingerprints_compatible


def test_fingerprints_compatible_requires_overlap():
    assert fingerprints_compatible("trading_bot|repair", "trading_bot|api")
    assert not fingerprints_compatible("trading_bot", "arbitrage")
    assert not fingerprints_compatible("none", "trading_bot")


@pytest.mark.asyncio
async def test_context_caps_and_seed():
    now = datetime.now(timezone.utc)
    seed = SimpleNamespace(
        id=10,
        author_id=5,
        community_id=1,
        telegram_message_id=100,
        reply_to_telegram_message_id=None,
        text="seed text about trading bot repair",
        message_date=now,
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=seed)

    # No parent, no children, no same-author rows
    empty = MagicMock()
    empty.all = MagicMock(return_value=[])
    session.scalars = AsyncMock(return_value=empty)

    ctx = await build_commercial_context(
        session, 10, include_related=False, scorer=None, max_messages=20
    )
    assert ctx.seed_message_id == 10
    assert len(ctx.members) == 1
    assert ctx.members[0].relation == RELATION_SEED
    assert ctx.context_hash
    # Deterministic hash for same seed-only context
    ctx2 = await build_commercial_context(
        session, 10, include_related=False, scorer=None, max_messages=20
    )
    assert ctx.context_hash == ctx2.context_hash


@pytest.mark.asyncio
async def test_context_dedup_by_message_id():
    now = datetime.now(timezone.utc)
    seed = SimpleNamespace(
        id=10,
        author_id=5,
        community_id=1,
        telegram_message_id=100,
        reply_to_telegram_message_id=None,
        text="seed",
        message_date=now,
    )
    other = SimpleNamespace(
        id=11,
        author_id=5,
        community_id=1,
        telegram_message_id=101,
        reply_to_telegram_message_id=None,
        text="related trading bot",
        message_date=now - timedelta(hours=1),
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=seed)

    call_n = {"n": 0}

    def _scalars_side_effect(*_a, **_k):
        call_n["n"] += 1
        m = MagicMock()
        # First few calls return related author msgs; later blind empty
        if call_n["n"] <= 3:
            m.all = MagicMock(return_value=[other, other])  # duplicate same id
        else:
            m.all = MagicMock(return_value=[])
        return m

    session.scalars = AsyncMock(side_effect=_scalars_side_effect)
    ctx = await build_commercial_context(
        session, 10, include_related=True, scorer=None, max_messages=20
    )
    ids = [m.message_id for m in ctx.members]
    assert ids.count(10) == 1
    assert ids.count(11) <= 1
