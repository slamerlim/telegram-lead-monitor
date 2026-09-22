"""Regression tests for opportunity lead upsert edge cases."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared import lead_write as lw
from shared.lead_write import upsert_opportunity_lead


def _result(**overrides):
    base = {
        "score": 80.0,
        "tier": "HIGH",
        "lead_type": "BOT_REPAIR",
        "buyer_type": "CLIENT",
        "intent_score": 1.0,
        "technical_score": 1.0,
        "commercial_score": 1.0,
        "promotion_score": 0.0,
        "matched_keywords": [],
        "matched_categories": [],
        "reasons": [],
        "contact_usernames": [],
        "contact_urls": [],
        "budget_amount": None,
        "budget_currency": None,
        "semantic_score": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _lead(**overrides):
    base = dict(
        message_id=1,
        status="NEW",
        score=50.0,
        tier="MEDIUM",
        opportunity_key="same-key",
        lead_type=None,
        buyer_type=None,
        intent_score=0,
        technical_score=0,
        commercial_score=0,
        promotion_score=0,
        matched_keywords="[]",
        matched_categories="[]",
        reasons="[]",
        contact_usernames="[]",
        contact_urls="[]",
        budget_amount=None,
        budget_currency=None,
        semantic_score=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _db_with_leads(by_message, by_key):
    db = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    async def scalar(stmt):
        sql = str(stmt)
        # Heuristic: message_id lookup vs opportunity_key lookup
        if "message_id" in sql and "opportunity_key" not in sql:
            # first unbound lead fetch uses message.id from outer scope via call order
            mid = getattr(scalar, "next_message", None)
            if mid is not None:
                return by_message.get(mid)
        if "opportunity_key" in sql:
            return by_key.get("same-key")
        return None

    db.scalar = scalar
    return db


@pytest.mark.asyncio
async def test_both_protected_leads_are_not_deleted():
    message = SimpleNamespace(id=2, community_id=1, text="need bot fix")
    lead_for_message = _lead(message_id=2, status="CONTACTED", score=50.0, opportunity_key="old-key")
    existing = _lead(message_id=1, status="QUALIFIED", score=90.0, opportunity_key="same-key")

    db = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    calls = {"n": 0}

    async def scalar(stmt):
        calls["n"] += 1
        if calls["n"] == 1:
            return lead_for_message
        return existing

    db.scalar = scalar
    original = lw.make_opportunity_key
    lw.make_opportunity_key = lambda community_id, text: "same-key"
    try:
        action, lead = await upsert_opportunity_lead(db, message, _result(score=70.0))
    finally:
        lw.make_opportunity_key = original

    assert action == "updated"
    assert lead is lead_for_message
    assert lead_for_message.opportunity_key is None
    assert lead_for_message.score == 70.0  # own row refreshed
    assert existing.score == 90.0  # other protected row untouched
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_protected_replaces_unprotected_and_takes_key():
    message = SimpleNamespace(id=2, community_id=1, text="need bot fix")
    protected = _lead(message_id=2, status="CONTACTED", score=50.0, opportunity_key="old-key")
    unprotected = _lead(message_id=1, status="NEW", score=90.0, opportunity_key="same-key")

    db = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    calls = {"n": 0}

    async def scalar(stmt):
        calls["n"] += 1
        if calls["n"] == 1:
            return protected
        if calls["n"] == 2:
            return unprotected
        # ownership check after delete — key free
        return None

    class NestedCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    db.scalar = scalar
    db.begin_nested = lambda: NestedCM()
    original = lw.make_opportunity_key
    lw.make_opportunity_key = lambda community_id, text: "same-key"
    try:
        action, lead = await upsert_opportunity_lead(db, message, _result(score=70.0))
    finally:
        lw.make_opportunity_key = original

    assert action == "deduped_updated"
    assert lead is protected
    assert protected.opportunity_key == "same-key"
    assert protected.score == 70.0
    db.delete.assert_awaited()


@pytest.mark.asyncio
async def test_low_after_detach_does_not_steal_key():
    message = SimpleNamespace(id=2, community_id=1, text="need bot fix")
    detached = _lead(message_id=2, status="CONTACTED", score=70.0, opportunity_key=None, tier="HIGH")
    owner = _lead(message_id=1, status="QUALIFIED", score=90.0, opportunity_key="same-key")

    db = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    calls = {"n": 0}

    async def scalar(stmt):
        calls["n"] += 1
        if calls["n"] == 1:
            return detached
        # ownership check returns owner message_id (select Lead.message_id)
        return owner.message_id

    db.scalar = scalar
    original = lw.make_opportunity_key
    lw.make_opportunity_key = lambda community_id, text: "same-key"
    try:
        action, lead = await upsert_opportunity_lead(db, message, _result(tier="LOW", score=10.0))
    finally:
        lw.make_opportunity_key = original

    assert action == "demoted"
    assert lead is detached
    assert detached.opportunity_key is None  # must not steal owner's key
    assert owner.opportunity_key == "same-key"


@pytest.mark.asyncio
async def test_protected_low_clears_stale_key_when_new_key_taken():
    message = SimpleNamespace(id=2, community_id=1, text="edited text")
    protected = _lead(
        message_id=2,
        status="CONTACTED",
        score=70.0,
        opportunity_key="stale-old-key",
        tier="HIGH",
    )
    other_owner_mid = 1

    db = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    calls = {"n": 0}

    async def scalar(stmt):
        calls["n"] += 1
        if calls["n"] == 1:
            return protected
        return other_owner_mid

    db.scalar = scalar
    original = lw.make_opportunity_key
    lw.make_opportunity_key = lambda community_id, text: "new-taken-key"
    try:
        action, lead = await upsert_opportunity_lead(db, message, _result(tier="LOW", score=10.0))
    finally:
        lw.make_opportunity_key = original

    assert action == "demoted"
    assert lead is protected
    assert protected.opportunity_key is None  # stale key cleared, not left pointing at old text
