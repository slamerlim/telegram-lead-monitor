"""Tests for commercial_context_v3 two-stage retrieval (unit + mocked IO)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.commercial_ai.context import build_commercial_context
from shared.commercial_ai.discovery import DiscoveryFeatures
from shared.commercial_ai.retrieval_v3 import (
    CONTEXT_VERSION_V3,
    score_relevance,
    build_commercial_context_v3,
)
from services.analyzer.app.scoring import LeadScorer

CFG = "config/scoring.yaml"


@pytest.fixture
def scorer() -> LeadScorer:
    return LeadScorer(CFG)


def _feats(**kwargs) -> DiscoveryFeatures:
    f = DiscoveryFeatures()
    for k, v in kwargs.items():
        setattr(f, k, v)
    return f


def test_relevance_prefers_domain_and_commercial_over_pure_token():
    seed = "Need Bybit trading bot developer budget $5k"
    bad = "Looking for Unity developer for a mobile game"
    good = "Also need Binance API websocket execution for the bot"

    seed_f = _feats(
        commercial_patterns=["hire"],
        domain_categories=["trading_bot"],
        eligible=True,
        topic_fingerprint="trading_bot",
    )
    bad_f = _feats(
        commercial_patterns=["hire"],
        domain_categories=[],
        eligible=True,
        topic_fingerprint="none",
    )
    good_f = _feats(
        commercial_patterns=["hire"],
        domain_categories=["exchange_api", "trading_bot"],
        eligible=True,
        topic_fingerprint="trading_bot|exchange_api",
        technical_project_terms=["api", "websocket"],
    )
    now = datetime.now(timezone.utc)
    bad_bd = score_relevance(
        seed_text=seed,
        cand_text=bad,
        seed_fp="trading_bot",
        cand_fp="none",
        seed_feats=seed_f,
        cand_feats=bad_f,
        seed_dt=now,
        cand_dt=now - timedelta(days=1),
        is_reply_rel=False,
    )
    good_bd = score_relevance(
        seed_text=seed,
        cand_text=good,
        seed_fp="trading_bot",
        cand_fp="trading_bot|exchange_api",
        seed_feats=seed_f,
        cand_feats=good_f,
        seed_dt=now,
        cand_dt=now - timedelta(days=1),
        is_reply_rel=False,
    )
    assert good_bd.relevance_score > bad_bd.relevance_score
    assert bad_bd.incompatible_project
    assert good_bd.relevance_score >= 0.35


def test_same_author_multiproject_incompatible():
    now = datetime.now(timezone.utc)
    seed_f = _feats(
        commercial_patterns=["hire"],
        domain_categories=["trading_bot"],
        eligible=True,
        topic_fingerprint="trading_bot",
    )
    other_f = _feats(
        commercial_patterns=["hire"],
        domain_categories=[],
        eligible=True,
        topic_fingerprint="none",
    )
    bd = score_relevance(
        seed_text="Need Bybit trading bot.",
        cand_text="Looking for Unity developer.",
        seed_fp="trading_bot",
        cand_fp="none",
        seed_feats=seed_f,
        cand_feats=other_f,
        seed_dt=now,
        cand_dt=now - timedelta(days=2),
        is_reply_rel=False,
    )
    assert bd.incompatible_project
    assert bd.relevance_score < 0.28


def test_solidity_vs_bot_repair_separation():
    now = datetime.now(timezone.utc)
    seed_f = _feats(
        commercial_patterns=["repair"],
        domain_categories=["trading_bot"],
        eligible=True,
        topic_fingerprint="trading_bot",
    )
    other_f = _feats(
        commercial_patterns=["hire"],
        domain_categories=[],
        eligible=True,
        topic_fingerprint="none",
    )
    bd = score_relevance(
        seed_text="Need bot repair for Bybit grid.",
        cand_text="Looking for Solidity developer for NFT mint.",
        seed_fp="trading_bot",
        cand_fp="none",
        seed_feats=seed_f,
        cand_feats=other_f,
        seed_dt=now,
        cand_dt=now - timedelta(days=1),
        is_reply_rel=False,
    )
    assert bd.incompatible_project
    assert bd.relevance_score < 0.28


def test_employer_job_posts_rejected_from_affinity():
    from shared.commercial_ai.retrieval_v3 import has_episode_affinity, is_employer_job_post

    assert is_employer_job_post("Rain is hiring a hybrid Fullstack Engineer")
    assert not is_employer_job_post("Need Bybit trading bot developer budget $5k")
    now = datetime.now(timezone.utc)
    seed_f = _feats(eligible=True, domain_categories=["market_making"], topic_fingerprint="market_making")
    cand_f = _feats(eligible=True, domain_categories=["market_making"], topic_fingerprint="market_making")
    bd = score_relevance(
        seed_text="Rain is hiring a Fullstack Engineer",
        cand_text="Consensys is hiring a Backend Engineer",
        seed_fp="market_making",
        cand_fp="market_making",
        seed_feats=seed_f,
        cand_feats=cand_f,
        seed_dt=now,
        cand_dt=now - timedelta(days=1),
        is_reply_rel=False,
    )
    assert not has_episode_affinity(
        bd,
        cand_f,
        seed_text="Rain is hiring a Fullstack Engineer",
        cand_text="Consensys is hiring a Backend Engineer",
    )


def test_budget_followup_not_incompatible():
    """Commercial persistence: budget follow-up without domain words stays eligible."""
    now = datetime.now(timezone.utc)
    seed_f = _feats(
        commercial_patterns=["hire"],
        domain_categories=["trading_bot"],
        eligible=True,
        topic_fingerprint="trading_bot",
        budget_language=False,
    )
    bud_f = _feats(
        commercial_patterns=[],
        domain_categories=[],
        eligible=False,
        topic_fingerprint="none",
        budget_language=True,
    )
    bd = score_relevance(
        seed_text="Need Bybit trading bot developer.",
        cand_text="Budget is around $5k for the project.",
        seed_fp="trading_bot",
        cand_fp="none",
        seed_feats=seed_f,
        cand_feats=bud_f,
        seed_dt=now,
        cand_dt=now - timedelta(days=2),
        is_reply_rel=False,
    )
    assert not bd.incompatible_project


def _msg(**kw):
    defaults = dict(
        id=1,
        author_id=5,
        community_id=1,
        telegram_message_id=100,
        reply_to_telegram_message_id=None,
        text="Need Bybit trading bot developer",
        message_date=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_v3_dispatch_and_relevance_on_members(scorer: LeadScorer):
    now = datetime.now(timezone.utc)
    seed = _msg(id=10, text="Need Bybit trading bot developer, budget $3k", message_date=now)
    hist = _msg(
        id=11,
        telegram_message_id=101,
        text="Looking for Bybit exchange API websocket bot repair",
        message_date=now - timedelta(days=3),
    )
    sushi = _msg(
        id=12,
        telegram_message_id=102,
        text="Anyone know a good sushi place in Tokyo?",
        message_date=now - timedelta(days=2),
    )

    def make_session():
        session = AsyncMock()
        session.get = AsyncMock(return_value=seed)
        call_n = {"i": 0}

        def scalars_side_effect(*_a, **_k):
            call_n["i"] += 1
            mock = MagicMock()
            if call_n["i"] == 1:
                mock.all = MagicMock(return_value=[])  # children
            elif call_n["i"] == 2:
                mock.all = MagicMock(return_value=[hist, sushi])  # stage1 pool
            else:
                mock.all = MagicMock(return_value=[])
            return mock

        session.scalars = AsyncMock(side_effect=scalars_side_effect)
        return session

    ctx = await build_commercial_context(
        make_session(), 10, include_related=True, scorer=scorer, context_version="ctx_v3"
    )
    assert ctx.context_version == CONTEXT_VERSION_V3
    assert ctx.n_stage1_candidates == 2
    support = [m for m in ctx.members if m.relation != "SEED"]
    assert any(m.message_id == 11 for m in support)
    assert all(m.message_id != 12 for m in support)
    for m in support:
        assert "relevance_score" in (m.relevance or {})

    ctx2 = await build_commercial_context(
        make_session(), 10, include_related=True, scorer=scorer, context_version="ctx_v3"
    )
    assert ctx.context_hash == ctx2.context_hash
    assert len(ctx.members) == len(ctx2.members)


@pytest.mark.asyncio
async def test_v3_cap_stage3(scorer: LeadScorer):
    now = datetime.now(timezone.utc)
    seed = _msg(id=50, message_date=now)
    pool = [
        _msg(
            id=100 + i,
            telegram_message_id=200 + i,
            text=f"Bybit bot repair looking for developer #{i}",
            message_date=now - timedelta(hours=i + 1),
        )
        for i in range(25)
    ]
    session = AsyncMock()
    session.get = AsyncMock(return_value=seed)
    call_n = {"i": 0}

    def scalars_side_effect(*_a, **_k):
        call_n["i"] += 1
        mock = MagicMock()
        if call_n["i"] == 1:
            mock.all = MagicMock(return_value=[])
        elif call_n["i"] == 2:
            mock.all = MagicMock(return_value=pool)
        else:
            mock.all = MagicMock(return_value=[])
        return mock

    session.scalars = AsyncMock(side_effect=scalars_side_effect)
    ctx = await build_commercial_context_v3(
        session, 50, scorer=scorer, max_messages=5, stage1_max=100
    )
    assert len(ctx.members) <= 5
    assert ctx.n_stage1_candidates == 25
    assert ctx.exclusion_counts.get("CAP_EXCEEDED", 0) > 0


@pytest.mark.asyncio
async def test_v3_temporal_bands_in_relevance(scorer: LeadScorer):
    now = datetime.now(timezone.utc)
    seed = _msg(id=70, text="Need Binance futures trading bot engineer", message_date=now)
    m24 = _msg(
        id=71,
        telegram_message_id=301,
        text="Binance API bot repair needed today",
        message_date=now - timedelta(hours=6),
    )
    m7 = _msg(
        id=72,
        telegram_message_id=302,
        text="Still looking for Binance trading system developer",
        message_date=now - timedelta(days=4),
    )
    m30 = _msg(
        id=73,
        telegram_message_id=303,
        text="Budget for Binance bot project around 4k",
        message_date=now - timedelta(days=20),
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=seed)
    call_n = {"i": 0}

    def scalars_side_effect(*_a, **_k):
        call_n["i"] += 1
        mock = MagicMock()
        if call_n["i"] == 1:
            mock.all = MagicMock(return_value=[])
        elif call_n["i"] == 2:
            mock.all = MagicMock(return_value=[m24, m7, m30])
        else:
            mock.all = MagicMock(return_value=[])
        return mock

    session.scalars = AsyncMock(side_effect=scalars_side_effect)
    ctx = await build_commercial_context_v3(session, 70, scorer=scorer)
    bands = {
        m.message_id: (m.relevance or {}).get("temporal_band")
        for m in ctx.members
        if m.relation != "SEED"
    }
    assert bands.get(71) == "24h"
    assert bands.get(72) == "7d"
    assert bands.get(73) == "30d"
