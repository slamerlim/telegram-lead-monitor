from pathlib import Path

from services.analyzer.app.scoring import LeadScorer

SCORING = Path(__file__).parents[1] / "config" / "scoring.yaml"


def scorer() -> LeadScorer:
    return LeadScorer(str(SCORING))


def test_support_question_is_not_a_commercial_lead() -> None:
    result = scorer().score(
        "Python trading bot using pybit. ErrCode 170140. Why is Bybit rejecting the market order?"
    )
    assert result.buyer_type in {"SUPPORT_SEEKER", "UNKNOWN"}
    assert result.lead_type in {"PLATFORM_SUPPORT", "TECHNICAL_QUESTION"}
    assert result.tier == "LOW"


def test_account_api_support_is_not_a_commercial_lead() -> None:
    result = scorer().score(
        "New API keys return error 10003. Can someone from the API team check my account?"
    )
    assert result.buyer_type == "SUPPORT_SEEKER"
    assert result.tier == "LOW"


def test_dca_product_feedback_is_not_a_commercial_lead() -> None:
    result = scorer().score(
        "Why is there a limit of 50 pairs in the DCA bot? Can we get a better filter?"
    )
    assert result.tier == "LOW"
    assert result.lead_type in {"PLATFORM_SUPPORT", "TECHNICAL_QUESTION", "NOISE"}


def test_bot_repair_request_is_high_value() -> None:
    result = scorer().score(
        "I have a Python Bybit trading bot. I need a developer to fix an execution bug."
    )
    assert result.buyer_type == "CLIENT"
    assert result.lead_type == "BOT_REPAIR"
    assert result.tier == "HIGH"


def test_bot_customization_request_is_commercial() -> None:
    result = scorer().score(
        "Looking for someone to customize my DCA bot for a custom entry and exit strategy."
    )
    assert result.buyer_type == "CLIENT"
    assert result.lead_type == "BOT_CUSTOMIZATION"
    assert result.tier == "HIGH"


def test_strategy_implementation_is_commercial() -> None:
    result = scorer().score(
        "I have a trading strategy and want a developer to implement and automate it."
    )
    assert result.buyer_type == "CLIENT"
    assert result.lead_type == "STRATEGY_IMPLEMENTATION"
    assert result.tier == "HIGH"


def test_contract_quant_hire_is_commercial() -> None:
    result = scorer().score(
        "Looking to hire a quantitative developer on a contract basis for crypto trading systems."
    )
    assert result.buyer_type == "CLIENT"
    assert result.lead_type == "QUANT_ENGINEERING_CONTRACT"
    assert result.tier == "HIGH"


def test_ml_ai_contract_hire_is_commercial() -> None:
    result = scorer().score(
        "We need to hire an ML engineer on contract to build a trading signal model."
    )
    assert result.buyer_type == "CLIENT"
    assert result.lead_type == "ML_AI_ENGINEERING_CONTRACT"
    assert result.tier == "HIGH"


def test_service_provider_is_not_a_customer() -> None:
    result = scorer().score(
        "I can build Solana trading bots and MEV bots. Feel free to DM me for your project."
    )
    assert result.buyer_type == "SERVICE_PROVIDER"
    assert result.tier == "LOW"


def test_job_seeker_is_not_a_customer() -> None:
    result = scorer().score(
        "#ищу_работу Python developer. Looking for a remote position. CV available."
    )
    assert result.buyer_type == "JOB_SEEKER"
    assert result.tier == "LOW"


def test_bot_purchase_is_commercial() -> None:
    result = scorer().score(
        "Looking to buy a custom trading bot for Binance futures. Budget $3,000."
    )
    assert result.buyer_type == "CLIENT"
    assert result.lead_type == "BOT_PURCHASE"
    assert result.tier == "HIGH"
    assert result.budget_amount == 3000
    assert result.budget_currency == "USD"
