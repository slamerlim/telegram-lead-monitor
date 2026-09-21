from services.analyzer.app.scoring import LeadScorer


def scorer() -> LeadScorer:
    return LeadScorer("config/scoring.yaml")


def assert_low_noncommercial(r):
    assert r.tier == "LOW"
    assert r.lead_type not in {
        "BOT_PURCHASE", "BOT_REPAIR", "BOT_CUSTOMIZATION", "STRATEGY_IMPLEMENTATION",
        "TRADING_SYSTEM_CONTRACT", "QUANT_ENGINEERING_CONTRACT", "ML_AI_ENGINEERING_CONTRACT",
        "ARBITRAGE_PROJECT", "COPY_TRADING_PROJECT", "MARKET_MAKING_PROJECT", "SOLANA_DEX_BOT_PROJECT",
    }


def test_bybit_support_order_error_is_not_commercial():
    r = scorer().score("Python trading bot using pybit. Order returns ErrCode 170140. Why is the API rejecting it? Please clarify.", community_username="BybitAPI")
    assert_low_noncommercial(r)
    assert r.lead_type in {"TECHNICAL_QUESTION", "PLATFORM_SUPPORT"}


def test_bybit_trailing_stop_question_is_not_commercial():
    r = scorer().score("Question about trailing stop on USDT perpetuals (V5 API). What is the minimum accepted value and which error code is returned?", community_username="BybitAPI")
    assert_low_noncommercial(r)


def test_bybit_regulatory_api_question_is_not_commercial():
    r = scorer().score("I had an API, it says expired. I deleted it and tried to create a new API but I am getting error 10024. What can I do?", community_username="BybitAPI")
    assert_low_noncommercial(r)


def test_bybit_archive_support_request_is_not_commercial():
    r = scorer().score("I need written clarification on historical market data. What official ticket channel or verified email address should I use?", community_username="BybitAPI")
    assert_low_noncommercial(r)


def test_3commas_product_feedback_is_not_commercial():
    r = scorer().score("Is there a guide for multi-pair DCA bots? Why is the pair limit 50? Can you add a USD pair filter?", community_username="Community_3Commas")
    assert_low_noncommercial(r)
    assert r.lead_type == "TECHNICAL_QUESTION"


def test_3commas_praise_is_not_commercial():
    r = scorer().score("Amazing job on v2, love that grid bot starts whenever I want, keep going well.", community_username="Community_3Commas")
    assert_low_noncommercial(r)


def test_3commas_dca_comment_is_not_commercial():
    r = scorer().score("We need the dca bot at least to work with.", community_username="Community_3Commas")
    assert_low_noncommercial(r)


def test_3commas_feature_discussion_is_not_commercial():
    r = scorer().score("That's absolutely right. If you have used DCA Bot with TV alerts, now you will need to set it up in the Signal Bot.", community_username="Community_3Commas")
    assert_low_noncommercial(r)


def test_thorchain_protocol_proposal_is_not_commercial():
    r = scorer().score("Proposal: make RUNE bonding permissionless without weakening THORChain's Proof-of-Bond security. Keep existing churn, unbonding, TSS and governance.", community_username="thorchain_org")
    assert_low_noncommercial(r)


def test_thorchain_adr_is_not_commercial():
    r = scorer().score("ADR Draft: Permissionless RUNE Bonding with Operator Skin-in-the-Game. The goal is permissionless Bond Provider access while preserving security.", community_username="thorchain_org")
    assert_low_noncommercial(r)


def test_vendor_response_is_not_client():
    r = scorer().score("According to our documentation, please check your API key permissions. Our support team confirmed this requirement.", community_username="BybitAPI")
    assert r.tier == "LOW"
    assert r.buyer_type == "SERVICE_PROVIDER"


def test_job_aggregator_is_not_target_lead():
    r = scorer().score("Coinbase is hiring Senior Product Designer\nTrust Wallet is hiring Design Engineer\nStripe is hiring AI Engineer\nEllipsis Labs is hiring Senior Smart Contract Engineer\nAllium is hiring Product Marketing Manager", community_username="web3hiring")
    assert r.tier == "LOW"
    assert r.lead_type == "JOB_VACANCY"


def test_job_seeker_resume_is_not_client():
    r = scorer().score("#Resume #Developer #Frontend #Vue #TypeScript #Web3 #Remote. Looking for a team. Contact @trycatchfinallythrow. CV available.", community_username="workingincrypto")
    assert r.tier == "LOW"
    assert r.buyer_type == "JOB_SEEKER"


def test_service_provider_gig_is_not_client():
    r = scorer().score("GIG OF THE DAY. I will develop MT5 Expert Advisor, Python trading bot, or automate your strategy. $50. Hire: https://laborx.com/gigs/example", community_username="laborx")
    assert r.tier == "LOW"
    assert r.buyer_type == "SERVICE_PROVIDER"
    assert r.lead_type == "SERVICE_ADVERTISEMENT"


def test_direct_bot_repair_is_commercial():
    r = scorer().score("I have a Python Bybit trading bot. It stopped placing orders after a websocket reconnect. I need a developer to diagnose and fix it.", community_username="BybitAPI")
    assert r.tier == "HIGH"
    assert r.buyer_type == "CLIENT"
    assert r.lead_type == "BOT_REPAIR"


def test_buy_bot_is_commercial():
    r = scorer().score("Looking to buy a custom Binance futures trading bot. Budget $3000.", community_username="cryptojobslist")
    assert r.tier == "HIGH"
    assert r.buyer_type == "CLIENT"
    assert r.lead_type == "BOT_PURCHASE"


def test_strategy_implementation_is_commercial():
    r = scorer().score("I have a TradingView strategy and need a developer to implement it as an automated futures trading bot.", community_username="algofoxchat")
    assert r.tier == "HIGH"
    assert r.lead_type == "STRATEGY_IMPLEMENTATION"


def test_contract_quant_is_commercial_and_domain_relevant():
    r = scorer().score("We are looking for a quantitative engineer on a 6-month contract to build an algorithmic crypto trading system.", community_username="web3hiring")
    assert r.tier == "HIGH"
    assert r.buyer_type == "RECRUITER"
    assert r.lead_type == "QUANT_ENGINEERING_CONTRACT"


def test_generic_ai_contract_outside_target_domain_is_not_target_lead():
    r = scorer().score("Freelance opportunity: AI/ML Engineer for a mobile application. Budget: $2000.", community_username="LaborXWeb3Jobs")
    assert r.tier == "LOW"
    assert r.lead_type == "JOB_VACANCY"


def test_ml_contract_in_trading_domain_is_commercial():
    r = scorer().score("Hiring an ML engineer on contract to build a signal filtering model for our crypto trading system.", community_username="cryptoDevJobs")
    assert r.tier == "HIGH"
    assert r.buyer_type == "RECRUITER"
    assert r.lead_type == "ML_AI_ENGINEERING_CONTRACT"


def test_full_time_generic_engineering_job_is_not_target_lead():
    r = scorer().score("We are hiring a Senior Software Engineer for our crypto company.", community_username="web3hiring")
    assert r.tier == "LOW"
    assert r.lead_type == "JOB_VACANCY"
