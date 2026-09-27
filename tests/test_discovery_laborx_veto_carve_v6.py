"""disc_v6 LaborX FO gig-marketplace project carve (Phase D).

Keeps marketplace/job_aggregator veto for pure noise; carves FO+repair/bot+budget.
Default production version is disc_v6 after acceptance (evidence 99).
"""

from __future__ import annotations

from pathlib import Path

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION,
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V5,
    DISCOVERY_VERSION_V6,
    evaluate_discovery,
)

CFG = Path("config/scoring.yaml")

FO_REPAIR = (
    "🌟 Freelance Opportunity: Quick Fix Needed for Python Trading Bot "
    "💰 Budget: $320 🤝 With: david lil"
)
FO_REPAIR_50 = (
    "🌟 Freelance Opportunity: Quick Fix Needed for Python Trading Bot "
    "💰 Budget: $50 🤝 With: Ryan Peterson"
)
FO_BOT = (
    "🌟 Freelance Opportunity: Crypto Trading Bot Developer "
    "💰 Budget: $200 🤝 With: Yohans Hayla"
)
FO_BOT_GUI = (
    "🌟 Freelance Opportunity: Python Crypto Trading Bot Developer (GUI + Dashboard) "
    "💰 Budget: $800 🤝 With: Harry Bennett"
)


def _scorer() -> LeadScorer:
    return LeadScorer(str(CFG))


def test_default_version_is_v6_after_acceptance():
    """Production default is disc_v6 after FO carve acceptance (evidence 99)."""
    assert DISCOVERY_VERSION == DISCOVERY_VERSION_V6 == "disc_v6"
    assert DISCOVERY_VERSION_V4 == "disc_v4"


def test_v6_recovers_fo_repair_with_budget():
    scorer = _scorer()
    for text in (FO_REPAIR, FO_REPAIR_50):
        v4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
        v6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        assert not v4.eligible
        assert "job_aggregator" in v4.veto_categories
        assert v6.eligible
        assert v6.gig_project_carve
        assert "job_aggregator" not in v6.veto_categories
        assert "CARVE" in v6.matched_paths


def test_v6_recovers_fo_trading_bot_project_with_budget():
    scorer = _scorer()
    for text in (FO_BOT, FO_BOT_GUI):
        v6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        assert v6.eligible
        assert v6.gig_project_carve
        assert "job_aggregator" not in v6.veto_categories


def test_v6_still_vetoes_plain_fo_employment_vacancy():
    scorer = _scorer()
    marketing = (
        "🌟 Freelance Opportunity: Marketing Manager for Web3 Startup "
        "💰 Budget: $1500 🤝 With: Jane Doe"
    )
    vacancy = (
        "New project on LaborX: Looking for full-time Python engineer. "
        "Apply now. Salary $120k"
    )
    for text in (marketing, vacancy):
        v6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        assert not v6.eligible
        assert not v6.gig_project_carve
        assert "job_aggregator" in v6.veto_categories or "corporate_employment" in v6.veto_categories


def test_v6_still_vetoes_gig_of_the_day_and_bare_laborx_noise():
    scorer = _scorer()
    gig = (
        "GIG OF THE DAY. I will develop MT5 Expert Advisor, Python trading bot, "
        "or automate your strategy. $50. Hire: https://laborx.com/gigs/example"
    )
    sponsorship = "Want your message here — sponsoring LaborX job board posts"
    for text in (gig, sponsorship):
        v6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        assert not v6.eligible
        assert not v6.gig_project_carve


def test_v6_laborx_source_not_banned_by_community_id():
    """Carve is message-level; LaborX username must not ban non-FO buyer RFQs."""
    scorer = _scorer()
    text = (
        "Looking for someone who can build a Bybit trading bot with WebSocket "
        "execution. Project budget $4k."
    )
    # Discovery eval is text-only; community does not enter evaluate_discovery.
    v6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
    assert v6.eligible
    assert not v6.gig_project_carve
    # Scorer still scores LaborX genuine FO repair HIGH (CRM path separate).
    scored = scorer.score(FO_REPAIR, community_username="LaborXWeb3Jobs")
    assert scored.tier == "HIGH"


def test_v5_still_vetoes_fo_untouched():
    """disc_v5 remains HOLD semantics; carve is v6-only."""
    scorer = _scorer()
    v5 = evaluate_discovery(scorer, FO_REPAIR, version=DISCOVERY_VERSION_V5)
    assert not v5.eligible
    assert "job_aggregator" in v5.veto_categories or v5.aggregator_signal


def test_v6_does_not_alter_score():
    scorer = _scorer()
    before = scorer.score(FO_REPAIR)
    evaluate_discovery(scorer, FO_REPAIR, version=DISCOVERY_VERSION_V6)
    after = scorer.score(FO_REPAIR)
    assert before.score == after.score
    assert before.tier == after.tier


def test_v6_vetoes_new_project_on_laborx_without_fo_template():
    """Evidence 119c mid=60620: NEW PROJECT ON LABORX must not FO-carve."""
    scorer = _scorer()
    text = (
        "🆕 NEW PROJECT ON LABORX\n\n"
        "🟨 Trading Platform–Security Fix,Feature Completion&Deployment\n\n"
        "💰 Budget: $800\n"
        "I have an existing Next.js 15 trading platform. "
        "I need an experienced developer to Fix the critical issues.\n"
        "🔗 Apply: https://laborx.com/jobs/trading-platform-security-fix-104288\n"
        "#LaborX"
    )
    v6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
    assert not v6.gig_project_carve
    assert not v6.eligible
    assert v6.aggregator_signal or "job_aggregator" in v6.veto_categories


def test_v6_vetoes_mexc_fomo_my_strategy_advice():
    """Evidence 119c mid=4780480: bare 'my strategy' FOMO is not path-E ownership."""
    scorer = _scorer()
    text = (
        "BTC just hit $81,000! 🚀 After such a strong move, I wouldn’t rush into "
        "buying or selling based on FOMO. My strategy would be to hold the majority "
        "of my BTC as a long-term position while taking a small portion of profits "
        "to lock in gains. #MEXC #BTC"
    )
    v6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
    assert not v6.ownership_signal
    assert not v6.eligible


def test_v6_bare_my_our_project_is_not_ownership():
    """Regression: bare 'project' must not widen ownership into frozen path_b/c."""
    scorer = _scorer()
    for text in (
        "Our project deadline is next Friday for the grid bot rollout on binance",
        "my project uses the exchange api, spent $500 on infra this month",
        "We have an existing project that needs a quant engineer",
    ):
        v6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        assert not v6.ownership_signal, text
