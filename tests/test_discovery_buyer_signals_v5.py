"""disc_v5 buyer/project path expansions + LaborX FO veto regression (D untouched)."""

from __future__ import annotations

from pathlib import Path

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION,
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V5,
    evaluate_discovery,
)

CFG = Path("config/scoring.yaml")


def _scorer() -> LeadScorer:
    return LeadScorer(str(CFG))


def test_default_version_remains_v4_until_acceptance():
    """Production default stays disc_v4; v5 is opt-in via evaluate_discovery(version=)."""
    assert DISCOVERY_VERSION == DISCOVERY_VERSION_V4 == "disc_v4"
    assert DISCOVERY_VERSION_V5 == "disc_v5"


def test_v5_recovers_need_trading_bot_budget():
    scorer = _scorer()
    text = "Need a trading bot built for Binance futures, can pay $2k"
    v4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert not v4.eligible
    assert v5.eligible
    assert "F" in v5.matched_paths or v5.direct_request_signal
    assert v5.buyer_direction in ("BUYER", "UNKNOWN", "RECRUITER")


def test_v5_recovers_anyone_available_grid_bot():
    scorer = _scorer()
    text = "Anyone available to code a Bybit grid bot? Budget $1500"
    v4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert not v4.eligible
    assert v5.eligible
    assert v5.bot_deliverable_signal or "F" in v5.matched_paths


def test_v5_recovers_hire_developer_to_build():
    scorer = _scorer()
    text = "Hire a developer to build arbitrage bot between Binance and Bybit"
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert v5.eligible
    assert v5.direct_request_signal or v5.project_scope


def test_v5_recovers_looking_to_pay_automate_ownership():
    scorer = _scorer()
    text = "Looking to pay someone to automate my Binance strategy"
    v4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert not v4.eligible
    assert v5.eligible
    assert v5.ownership_signal or v5.direct_request_signal


def test_v5_recovers_build_me_bot_can_pay():
    scorer = _scorer()
    text = "build me a trading bot for bybit please, I can pay"
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert v5.eligible
    assert "F" in v5.matched_paths or "A" in v5.matched_paths


def test_v5_recovers_russian_nuzhen_chelovek_bota():
    """Tight v5 drops bare 'нужен человек' (job-board noise); keep explicit buyer RU forms."""
    scorer = _scorer()
    # Explicit developer form still works via v4 RU regex.
    text = "Нужен разработчик сделать бота для Binance, бюджет 2000$"
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert v5.eligible


def test_v5_rejects_russian_vacancy_hashtag():
    scorer = _scorer()
    text = (
        "#Вакансия #HeadOfMarketing #trading #Fulltime #Remote "
        "Требуется Head Of Marketing топ-уровня в онлайн-школу"
    )
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert not v5.eligible


def test_v5_rejects_provider_offer_bot():
    scorer = _scorer()
    text = (
        "hey everyone, if there are any traders in solana network who need fast "
        "signals for trading, i can offer my telegram bot"
    )
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert not v5.eligible
    assert v5.provider_signal or "service_provider" in v5.veto_categories


def test_v5_path_counters_populated():
    scorer = _scorer()
    text = "Want custom trading bot for Bybit, paid project"
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert v5.eligible
    assert isinstance(v5.matched_paths, list)
    assert len(v5.matched_paths) >= 1


def test_v5_still_vetoes_corporate_employment():
    scorer = _scorer()
    text = (
        "Company X is hiring Senior Python Engineers. Apply now. "
        "Full-time remote role with salary $140,000-$180,000."
    )
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert not v5.eligible


def test_v5_still_vetoes_laborx_fo_untouched_d():
    """Prove D (LaborX FO / gig marketplace veto) is untouched."""
    scorer = _scorer()
    text = (
        "🌟 Freelance Opportunity: Quick Fix Needed for Python Trading Bot "
        "💰 Budget: $320 🤝 With: david lil"
    )
    v4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert not v4.eligible
    assert not v5.eligible
    assert "job_aggregator" in v5.veto_categories or v5.aggregator_signal


def test_v5_still_vetoes_provider_and_seeker():
    scorer = _scorer()
    provider = (
        "I can build trading bots for Bybit and Binance. Our services include "
        "custom development. Looking for clients — DM me."
    )
    seeker = "#резюме #opentowork Looking for work as Senior Python developer in crypto"
    assert not evaluate_discovery(scorer, provider, version=DISCOVERY_VERSION_V5).eligible
    assert not evaluate_discovery(scorer, seeker, version=DISCOVERY_VERSION_V5).eligible


def test_v5_does_not_alter_score():
    scorer = _scorer()
    text = "Need a trading bot built for Binance futures, can pay $2k"
    before = scorer.score(text)
    evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    after = scorer.score(text)
    assert before.score == after.score
    assert before.tier == after.tier


def test_v4_regression_existing_buyer_still_eligible():
    scorer = _scorer()
    text = (
        "Looking for someone who can build a Bybit trading bot with WebSocket "
        "execution. Project budget $4k."
    )
    v4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
    v5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
    assert v4.eligible
    assert v5.eligible
