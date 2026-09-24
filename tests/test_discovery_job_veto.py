"""Discovery job-board / aggregator / resume veto tests."""

from __future__ import annotations

from pathlib import Path

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import DISCOVERY_VERSION, evaluate_discovery

CFG = Path("config/scoring.yaml")


def test_multi_company_hiring_feed_vetoed():
    scorer = LeadScorer(str(CFG))
    text = (
        "Plaid Inc. is hiring a hybrid Senior Machine Learning Engineer. "
        "BitGo Prime is hiring an onsite Senior Security Application Engineer. "
        "Robinhood is hiring a hybrid Web Developer in Toronto."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert not feats.eligible
    assert feats.aggregator_signal or feats.hard_exclude or feats.employment_signal


def test_resume_hashtags_vetoed():
    scorer = LeadScorer(str(CFG))
    text = (
        "#Резюме #SeniorCopywriter #Web3 #Fintech "
        "Ищу работу на позиции Senior Content Writer. Open to opportunities."
    )
    feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION)
    assert not feats.eligible
