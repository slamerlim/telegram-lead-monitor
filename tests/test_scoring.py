from services.analyzer.app.scoring import LeadScorer


def scorer() -> LeadScorer:
    return LeadScorer("config/scoring.yaml")


def test_existing_bybit_bot_error_without_hire_is_not_commercial() -> None:
    result = scorer().score(
        "Python trading bot using pybit. My live Bybit bot gets ErrCode 170140 on /v5/order/create. "
        "I tried multiple fixes. Why is the API rejecting the order?"
    )
    assert result.tier == "LOW"
    assert result.buyer_type in {"SUPPORT_SEEKER", "UNKNOWN"}
    assert result.lead_type in {"PLATFORM_SUPPORT", "TECHNICAL_QUESTION"}
    assert "exchange_api" in result.matched_categories
    assert "trading_bot" in result.matched_categories
    assert result.contact_usernames == []


def test_direct_client_build_request_is_high() -> None:
    result = scorer().score(
        "Looking for a developer to build a Bybit and OKX funding arbitrage bot. Budget $3k."
    )
    assert result.tier == "HIGH"
    assert result.buyer_type == "CLIENT"
    assert result.lead_type == "ARBITRAGE_PROJECT"
    assert result.budget_amount == 3000.0
    assert result.budget_currency == "USD"


def test_service_advertisement_is_not_a_client() -> None:
    result = scorer().score(
        "Hello guys. I am a developer. I can help you build trading bots, sniper bots and MEV bots. "
        "Feel free to DM me for your project."
    )
    assert result.buyer_type == "SERVICE_PROVIDER"
    assert result.lead_type == "SERVICE_ADVERTISEMENT"
    assert result.score < 50
    assert result.tier == "LOW"


def test_job_seeker_is_not_a_client() -> None:
    result = scorer().score(
        "#ищу_работу Python developer. Looking for work, remote or contract. CV available."
    )
    assert result.buyer_type == "JOB_SEEKER"
    assert result.lead_type == "JOB_SEEKER"
    assert result.score < 50


def test_job_vacancy_is_distinct_from_direct_client() -> None:
    result = scorer().score(
        "#vacancy We are hiring a Senior Backend Engineer for a crypto trading platform."
    )
    assert result.buyer_type == "RECRUITER"
    assert result.lead_type == "JOB_VACANCY"


def test_budget_requires_numeric_amount() -> None:
    result = scorer().score("We pay competitive rates for developers working on trading infrastructure.")
    assert result.budget_amount is None
    assert result.budget_currency is None


def test_contact_extraction() -> None:
    result = scorer().score(
        "Need a developer to fix my Bybit bot. Contact: @founder_name or DM @trading_team."
    )
    assert "founder_name" in result.contact_usernames
    assert "trading_team" in result.contact_usernames
    assert "https://t.me/founder_name" in result.contact_urls


def test_promotion_is_negative() -> None:
    result = scorer().score("Buy our trading bot and join VIP signals today")
    assert result.tier == "LOW"
    assert result.score < 50


def test_p2p_api_documentation_question_is_not_direct_client_request() -> None:
    result = scorer().score(
        "I am developing a SaaS analytics platform for P2P traders. "
        "I need clarification about the officially supported Bybit API and whether the endpoints are allowed."
    )
    assert result.buyer_type == "UNKNOWN"
    assert result.lead_type in {"TECHNICAL_QUESTION", "TECHNICAL_PROBLEM"}
    assert result.score < 50


def test_existing_bot_execution_failure_without_hire_is_not_commercial() -> None:
    result = scorer().score(
        "My Python trading bot using pybit is live on Bybit and /v5/order/create returns ErrCode 170140. "
        "I tried multiple fixes. Why is the API rejecting the market order?"
    )
    assert result.buyer_type in {"SUPPORT_SEEKER", "UNKNOWN"}
    assert result.lead_type in {"PLATFORM_SUPPORT", "TECHNICAL_QUESTION", "BOT_REPAIR", "EXECUTION_PROBLEM"}
    assert result.tier == "LOW"


def test_product_feature_discussion_is_not_a_custom_development_lead() -> None:
    result = scorer().score(
        "They have not been brought back yet. Today we have added the first DCA bot start conditions. "
        "Soon we will expand the indicators list."
    )
    assert result.buyer_type == "UNKNOWN"
    assert result.lead_type in {"TECHNICAL_QUESTION", "NOISE"}
    assert result.score < 50
