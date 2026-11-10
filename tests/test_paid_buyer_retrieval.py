import pytest

from services.analyzer.app.scoring import LeadScorer


@pytest.mark.parametrize("text", [
    "Can someone build a trading bot for me? Budget $3000, paid project.",
    "Who can build me a Binance trading bot? Paid work, budget $3000.",
    "Ищу разработчика для торгового бота Binance, бюджет 100000 рублей.",
    "Кто может написать торгового бота для Binance? Бюджет 100000 рублей.",
    "Freelance Opportunity: Quick Fix Needed for Python Trading Bot. Budget: $320",
    "NEW PROJECT ON LABORX: Quick Fix Needed for Python Trading Bot. Budget: $320",
])
def test_explicit_paid_requests_reach_commercial_review_without_threshold_change(text):
    scorer = LeadScorer("config/scoring.yaml")
    result = scorer.score(text)
    assert scorer.high == 75 and scorer.medium == 50
    assert result.tier in {"HIGH", "MEDIUM"}, result
    assert result.buyer_type == "CLIENT"
    assert scorer.is_commercial_lead_type(result.lead_type)
    assert scorer.discovery_features(text, version="disc_v7").eligible


@pytest.mark.parametrize("text", [
    "I need someone to teach us trading, please. Budget $3000.",
    "Can someone build a website for me? Budget $3000, paid project.",
    "I will build your trading bot. Budget $3000. Hire me.",
    "Looking for work as a trading bot developer. Budget $3000.",
    "NEW PROJECT ON LABORX: Looking for a full-time Python engineer. Salary $120k.",
    "How can I repair my trading bot? API error. My trading budget is $3000.",
    "GIG OF THE DAY: I will build a trading bot. Budget $3000.",
])
def test_non_buyers_stay_out_of_hot_queue(text):
    assert LeadScorer("config/scoring.yaml").score(text).tier == "LOW"


def test_education_is_not_buyer_discovery():
    result = LeadScorer("config/scoring.yaml").discovery_features("I need someone to teach us trading, please.", version="disc_v7")
    assert not result.eligible and "education_only" in result.veto_categories
