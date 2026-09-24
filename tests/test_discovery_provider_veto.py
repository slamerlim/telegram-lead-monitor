"""Provider and recruiter discovery logic tests."""

from __future__ import annotations

from pathlib import Path

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import DISCOVERY_VERSION, evaluate_discovery

CFG = Path("config/scoring.yaml")


def test_provider_portfolio_ad_vetoed():
    scorer = LeadScorer(str(CFG))
    text = (
        "I recently helped my client develop an all-in-one Solana trading and bundling "
        "system. Do you need developer support? Hire me — DM me for your project."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert not feats.eligible
    assert feats.provider_signal or feats.buyer_direction == "PROVIDER"


def test_strategy_implementation_buyer():
    scorer = LeadScorer(str(CFG))
    text = (
        "I have a trading strategy and need a developer to implement and automate it "
        "on Binance futures. Looking for a contractor, budget $4k."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert feats.eligible
    assert feats.buyer_direction == "BUYER"
