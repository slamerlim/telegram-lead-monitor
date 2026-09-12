from services.analyzer.app.scoring import LeadScorer


def test_high_intent_message():
    scorer = LeadScorer("config/scoring.yaml")
    result = scorer.score(
        "Looking for a Python developer to fix our Bybit trading bot. API issue and order execution problem."
    )
    assert result.tier == "HIGH"
    assert "customer_intent" in result.matched_categories
    assert "trading_bot" in result.matched_categories
    assert "exchange_api" in result.matched_categories


def test_promotional_message_is_not_high():
    scorer = LeadScorer("config/scoring.yaml")
    result = scorer.score("Buy our trading bot and join VIP signals today: https://example.com https://example.com")
    assert result.score < 50
