"""Recruiter vs project-buyer discovery tests."""

from __future__ import annotations

from pathlib import Path

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import DISCOVERY_VERSION, evaluate_discovery

CFG = Path("config/scoring.yaml")


def test_generic_recruiter_excluded():
    scorer = LeadScorer(str(CFG))
    text = (
        "#vacancy #hiring Looking for a senior Python developer for Company X. "
        "Full-time remote. Apply below."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert not feats.eligible


def test_project_recruitment_can_remain_candidate():
    scorer = LeadScorer(str(CFG))
    text = (
        "Client needs a contractor to build an automated trading system using Bybit API. "
        "Looking for a developer for this project, budget around $5k."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert feats.eligible
    assert feats.project_scope or feats.budget_signal
