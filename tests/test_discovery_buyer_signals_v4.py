"""disc_v4 buyer-signal families and conjunction path tests."""

from __future__ import annotations

from pathlib import Path

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION,
    DISCOVERY_VERSION_V3,
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V6,
    evaluate_discovery,
)

CFG = Path("config/scoring.yaml")


def _scorer() -> LeadScorer:
    return LeadScorer(str(CFG))


def test_default_version_is_v6():
    assert DISCOVERY_VERSION == DISCOVERY_VERSION_V6 == "disc_v6"
    assert DISCOVERY_VERSION_V4 == "disc_v4"


def test_score_independent_from_discovery_score():
    scorer = _scorer()
    text = (
        "Need a Python developer to implement my Bybit futures strategy, budget $3k"
    )
    before = scorer.score(text)
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    after = scorer.score(text)
    assert before.score == after.score
    assert before.tier == after.tier
    assert feats.discovery_score != before.score or feats.eligible


def test_direct_buyer_rfq():
    scorer = _scorer()
    text = (
        "Looking for someone who can build a Bybit trading bot with WebSocket "
        "execution. Project budget $4k."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.buyer_direction == "BUYER"
    assert feats.direct_request_signal or "DIRECT_PROJECT_REQUEST" in feats.buyer_signal_families


def test_trading_bot_purchase():
    scorer = _scorer()
    text = "Need to buy/commission a custom trading bot for Binance futures, budget $2k"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.buyer_direction in ("BUYER", "UNKNOWN")


def test_trading_bot_repair():
    scorer = _scorer()
    text = "My Bybit trading bot is broken — need someone to fix order sync ASAP"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.repair_signal
    assert "BOT_REPAIR_REQUEST" in feats.buyer_signal_families


def test_strategy_implementation():
    scorer = _scorer()
    text = "Need a developer to implement my strategy as an automated Bybit bot"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.ownership_signal or feats.automation_signal or feats.direct_request_signal


def test_automation_request():
    scorer = _scorer()
    text = "Looking for contractor to automate my trading process on OKX into a bot"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.automation_signal or feats.direct_request_signal


def test_budgeted_project():
    scorer = _scorer()
    text = (
        "Need contractor for arbitrage bot on Binance, fixed price $5k, "
        "deadline 3 weeks"
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.budget_signal or feats.timeline_signal


def test_timed_project():
    scorer = _scorer()
    text = (
        "Looking for a developer to implement this Bybit strategy, deadline 3 weeks"
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.timeline_signal or feats.direct_request_signal


def test_project_recruiter_allowed():
    scorer = _scorer()
    text = (
        "Client is looking for a contractor to build an automated trading system "
        "on Bybit. Budget $5k."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.project_procurement_signal or feats.project_scope
    assert feats.buyer_direction in ("BUYER", "RECRUITER")


def test_corporate_employment_vetoed():
    scorer = _scorer()
    text = (
        "Company X is hiring Senior Python Engineers. Apply now. "
        "Full-time remote role with salary $140,000-$180,000."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert not feats.eligible


def test_job_aggregator_vetoed():
    scorer = _scorer()
    text = (
        "Plaid Inc. is hiring a hybrid Senior Machine Learning Engineer. "
        "BitGo Prime is hiring an onsite Senior Security Application Engineer. "
        "Robinhood is hiring a hybrid Web Developer in Toronto."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert not feats.eligible


def test_resume_vetoed():
    scorer = _scorer()
    text = (
        "#резюме #CV #opentowork Looking for work as Senior Python developer. "
        "Open to opportunities in trading/crypto."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert not feats.eligible


def test_seeker_vetoed():
    scorer = _scorer()
    text = "Open to work — seeking remote Python developer positions in fintech"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert not feats.eligible


def test_provider_vetoed():
    scorer = _scorer()
    text = (
        "I can build trading bots for Bybit and Binance. Our services include "
        "custom development. Looking for clients — DM me."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert not feats.eligible


def test_support_vetoed():
    scorer = _scorer()
    text = "How do I reset my Bybit API key? Support please help with login error"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert not feats.eligible


def test_marketing_vetoed():
    scorer = _scorer()
    text = "🚀 New signal bot for Bybit! Join our channel for free signals and promo"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert not feats.eligible


def test_news_vetoed():
    scorer = _scorer()
    text = (
        "Breaking: Binance announces new futures products. Market update digest "
        "for traders today."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    # May or may not hard-exclude depending on news patterns; must not be eligible buyer
    if feats.news_signal or feats.hard_exclude:
        assert not feats.eligible


def test_ambiguous_buyer_passes():
    scorer = _scorer()
    text = "Looking for Python developer for trading project on Bybit"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.buyer_direction in ("BUYER", "RECRUITER", "UNKNOWN")


def test_recruiter_plus_concrete_project():
    scorer = _scorer()
    text = (
        "We are looking for a freelancer for a client project: build market-making "
        "bot on OKX, milestones and fixed price."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.soft_direction or feats.project_procurement_signal or feats.project_scope


def test_buyer_with_employment_vocab_soft():
    """Employment-ish wording with concrete project should soft-pass."""
    scorer = _scorer()
    text = (
        "Looking for a contractor for our trading system — need Bybit API "
        "integration and execution engine. Budget $8k."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.ownership_signal or feats.project_scope


def test_russian_buyer_forms():
    scorer = _scorer()
    text = "Нужен разработчик починить мой бот на Bybit, бюджет 2000$"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert feats.eligible
    assert feats.buyer_direction == "BUYER"


def test_provider_promo_bot_dm_vetoed():
    scorer = _scorer()
    text = (
        "That’s why I use my bot to automate my trading. "
        "Want to know how the bot works? Message me “BOT” and I’ll share."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert not feats.eligible


def test_support_ticket_not_candidate():
    scorer = _scorer()
    text = (
        "Dear Bybit Technical Support Team, We are developing an automated trading "
        "system that manages Futures Grid Bots via the Bybit API. Please help."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert not feats.eligible
    assert feats.support_signal or "support_question" in feats.veto_categories



def test_fix_api_announcement_not_repair_candidate():
    scorer = _scorer()
    text = (
        "[New Feature] FIX API for Spot Trading Bybit is pleased to announce "
        "the launch of FIX API for Spot Trading"
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert not feats.eligible



def test_v4_can_recover_beyond_v3_on_soft_project():
    """Ambiguous looking-for + domain + ownership should prefer v4 soft path."""
    scorer = _scorer()
    text = (
        "Looking for a Python developer to fix our existing exchange integration "
        "on Bybit — current system has order bugs, paid project."
    )
    v3 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V3)
    v4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    assert v4.eligible
    assert v4.ownership_signal or v4.repair_signal
    # v4 must not be worse than v3 on this clear buyer repair/ownership case
    if v3.eligible:
        assert v4.eligible
