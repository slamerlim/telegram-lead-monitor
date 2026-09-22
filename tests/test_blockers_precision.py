"""BLOCKER 1 + 3 acceptance: commercial vetoes and community_profiles."""

from pathlib import Path

from services.analyzer.app.scoring import LeadScorer

SCORING = Path(__file__).parents[1] / "config" / "scoring.yaml"


def scorer() -> LeadScorer:
    return LeadScorer(str(SCORING))


BITUNIX_PROMO = """🚀 Futures Grid PnL Challenge is LIVE!

🔥 Try your hand at Futures Grid and get rewarded for participation and performance!

💡 How to Join
1️⃣ Create any Futures Grid bot during the challenge period
2️⃣ Trade and climb the PnL leaderboard
Top 3 PnL → 20 USDT Futures Bonus
"""

LABORX_DIGEST = """🔥 Hot Gigs This Week on LaborX

1. Rust Developer (Solana)
   💰 $10,000-20,000/mo | 🏢 DeFi Protocol | 📍 Remote

2. Smart Contract Engineer
   💰 $9,000-16,000/mo | 🏢 DAO Platform | 📍 Remote

3. Web3 Product Manager
   💰 $8,000-12,000/mo
"""

BLOFIN_PROMO = """Our latest BloFin Build brings TWAP orders to the spotlight.

TWAP automatically splits large orders into smaller executions over time, helping traders reduce market impact and minimize slippage.
"""


def test_community_profiles_loaded_and_applied():
    s = scorer()
    assert s.community_profiles.get("communities", {}).get("bitunixglobal") == "exchange_official"
    profile = s._profile("bitunixglobal", None)
    assert profile.get("class") == "EXCHANGE_OFFICIAL"
    assert float(profile.get("support_weight", 0)) > 0
    assert float(profile.get("job_weight", 0)) > 0

    job = s._profile("LaborXWeb3Jobs", None)
    assert job.get("class") == "JOB_BOARD"
    assert float(job.get("job_weight", 0)) > 0


def test_bitunix_broadcast_family_is_not_a_lead():
    r = scorer().score(BITUNIX_PROMO, community_username="bitunixglobal")
    assert r.tier == "LOW"
    assert r.buyer_type != "CLIENT" or r.lead_type not in {
        "TRADING_SYSTEM_CONTRACT",
        "BOT_PURCHASE",
        "STRATEGY_IMPLEMENTATION",
    }
    assert any("marketing" in x or "exchange-official" in x for x in r.reasons)


def test_blofin_product_promo_is_not_client_lead():
    r = scorer().score(BLOFIN_PROMO, community_username="BloFin_Official")
    assert r.tier == "LOW"
    assert r.lead_type not in {
        "TRADING_SYSTEM_CONTRACT",
        "BOT_PURCHASE",
        "STRATEGY_IMPLEMENTATION",
    }


def test_laborx_hot_gigs_digest_is_not_client_lead():
    r = scorer().score(LABORX_DIGEST, community_username="laborx")
    assert r.tier == "LOW"
    assert r.lead_type == "JOB_VACANCY"
    assert r.buyer_type != "CLIENT"


def test_ethena_vacancy_is_not_ordinary_client_lead():
    r = scorer().score(
        "Ethena is hiring a remote Senior/Staff DeFi Engineer\n"
        "Ethena Labs is looking for a Senior Staff DeFi Engineer to design, "
        "deploy and maintain DeFi infrastructure and integrate security and trading systems.",
        community_username="jobstash",
    )
    assert r.tier == "LOW"
    assert r.buyer_type in {"RECRUITER", "UNKNOWN"}
    assert r.lead_type == "JOB_VACANCY"


def test_job_seeker_ru_is_not_target_opportunity():
    r = scorer().score(
        "🐹 Ищу задачи по разработке: Telegram Mini Apps, Web3, бэкенд и автоматизация\n"
        "Меня зовут Даниил – разработчик с опытом разработки масштабируемых проектов",
        community_username="solana_jobs",
    )
    assert r.tier == "LOW"
    assert r.buyer_type == "JOB_SEEKER"


def test_explicit_buyer_with_send_cv_remains_client():
    r = scorer().score(
        "Looking for a quant developer to build a crypto trading bot for Bybit. "
        "Budget $5000, urgent. Send your CV and portfolio."
    )
    assert r.tier == "HIGH"
    assert r.buyer_type == "CLIENT"


def test_buyer_firm_need_developer_is_not_service_ad():
    r = scorer().score(
        "We need a developer to fix our trading bot on Bybit. "
        "We are a proprietary trading firm and our engineer left. Budget $4000."
    )
    assert r.tier == "HIGH"
    assert r.buyer_type == "CLIENT"
    assert r.lead_type == "BOT_REPAIR"


def test_community_profile_lookup_is_case_insensitive():
    s = scorer()
    assert s._profile("bybitapi", None).get("class") == "EXCHANGE_OFFICIAL"
    assert s._profile("BybitAPI", None).get("class") == "EXCHANGE_OFFICIAL"


def test_vendor_outreach_is_not_target_opportunity():
    r = scorer().score(
        "Hello, Do you need developer support for your blockchain projects?\n"
        "I'm a full-stack developer and can help with Solana bots. DM me.",
        community_username="solanadev",
    )
    assert r.tier == "LOW"
    assert r.buyer_type == "SERVICE_PROVIDER"


def test_support_api_clarification_remains_excluded():
    r = scorer().score(
        "Python trading bot using pybit. ErrCode 170140. Why is Bybit rejecting the market order?",
        community_username="BybitAPI",
    )
    assert r.tier == "LOW"
    assert any("support" in x or "exchange-official" in x for x in r.reasons)


def test_exchange_profile_changes_scoring_path_vs_dev_community():
    text = "Anyone know how the Bybit V5 websocket order book stream works?"
    exchange = scorer().score(text, community_username="BybitAPI")
    general = scorer().score(text, community_username="unknown_dev_chat")
    assert exchange.tier == "LOW"
    assert any("community support prior" in x or "exchange-official" in x for x in exchange.reasons)
    # Same non-commercial text must not become a lead in either community.
    assert general.tier == "LOW"


def test_genuine_laborx_bot_repair_rfq_still_high():
    r = scorer().score(
        "🌟 Freelance Opportunity: Need Developer to Fix Polymarket Trading Bot (Urgent)\n"
        "💰 Budget: $1500",
        community_username="LaborXWeb3Jobs",
    )
    assert r.tier == "HIGH"
    assert r.lead_type in {"BOT_REPAIR", "TRADING_SYSTEM_CONTRACT"}


def test_genuine_copy_trading_buyer_still_high():
    r = scorer().score(
        "Если вы разработчик копитрейдинг бота, напишите мне. "
        "Мне нужен копитрейдинг бот на solana, который способен попадать в 0-1 block.",
        community_username="solana_dev_ru",
    )
    assert r.tier == "HIGH"
    assert r.buyer_type == "CLIENT"
    assert r.lead_type == "COPY_TRADING_PROJECT"
