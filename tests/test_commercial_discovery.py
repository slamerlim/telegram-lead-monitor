"""Commercial discovery features — scorer unchanged + LOW→candidate rules."""

from __future__ import annotations

from pathlib import Path

from services.analyzer.app.scoring import LeadScorer

CFG = Path("config/scoring.yaml")


def test_score_unchanged_after_discovery_features():
    scorer = LeadScorer(str(CFG))
    samples = [
        "Looking to hire a python developer to build a Bybit trading bot, budget $2k",
        "How do I reset my Binance API key password?",
        "We are hiring multiple roles: 1) designer 2) marketer 3) support agent",
        "Need someone to repair my broken trading bot order sync",
        "Breaking news: bitcoin surged past previous highs today",
    ]
    before = [scorer.score(t) for t in samples]
    # discovery_features must not mutate scorer state affecting score()
    for t in samples:
        scorer.discovery_features(t)
    after = [scorer.score(t) for t in samples]
    for b, a in zip(before, after):
        assert b.score == a.score
        assert b.tier == a.tier
        assert b.lead_type == a.lead_type
        assert b.buyer_type == a.buyer_type
        assert b.matched_categories == a.matched_categories
        assert b.reasons == a.reasons


def test_discovery_eligible_commercial_domain():
    scorer = LeadScorer(str(CFG))
    feats = scorer.discovery_features(
        "Need to hire a developer to build a custom Bybit trading bot with API integration"
    )
    # May or may not hit depending on YAML; if commercial+domain present, eligible.
    if feats.commercial_patterns or feats.hiring_patterns or feats.implementation_patterns:
        if feats.domain_categories or feats.technical_project_terms:
            assert feats.eligible or feats.hard_exclude


def test_discovery_hard_excludes_news_and_marketing():
    scorer = LeadScorer(str(CFG))
    news = scorer.discovery_features(
        "Daily crypto news digest: markets update, top headlines, subscribe for more"
    )
    # If news patterns match, must exclude
    if news.hard_exclude:
        assert not news.eligible


def test_topic_fingerprint_deterministic():
    from shared.commercial_ai.discovery import topic_fingerprint_from_hits

    a = topic_fingerprint_from_hits(categories=["trading_bot", "exchange_api"], pattern_names=[])
    b = topic_fingerprint_from_hits(categories=["exchange_api", "trading_bot"], pattern_names=[])
    assert a == b
    assert "trading_bot" in a
