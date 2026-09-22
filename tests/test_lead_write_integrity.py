"""Tests for concurrent opportunity_key IntegrityError handling."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from shared import lead_write as lw
from shared.lead_write import upsert_opportunity_lead
from shared.models import Lead


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


@pytest.mark.asyncio
async def test_assign_key_clears_on_integrity_error():
    lead = Lead(message_id=2, opportunity_key="old")
    db = MagicMock()

    async def scalar(stmt):
        return None  # appears free

    class NestedCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    flush_n = {"n": 0}

    async def flush():
        flush_n["n"] += 1
        raise IntegrityError("stmt", {}, Exception("uq"))

    db.scalar = scalar
    db.begin_nested = lambda: NestedCM()
    db.flush = flush

    await lw._assign_opportunity_key_if_free(db, lead, "new-key")
    assert lead.opportunity_key is None


@pytest.mark.asyncio
async def test_create_path_reattaches_after_key_race():
    message = SimpleNamespace(id=2, community_id=1, text="need bot fix")
    existing = SimpleNamespace(
        message_id=1,
        status="NEW",
        score=90.0,
        tier="HIGH",
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

    db = MagicMock()
    db.delete = AsyncMock()
    added = []

    def add(obj):
        added.append(obj)

    flush_n = {"n": 0}

    async def flush():
        flush_n["n"] += 1
        if flush_n["n"] == 1:
            raise IntegrityError("stmt", {}, Exception("uq_leads_opportunity_key"))

    calls = {"n": 0}

    async def scalar(stmt):
        calls["n"] += 1
        # 1: no lead for message, 2: no existing key, 3: after conflict existing appears
        if calls["n"] <= 2:
            return None
        return existing

    class NestedCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    db.scalar = scalar
    db.begin_nested = lambda: NestedCM()
    db.flush = flush
    db.add = add

    original = lw.make_opportunity_key
    lw.make_opportunity_key = lambda community_id, text: "same-key"
    try:
        action, lead = await upsert_opportunity_lead(db, message, _result(score=70.0))
    finally:
        lw.make_opportunity_key = original

    assert action == "deduped_skipped"
    assert lead is existing
    assert len(added) == 1
    db.delete.assert_awaited()
