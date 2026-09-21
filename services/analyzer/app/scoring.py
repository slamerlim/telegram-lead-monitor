from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ScoreResult:
    score: float
    tier: str
    buyer_type: str
    lead_type: str
    intent_score: float
    technical_score: float
    commercial_score: float
    promotion_score: float
    matched_keywords: list[str]
    matched_categories: list[str]
    reasons: list[str]
    contact_usernames: list[str]
    contact_urls: list[str]
    budget_amount: float | None = None
    budget_currency: str | None = None
    semantic_score: float | None = None


class LeadScorer:
    """Commercial lead classifier for trading-system development prospects.

    Core rule:
        Technical relevance is never sufficient for a commercial lead.

    A qualifying lead needs explicit commercial evidence such as buying, commissioning,
    hiring/contracting, fixing, customizing, or implementing a strategy. Exchange support,
    account support, product questions, vendor responses, job seekers, service providers and
    generic job aggregation are deliberately suppressed.
    """

    _username_rx = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{4,32})")
    _http_rx = re.compile(r"https?://\S+", re.IGNORECASE)
    _money_patterns = [
        re.compile(r"(?P<cur>\$|€|₽)\s*(?P<num>\d+(?:[.,]\d+)?\s*[kKmM]?)"),
        re.compile(
            r"(?P<num>\d+(?:[.,]\d+)?\s*[kKmM]?)\s*"
            r"(?P<cur>USD|USDT|USDC|EUR|RUB|\$|€|₽)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?P<num>\d+(?:[.,]\d+)?\s*[kKmM]?)\s*"
            r"(?P<cur>dollars?|euros?|руб(?:лей|\.)|доллар(?:ов|а)?)",
            re.IGNORECASE,
        ),
    ]
    _multiplier = {"k": 1_000.0, "m": 1_000_000.0}
    _currency_map = {
        "$": "USD", "usd": "USD", "usdt": "USDT", "usdc": "USDC",
        "eur": "EUR", "€": "EUR", "rub": "RUB", "₽": "RUB", "руб": "RUB",
        "рублей": "RUB", "доллар": "USD", "долларов": "USD", "dollar": "USD",
        "dollars": "USD", "euro": "EUR", "euros": "EUR",
    }
    _COMMERCIAL_TYPES = {
        "BOT_PURCHASE", "BOT_REPAIR", "BOT_CUSTOMIZATION", "STRATEGY_IMPLEMENTATION",
        "TRADING_SYSTEM_CONTRACT", "QUANT_ENGINEERING_CONTRACT", "ML_AI_ENGINEERING_CONTRACT",
        "ARBITRAGE_PROJECT", "COPY_TRADING_PROJECT", "MARKET_MAKING_PROJECT",
        "SOLANA_DEX_BOT_PROJECT",
    }
    _TARGET_TECH_CATEGORIES = {
        "trading_bot", "exchange_api", "execution", "strategy_automation", "arbitrage",
        "copy_trading", "solana_dex", "quant_ml", "python", "market_making",
    }
    _commercial_action_rx = re.compile(
        r"\b(?:need|looking for|seeking|want|hire|hiring|commission|purchase|buy|quote|proposal|"
        r"заказать|купить|нанять|ищу|нужен)\b",
        re.IGNORECASE | re.UNICODE,
    )
    _target_financial_domain_rx = re.compile(
        r"\b(?:crypto(?:currency)?|trading|trader|market|financial|finance|fintech|exchange|execution|order\s+book|strategy|algorithmic|algorithm|portfolio|alpha|derivatives|futures|perpetuals?|spot|defi|web3|blockchain|solana|ethereum|bybit|binance|okx|bitget|coinbase)\b|"
        r"(?:крипт|трейд|торг|финанс|бирж|исполнен|стратег|алгоритм|портфел|дериватив|фьючерс|спот|дефи|блокчейн)",
        re.IGNORECASE | re.UNICODE,
    )
    _developer_role_rx = re.compile(
        r"\b(?:developer|programmer|engineer|coder|freelancer|contractor|quant|quantitative|ml engineer|ai engineer|machine learning)\b|"
        r"(?:разработчик|программист|инженер|фрилансер|подрядчик|квант|разработчик ml|ai)",
        re.IGNORECASE | re.UNICODE,
    )
    _repair_action_rx = re.compile(
        r"\b(?:fix|repair|debug|troubleshoot|diagnose|resolve)\b|(?:починить|исправить|дебаг|диагностировать|устранить)",
        re.IGNORECASE | re.UNICODE,
    )
    _customization_action_rx = re.compile(
        r"\b(?:customi[sz]e|modify|extend|add|change)\b|(?:доработать|модифицировать|добавить|изменить|кастомизировать)",
        re.IGNORECASE | re.UNICODE,
    )
    _implementation_action_rx = re.compile(
        r"\b(?:implement|automate|build|develop|create)\b|(?:реализовать|внедрить|автоматизировать|разработать|создать)",
        re.IGNORECASE | re.UNICODE,
    )
    _bot_context_rx = re.compile(
        r"\b(?:trading bot|crypto bot|dca bot|grid bot|scalping bot|copy[- ]trading bot|bot for|my bot|our bot)\b|"
        r"(?:торговый бот|торгового бота|мой бот|наш бот|бот для)",
        re.IGNORECASE | re.UNICODE,
    )
    _strategy_context_rx = re.compile(
        r"\b(?:strategy|trading strategy|algorithm|trading system|automated trading|algorithmic trading)\b|"
        r"(?:стратегия|торговая стратегия|алгоритм|торговая система|алготрейдинг)",
        re.IGNORECASE | re.UNICODE,
    )

    def __init__(self, path: str, high: float = 75, medium: float = 50) -> None:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        self.thresholds = data.get("thresholds", {})
        self.weights: dict[str, float] = data.get("categories", {})
        self.terms: dict[str, list[str]] = data.get("terms", {})
        self.patterns: dict[str, list[re.Pattern[str]]] = {
            key: [re.compile(pattern, re.IGNORECASE | re.UNICODE) for pattern in values]
            for key, values in data.get("patterns", {}).items()
        }
        self.high = float(self.thresholds.get("high", high))
        self.medium = float(self.thresholds.get("medium", medium))
        self.community_profiles = data.get("community_profiles", {})
        self.compiled = {
            category: [
                (term, re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE | re.UNICODE))
                for term in terms
            ]
            for category, terms in self.terms.items()
        }

    def _profile(self, community_username: str | None, community_name: str | None) -> dict[str, Any]:
        key = (community_username or "").strip()
        profiles = self.community_profiles.get("communities", {})
        profile_defs = self.community_profiles.get("profiles", {})
        profile_name = profiles.get(key) or profiles.get(key.lower())
        if not profile_name and community_name:
            lower_name = community_name.lower()
            for username, name in profiles.items():
                if username.lower() in lower_name or lower_name in username.lower():
                    profile_name = name
                    break
        return profile_defs.get(profile_name or self.community_profiles.get("default_profile", "general"), profile_defs.get("general", {}))

    def _matches(self, text: str) -> tuple[list[str], list[str], dict[str, list[str]]]:
        matched_keywords: list[str] = []
        categories: list[str] = []
        by_category: dict[str, list[str]] = {}
        for category, pairs in self.compiled.items():
            matches = [term for term, rx in pairs if rx.search(text)]
            if matches:
                matched_keywords.extend(matches)
                categories.append(category)
                by_category[category] = matches
        return list(dict.fromkeys(matched_keywords)), list(dict.fromkeys(categories)), by_category

    def _pattern_hits(self, text: str, category: str) -> list[str]:
        return [pattern.pattern for pattern in self.patterns.get(category, []) if pattern.search(text)]

    def _has_explicit_commercial_intent(self, text: str) -> tuple[bool, list[str]]:
        hits: list[str] = []
        for category in (
            "commercial_purchase",
            "commercial_repair",
            "commercial_customization",
            "commercial_hire",
            "commercial_implementation",
        ):
            hits.extend(self._pattern_hits(text, category))
        return bool(hits), hits

    @classmethod
    def is_commercial_lead_type(cls, lead_type: str) -> bool:
        return lead_type in cls._COMMERCIAL_TYPES

    def _has_target_financial_domain(self, text: str) -> bool:
        return self._target_financial_domain_rx.search(text) is not None

    def _is_support_like(self, text: str) -> bool:
        return bool(self._pattern_hits(text, "platform_support"))

    def _is_vendor_response(self, text: str) -> bool:
        return bool(self._pattern_hits(text, "vendor_response"))

    def _is_job_aggregation(self, text: str) -> bool:
        hits = self._pattern_hits(text, "job_aggregator")
        return bool(hits)

    def _is_multi_job_aggregation(self, text: str) -> bool:
        # A digest containing several independent vacancies must not be promoted merely
        # because one item happens to mention a target role such as AI/ML or quant.
        hiring_occurrences = len(re.findall(r"\b(?:hiring|is\s+hiring)\b", text, re.IGNORECASE))
        vacancy_occurrences = len(re.findall(r"\b(?:vacancy|position|job)\b", text, re.IGNORECASE))
        return hiring_occurrences >= 3 or vacancy_occurrences >= 4

    def _classify_buyer(self, text: str) -> tuple[str, list[str]]:
        reasons: list[str] = []
        provider = self._pattern_hits(text, "service_provider")
        seeker = self._pattern_hits(text, "job_seeker")
        recruiter = self._pattern_hits(text, "recruiter")
        explicit, explicit_hits = self._has_explicit_commercial_intent(text)
        support = self._is_support_like(text)
        vendor = self._is_vendor_response(text)

        if vendor:
            reasons.append("vendor/support response language detected")
            return "SERVICE_PROVIDER", reasons
        # A service-provider advertisement is not a buyer simply because it contains
        # words such as "develop", "fix", "build", or a price.
        if provider:
            reasons.append("service-provider/self-promotion language detected")
            return "SERVICE_PROVIDER", reasons
        if seeker and not explicit:
            reasons.append("job-seeker/CV language detected")
            return "JOB_SEEKER", reasons
        if recruiter:
            # Vacancy/job-ad language stays RECRUITER. Explicit commercial commission
            # phrasing ("looking for a developer to build …") is buyer-side CLIENT.
            vacancy_ad = bool(
                re.search(
                    r"#(?:vacancy|hiring|job)\b|"
                    r"\bhiring\b|"
                    r"\bwe\s+(?:are\s+)?(?:looking\s+for|seeking|hiring)\b|"
                    r"\b(?:vacancy|position)\b.{0,100}\b(?:developer|engineer|trader|quant|programmer|ml|ai)\b|"
                    r"\b(?:freelance|contract)\s+opportunity\b",
                    text,
                    re.IGNORECASE,
                )
            )
            if explicit and not vacancy_ad:
                reasons.append("commercial hiring/contract language detected")
                return "CLIENT", reasons
            if explicit:
                reasons.append("commercial hiring/contract language detected")
            else:
                reasons.append("job/recruitment language detected without target commercial engagement")
            return "RECRUITER", reasons
        if support and not explicit:
            reasons.append("exchange/product/account support language without commercial request")
            return "SUPPORT_SEEKER", reasons
        if explicit:
            reasons.append(f"explicit commercial intent: {', '.join(explicit_hits[:4])}")
            return "CLIENT", reasons
        return "UNKNOWN", reasons

    def _classify_lead_type(self, text: str, categories: list[str], buyer_type: str) -> str:
        lower = text.lower()
        explicit, _ = self._has_explicit_commercial_intent(text)
        if buyer_type == "SERVICE_PROVIDER":
            return "SERVICE_ADVERTISEMENT"
        if buyer_type == "JOB_SEEKER":
            return "JOB_SEEKER"
        if buyer_type == "RECRUITER":
            # Contract/freelance target roles are commercial; otherwise just a vacancy.
            contract = bool(self._pattern_hits(text, "contract_engagement"))
            target_domain = self._has_target_financial_domain(text)
            target_quant = ("quant_ml" in categories and bool(re.search(r"\bquant(?:itative)?\b", lower)) and target_domain)
            target_ml = ("quant_ml" in categories and bool(re.search(r"\b(?:ml|ai|machine learning|artificial intelligence)\b", lower)) and target_domain)
            target_trading = target_domain and bool(re.search(r"\b(?:trading system|algorithmic trading|trading bot|execution|market making|crypto trading)\b", lower))
            if explicit and contract and target_quant:
                return "QUANT_ENGINEERING_CONTRACT"
            if explicit and contract and target_ml:
                return "ML_AI_ENGINEERING_CONTRACT"
            if explicit and contract and target_trading:
                return "TRADING_SYSTEM_CONTRACT"
            return "JOB_VACANCY"

        if buyer_type != "CLIENT":
            if buyer_type == "SUPPORT_SEEKER":
                return "PLATFORM_SUPPORT"
            return "TECHNICAL_QUESTION" if categories else "NOISE"

        if not explicit:
            return "TECHNICAL_QUESTION"
        purchase = bool(self._pattern_hits(text, "commercial_purchase"))
        repair = bool(self._pattern_hits(text, "commercial_repair")) or (
            self._repair_action_rx.search(text) is not None
            and self._bot_context_rx.search(text) is not None
            and (self._developer_role_rx.search(text) is not None or self._commercial_action_rx.search(text) is not None)
        )
        customize = bool(self._pattern_hits(text, "commercial_customization")) or (
            self._customization_action_rx.search(text) is not None
            and self._bot_context_rx.search(text) is not None
            and (self._developer_role_rx.search(text) is not None or self._commercial_action_rx.search(text) is not None)
        )
        implementation = bool(self._pattern_hits(text, "commercial_implementation")) or (
            self._implementation_action_rx.search(text) is not None
            and self._strategy_context_rx.search(text) is not None
            and (self._developer_role_rx.search(text) is not None or self._commercial_action_rx.search(text) is not None)
        )
        if purchase and "trading_bot" in categories:
            return "BOT_PURCHASE"
        if repair and "trading_bot" in categories:
            return "BOT_REPAIR"
        if customize and ("trading_bot" in categories or "strategy_automation" in categories):
            return "BOT_CUSTOMIZATION"
        if implementation:
            return "STRATEGY_IMPLEMENTATION"
        if "arbitrage" in categories and explicit:
            return "ARBITRAGE_PROJECT"
        if "copy_trading" in categories and explicit:
            return "COPY_TRADING_PROJECT"
        if "market_making" in categories and explicit:
            return "MARKET_MAKING_PROJECT"
        if "solana_dex" in categories and explicit:
            return "SOLANA_DEX_BOT_PROJECT"
        if "quant_ml" in categories and explicit and self._has_target_financial_domain(text):
            if re.search(r"\b(?:ml|ai|machine learning|artificial intelligence)\b", lower):
                return "ML_AI_ENGINEERING_CONTRACT"
            if re.search(r"\bquant(?:itative)?\b", lower):
                return "QUANT_ENGINEERING_CONTRACT"
        if "execution" in categories or "exchange_api" in categories or "trading_bot" in categories:
            return "TRADING_SYSTEM_CONTRACT"
        return "TECHNICAL_QUESTION"

    def _extract_contacts(self, text: str) -> tuple[list[str], list[str]]:
        usernames = []
        for username in self._username_rx.findall(text):
            if username.lower() in {"username", "channelname", "example", "everyone", "here"}:
                continue
            usernames.append(username)
        usernames = list(dict.fromkeys(usernames))
        return usernames, [f"https://t.me/{u}" for u in usernames]

    def _normalize_money_number(self, raw_num: str) -> str:
        """Normalize money number tokens, preserving thousand separators.

        `$3,000` must become 3000, not 3.0. A single comma/dot with 1-2 fraction
        digits is treated as a decimal separator (e.g. `3,5` → `3.5`).
        """
        raw_num = re.sub(r"\s+", "", raw_num)
        if not raw_num:
            return raw_num

        if "," in raw_num and "." in raw_num:
            if raw_num.rfind(",") > raw_num.rfind("."):
                # European style: 1.234,56
                return raw_num.replace(".", "").replace(",", ".")
            # US style: 1,234.56
            return raw_num.replace(",", "")

        if "," in raw_num:
            left, right = raw_num.split(",", 1)
            if right.isdigit() and len(right) == 3 and left.replace(",", "").isdigit():
                return raw_num.replace(",", "")
            return raw_num.replace(",", ".")

        if raw_num.count(".") > 1:
            return raw_num.replace(".", "")

        return raw_num

    def _extract_budget(self, text: str) -> tuple[float | None, str | None]:
        for rx in self._money_patterns:
            match = rx.search(text)
            if not match:
                continue
            raw_num = self._normalize_money_number(match.group("num"))
            suffix = raw_num[-1].lower() if raw_num and raw_num[-1].lower() in self._multiplier else ""
            if suffix:
                raw_num = raw_num[:-1]
            try:
                amount = float(Decimal(raw_num)) * self._multiplier.get(suffix, 1.0)
            except (InvalidOperation, ValueError):
                continue
            raw_cur = match.group("cur").lower()
            return amount, self._currency_map.get(raw_cur, raw_cur.upper())
        return None, None

    def score(self, text: str, community_username: str | None = None, community_name: str | None = None) -> ScoreResult:
        clean = " ".join(text.split())
        matched_keywords, categories, by_category = self._matches(clean)
        lower = clean.lower()
        profile = self._profile(community_username, community_name)

        technical_hits = self._pattern_hits(clean, "technical_problem")
        if technical_hits and any(t in lower for t in ("api", "pybit", "/v5/", "websocket", "order", "errcode", "error code")):
            if "exchange_api" not in categories:
                categories.append("exchange_api")
                by_category["exchange_api"] = ["technical API problem"]
                matched_keywords.append("technical API problem")
        if technical_hits and any(t in lower for t in ("bot", "trading bot", "торговый бот", "торгового бота")):
            if "trading_bot" not in categories:
                categories.append("trading_bot")
                by_category["trading_bot"] = ["existing bot problem"]
                matched_keywords.append("existing bot problem")

        buyer_type, buyer_reasons = self._classify_buyer(clean)
        # Strategy implementation posts often lack keyword-category hits; attach the
        # category so commercial scoring and lead typing stay consistent.
        if self._pattern_hits(clean, "commercial_implementation") and "strategy_automation" not in categories:
            categories.append("strategy_automation")
            by_category["strategy_automation"] = ["strategy implementation request"]
            matched_keywords.append("strategy implementation request")
        lead_type = self._classify_lead_type(clean, categories, buyer_type)
        contacts, contact_urls = self._extract_contacts(clean)
        budget_amount, budget_currency = self._extract_budget(clean)
        explicit, _ = self._has_explicit_commercial_intent(clean)
        support = self._is_support_like(clean)
        vendor = self._is_vendor_response(clean)
        job_agg = self._is_job_aggregation(clean)
        multi_job_agg = self._is_multi_job_aggregation(clean)

        reasons = list(buyer_reasons)
        technical_score = 0.0
        commercial_score = 0.0
        promotion_score = 0.0
        score = 0.0

        for category in categories:
            weight = float(self.weights.get(category, 0.0))
            if category == "negative_promotion":
                promotion_score += weight
                score += weight
                reasons.append(f"promotion penalty: {', '.join(by_category[category][:3])}")
                continue
            if category in self._TARGET_TECH_CATEGORIES:
                technical_score += max(0.0, weight)
            score += weight
            reasons.append(f"{category}: {', '.join(by_category[category][:4])}")

        intent_score = 0.0
        if explicit:
            intent_score += 45
            commercial_score += 45
            score += 45
            reasons.append("explicit commercial request detected")
        elif buyer_type == "CLIENT":
            # This path is deliberately small and should not independently create a lead.
            intent_score += 8
            commercial_score += 8
            score += 8
            reasons.append("weak buyer-like context without explicit commercial action")

        if budget_amount is not None:
            score += 12
            commercial_score += 12
            reasons.append(f"explicit numeric budget/price: {budget_amount:g} {budget_currency}")

        if bool(self._pattern_hits(clean, "urgency")) and explicit:
            score += 5
            commercial_score += 5
            reasons.append("commercial urgency detected")

        if lead_type in {"BOT_REPAIR", "BOT_CUSTOMIZATION", "STRATEGY_IMPLEMENTATION", "TRADING_SYSTEM_CONTRACT", "QUANT_ENGINEERING_CONTRACT", "ML_AI_ENGINEERING_CONTRACT"}:
            score += 18
            commercial_score += 18
            reasons.append(f"target service confirmed: {lead_type}")

        if profile.get("job_weight"):
            score -= float(profile["job_weight"])
        if profile.get("support_weight") and not explicit:
            score -= float(profile["support_weight"])
            reasons.append(f"community support prior: {community_username or community_name or 'unknown'}")
        if profile.get("commercial_boost") and explicit:
            score += float(profile["commercial_boost"])

        # Hard commercial gate: no explicit purchase/hire/repair/customization/implementation
        # request means there is no qualifying sales lead.
        qualifying_type = lead_type in self._COMMERCIAL_TYPES
        contract_target = lead_type in {"TRADING_SYSTEM_CONTRACT", "QUANT_ENGINEERING_CONTRACT", "ML_AI_ENGINEERING_CONTRACT"}
        if not qualifying_type:
            score = min(score, 34.0)
        if support and not explicit:
            score = min(score, 20.0)
            reasons.append("support-only request veto")
        if vendor or buyer_type in {"SERVICE_PROVIDER", "JOB_SEEKER"}:
            score = min(score, 10.0)
            promotion_score -= 45
            reasons.append("non-buyer veto")
        if job_agg and not contract_target:
            score = min(score, 25.0)
            reasons.append("job-aggregator veto")
        if multi_job_agg:
            score = min(score, 25.0)
            lead_type = "JOB_VACANCY"
            reasons.append("multi-job aggregation veto")

        # Questions about how a vendor's product works are not repair/customization orders.
        if self._pattern_hits(clean, "product_feedback") and not explicit:
            score = min(score, 20.0)
            lead_type = "TECHNICAL_QUESTION"
            reasons.append("product-support/feature-feedback veto")

        if len(self._http_rx.findall(clean)) >= 8:
            score -= 5
            reasons.append("link-heavy message")

        score = max(0.0, min(100.0, score))
        tier = "HIGH" if score >= self.high else "MEDIUM" if score >= self.medium else "LOW"
        if not qualifying_type:
            tier = "LOW"
        if support and not explicit:
            tier = "LOW"
        if multi_job_agg:
            tier = "LOW"

        return ScoreResult(
            score=score,
            tier=tier,
            buyer_type=buyer_type,
            lead_type=lead_type,
            intent_score=intent_score,
            technical_score=technical_score,
            commercial_score=commercial_score,
            promotion_score=promotion_score,
            matched_keywords=list(dict.fromkeys(matched_keywords)),
            matched_categories=list(dict.fromkeys(categories)),
            reasons=reasons,
            contact_usernames=contacts,
            contact_urls=contact_urls,
            budget_amount=budget_amount,
            budget_currency=budget_currency,
        )
