"""Buyer-direction and discovery veto unit tests (scorer unchanged)."""

from __future__ import annotations

from pathlib import Path

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION,
    DISCOVERY_VERSION_V2,
    evaluate_discovery,
)

CFG = Path("config/scoring.yaml")


def _scorer() -> LeadScorer:
    return LeadScorer(str(CFG))


def test_score_unchanged_by_disc_v3():
    scorer = _scorer()
    samples = [
        "Looking to hire a python developer to build a Bybit trading bot, budget $2k",
        "Binance is hiring backend engineers in London, apply now",
        "I can build trading bots; DM me for development",
        "#резюме Looking for work as Python developer trading bots",
    ]
    before = [scorer.score(t) for t in samples]
    for t in samples:
        evaluate_discovery(scorer, t, version=DISCOVERY_VERSION)
    after = [scorer.score(t) for t in samples]
    for b, a in zip(before, after):
        assert b.score == a.score
        assert b.tier == a.tier
        assert b.matched_categories == a.matched_categories


def test_buyer_rfq_eligible():
    scorer = _scorer()
    text = (
        "Need someone to build a Bybit trading bot with WebSocket execution; "
        "budget $3k for the project"
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert feats.buyer_direction == "BUYER"
    assert feats.eligible
    assert feats.discovery_score > 0
    assert "corporate_employment" not in feats.veto_categories


def test_corporate_hiring_vetoed():
    scorer = _scorer()
    text = (
        "Rain is hiring a hybrid Fullstack Engineer in New York, NY. "
        "Rain is looking for a Fullstack Engineer. Apply now. Full-time remote role."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert feats.buyer_direction in ("EMPLOYER", "RECRUITER", "UNKNOWN")
    assert not feats.eligible
    assert feats.employment_signal or "corporate_employment" in feats.veto_categories or feats.hard_exclude


def test_job_seeker_vetoed():
    scorer = _scorer()
    text = (
        "#резюме #CV #opentowork Looking for work as Senior Python developer. "
        "Open to opportunities. Available for remote positions in trading/crypto."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert feats.seeker_signal or feats.buyer_direction == "SEEKER"
    assert not feats.eligible


def test_provider_vetoed():
    scorer = _scorer()
    text = (
        "I can build trading bots for Bybit and Binance. Our services include "
        "custom development. Looking for clients — DM me."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert feats.buyer_direction == "PROVIDER" or feats.provider_signal
    assert not feats.eligible


def test_repair_request_eligible():
    scorer = _scorer()
    text = "Need someone to repair my broken Bybit trading bot order sync ASAP"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert feats.eligible
    assert feats.repair_signal or feats.project_scope
    assert feats.buyer_direction in ("BUYER", "UNKNOWN")


def test_project_recruiter_may_pass():
    scorer = _scorer()
    text = (
        "Client needs a contractor to build an automated trading system using "
        "Bybit API WebSocket execution for their strategy."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    # Project scope + commercial → should remain discoverable
    assert feats.project_scope or feats.eligible
    if feats.eligible:
        assert feats.buyer_direction in ("BUYER", "RECRUITER")


def test_v2_still_fires_on_corporate_hiring():
    """Regression: disc_v2 was contaminated by employer feeds."""
    scorer = _scorer()
    text = (
        "FalconX is hiring a Senior Trading Systems Developer. "
        "FalconX is looking for a Senior Trading Systems Developer."
    )
    v2 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V2)
    v4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    # v3/v4 must be stricter on employer posts
    if v2.eligible:
        assert not v4.eligible


def test_ambiguity_buyer_passes():
    scorer = _scorer()
    text = "I need someone to help with a trading bot for Bybit"
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    # Ambiguous but buyer-side — should not be hard-vetoed as seeker/provider
    assert feats.buyer_direction != "SEEKER"
    assert feats.buyer_direction != "PROVIDER"
