"""Regression tests for commercial-gate / semantic promotion safety."""
from __future__ import annotations

from types import SimpleNamespace

from services.analyzer.app.main import Analyzer
from services.analyzer.app.scoring import LeadScorer, ScoreResult


def scorer() -> LeadScorer:
    return LeadScorer("config/scoring.yaml")


def test_commercial_action_rx_is_defined() -> None:
    assert LeadScorer._commercial_action_rx.search("I need help with my trading bot")


def test_commission_phrasing_is_commercial_via_buyer_intent() -> None:
    result = scorer().score(
        "I want to commission a developer to build a custom futures trading bot."
    )
    assert result.buyer_type == "CLIENT"
    assert result.tier == "HIGH"
    assert LeadScorer.is_commercial_lead_type(result.lead_type)


def test_semantic_boost_does_not_promote_non_commercial_client() -> None:
    worker = Analyzer.__new__(Analyzer)
    worker.scorer = scorer()
    worker.semantic = SimpleNamespace(score=lambda _text: 0.99)

    result = ScoreResult(
        score=34.0,
        tier="LOW",
        buyer_type="CLIENT",
        lead_type="TECHNICAL_QUESTION",
        intent_score=8.0,
        technical_score=10.0,
        commercial_score=8.0,
        promotion_score=0.0,
        matched_keywords=[],
        matched_categories=["trading_bot"],
        reasons=["weak buyer-like context"],
        contact_usernames=[],
        contact_urls=[],
    )

    # Reproduce the post-score semantic adjustment path without DB.
    settings_threshold = 0.48
    semantic_score = worker.semantic.score("any")
    if (
        semantic_score >= settings_threshold
        and result.tier == "LOW"
        and result.buyer_type == "CLIENT"
        and LeadScorer.is_commercial_lead_type(result.lead_type)
        and worker.scorer._has_target_financial_domain("my trading bot")
    ):
        result.tier = "MEDIUM"

    assert result.tier == "LOW"
    assert not LeadScorer.is_commercial_lead_type("TECHNICAL_QUESTION")
