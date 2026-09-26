"""HIGH-recall commercial discovery from LOW-tier messages (YAML patterns only).

disc_v3/v4 add buyer_direction + non-buyer vetoes. Does NOT alter LeadScorer.score().
disc_v4 expands positive buyer-signal conjunctions without weakening hard vetoes.
disc_v5 recovers NO_PATH buyer/project phrases (C-only); keeps LaborX/gig vetoes.
disc_v6 = disc_v4 paths + narrow FO gig-marketplace project carve (D); default after acceptance.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

DISCOVERY_VERSION = "disc_v6"
DISCOVERY_VERSION_V2 = "disc_v2"
DISCOVERY_VERSION_V3 = "disc_v3"
DISCOVERY_VERSION_V4 = "disc_v4"
DISCOVERY_VERSION_V5 = "disc_v5"
DISCOVERY_VERSION_V6 = "disc_v6"

BuyerDirection = Literal[
    "BUYER",
    "EMPLOYER",
    "SEEKER",
    "PROVIDER",
    "RECRUITER",
    "UNKNOWN",
]

_COMMERCIAL_PATTERN_CATS = (
    "commercial_purchase",
    "commercial_repair",
    "commercial_customization",
    "commercial_hire",
    "commercial_implementation",
)
_TECH_CATS = frozenset(
    {
        "trading_bot",
        "exchange_api",
        "trading_system",
        "strategy_automation",
        "arbitrage",
        "copy_trading",
        "market_making",
        "solana_dex",
        "quant_engineering",
        "ml_ai_engineering",
    }
)
_DOMAIN_CATS = frozenset(
    {
        "trading_bot",
        "exchange_api",
        "trading_system",
        "strategy_automation",
        "arbitrage",
        "copy_trading",
        "market_making",
        "solana_dex",
    }
)
_HARD_EXCLUDE_CATS = frozenset(
    {
        "marketing_broadcast",
        "news_digest",
        "job_aggregator",
        "negative_promotion",
    }
)

# Corporate employment vacancy (not project RFQ).
_EMPLOYER_RX = re.compile(
    r"(?:"
    r"\bis\s+hiring\b|"
    r"\bwe(?:'re|\s+are)\s+hiring\b|"
    r"\bapply\s+(?:now|here|below|via|at)\b|"
    r"\b(?:full[- ]time|part[- ]time)\s+(?:role|position|job|engineer|developer)\b|"
    r"\bsalary\s*(?:range|:)|"
    r"\bbenefits\s*(?:include|:)|"
    r"\bjob\s+description\b|"
    r"\bopen\s+(?:role|position|vacancy)\b|"
    r"\bcareer(?:s)?\s+page\b|"
    r"#вакансия\b|"
    r"#hiring\b"
    r")",
    re.IGNORECASE,
)
# Hard corporate JD (v4): salary/board/is-hiring — not bare full-time alone.
_CORPORATE_JD_HARD_RX = re.compile(
    r"(?:"
    r"\bis\s+hiring\b|"
    r"\bwe(?:'re|\s+are)\s+hiring\b|"
    r"\$\d{2,3},\d{3}\s*[-–—]\s*\$?\d{2,3},\d{3}|"
    r"\bjobstash\b|"
    r"\bwant your message here\b|"
    r"\bsponsoring\b.{0,40}\bjob\b|"
    r"\bapply\s+(?:now|here|below)\b|"
    r"\bcareer(?:s)?\s+page\b"
    r")",
    re.IGNORECASE,
)
# Legacy v3 demotion set (includes bare full-time).
_CORPORATE_JD_RX = re.compile(
    r"(?:"
    r"\bis\s+hiring\b|"
    r"\$\d{2,3},\d{3}\s*[-–—]\s*\$?\d{2,3},\d{3}|"
    r"\bjobstash\b|"
    r"\bwant your message here\b|"
    r"\bsponsoring\b.{0,40}\bjob\b|"
    r"\bfull[- ]time\b|"
    r"\bremote\s+(?:role|position|software engineer)\b"
    r")",
    re.IGNORECASE,
)

# disc_v4/v5 positive buyer-signal families
_DIRECT_REQUEST_RX = re.compile(
    r"(?:"
    r"\blooking\s+for\s+(?:someone|a\s+developer|a\s+freelancer|a\s+contractor|an?\s+engineer)\b|"
    r"\bneed\s+someone\s+(?:to|who)\b|"
    r"\bneed\s+(?:a\s+)?(?:developer|engineer|freelancer|contractor)\b|"
    r"\bcan\s+anyone\s+(?:build|develop|fix|implement|automate|code)\b|"
    r"\bwho\s+can\s+(?:build|develop|fix|implement|automate|code)\b|"
    r"\bsearching\s+for\s+(?:a\s+)?(?:developer|freelancer|contractor)\b|"
    r"\bищу\s+(?:разработчик|программист|фрилансер|подрядчик)\w*\b|"
    r"\bнужен\s+(?:разработчик|программист|фрилансер|подрядчик)\w*\b|"
    r"\bтребуется\s+(?:разработчик|программист)\w*\b|"
    r"\bкто\s+(?:может|сможет)\s+(?:разработать|починить|сделать|автоматизировать)\b|"
    r"\bнужна\s+автоматизаци\w*\b"
    r")",
    re.IGNORECASE,
)
# disc_v5: additional buyer/request phrases that v4 missed (C-only; not employment/gig).
# Keep tight: require explicit hire-to-build / bot-deliverable / pay-to-automate forms.
# Do NOT use bare "нужен человек" / "требуется" (Russian job-board contamination).
_DIRECT_REQUEST_V5_RX = re.compile(
    r"(?:"
    r"\blooking\s+for\s+(?:an?\s+)?(?:experienced\s+|senior\s+|junior\s+|python\s+|ml\s+|ai\s+|quant\s+|backend\s+)?"
    r"(?:developer|engineer|freelancer|contractor|programmer)\b|"
    r"\b(?:need|want|seeking)\s+(?:an?\s+)?(?:experienced\s+|senior\s+|python\s+|quant\s+)?"
    r"(?:developer|engineer|freelancer|contractor|programmer)\b|"
    r"\bhire\s+(?:an?\s+)?(?:developer|engineer|freelancer|contractor)\s+to\s+"
    r"(?:build|develop|implement|automate|fix|integrate)\b|"
    r"\blooking\s+to\s+(?:hire|pay|commission)\s+someone\b|"
    r"\b(?:need|want)\s+(?:an?\s+)?(?:custom\s+)?trading\s+bot\b|"
    r"\b(?:need|want)\s+(?:an?\s+)?custom\s+bot\b|"
    r"\b(?:build|develop)\s+(?:me\s+|us\s+)?(?:an?\s+)?(?:custom\s+)?(?:trading\s+)?bot\b|"
    r"\bcommission\s+(?:a\s+)?(?:custom\s+)?(?:trading\s+)?bot\b|"
    r"\b(?:anyone|anybody)\s+(?:available\s+to|who\s+can)\s+(?:build|code|develop|fix|automate)\b|"
    r"\bcan\s+(?:anybody|someone)\s+(?:build|develop|code|fix)\b|"
    r"\bi\s+need\s+help\s+(?:building|developing|fixing)\b|"
    r"\bneed\s+an?\s+expert\s+to\s+(?:integrate|build|develop|fix)\b|"
    r"\bseeking\s+(?:a\s+)?(?:freelancer|contractor|developer)\s+to\s+"
    r"(?:develop|build|implement|integrate)\b|"
    r"\brequire\s+(?:a\s+)?contractor\s+for\s+(?:solana|dex|arbitrage|trading|bot)\b|"
    r"\bneed\s+(?:a\s+)?quant\s+engineer\s+for\b|"
    r"\bpaid\s+(?:project|gig)\b"
    r")",
    re.IGNORECASE,
)
_OWNERSHIP_RX = re.compile(
    r"(?:"
    r"\b(?:my|our)\s+(?:bot|strategy|trading\s+system|system|exchange\s+integration|api)\b|"
    r"\b(?:existing|current|broken)\s+(?:bot|strategy|system|infrastructure)\b|"
    r"\bмо[яй]\s+(?:бот|стратеги\w*|систем\w*)\b|"
    r"\bнаш[ае]?\s+(?:бот|стратеги\w*|систем\w*)\b|"
    r"\bтекущ\w*\s+бот\b"
    r")",
    re.IGNORECASE,
)
# disc_v5: allow one domain token between possessive and asset ("my Binance strategy").
# Exclude provider "my telegram bot" / "my signal bot" offer patterns via negative lookbehind-ish
# by requiring trading/strategy/exchange/api/system assets (not bare bot after chat apps).
_OWNERSHIP_V5_RX = re.compile(
    r"(?:"
    r"\b(?:my|our)\s+(?:binance|bybit|okx|exchange|futures|spot|existing|current|broken)\s+"
    r"(?:bot|strategy|system|api|integration)\b|"
    r"\b(?:my|our)\s+(?:trading\s+)?(?:strategy|system|execution\s+(?:engine|system)|"
    r"exchange\s+integration)\b|"
    r"\b(?:existing|current|broken)\s+(?:\w+\s+){0,1}(?:trading\s+)?(?:bot|strategy|system)\b"
    r")",
    re.IGNORECASE,
)
# True project-budget language (excludes lone market $-amounts / prize pools).
_TRUE_BUDGET_RX = re.compile(
    r"(?:"
    r"\bbudget\b|"
    r"\bбюджет\b|"
    r"\bfixed[- ]price\b|"
    r"\bhourly\s+(?:rate|\$)|"
    r"\bquote\b|"
    r"\bmilestone\b|"
    r"\bcan\s+pay\b|"
    r"\bi\s+can\s+pay\b|"
    r"\bwe\s+can\s+pay\b|"
    r"\bpaying\s+for\b|"
    r"\bpaid\s+(?:project|gig|freelance|work)\b|"
    r"\$\s?\d{2,6}(?:\s*(?:k|usd))?\s*(?:budget|fixed|total)|"
    r"\bbudget\s*(?:of|:)?\s*\$?\d|"
    r"\bстоимость\b|"
    r"\bоплат\w*\b"
    r")",
    re.IGNORECASE,
)
# Bot/system deliverable as the thing being procured (v5 path F).
_BOT_DELIVERABLE_RX = re.compile(
    r"(?:"
    r"\b(?:custom\s+)?(?:trading\s+)?(?:grid\s+)?bot\b|"
    r"\b(?:copy\s+trading|arbitrage|market[- ]making|sniper)\s+bot\b|"
    r"\bexecution\s+(?:engine|system)\b|"
    r"\btrading\s+system\b|"
    r"\bexchange\s+api\s+integration\b|"
    r"\bpybit\b"
    r")",
    re.IGNORECASE,
)
_REPAIR_CONJ_RX = re.compile(
    r"(?:"
    r"\b(?:broken|bug|not\s+working|doesn'?t\s+work|malfunction|failed|error|fix|repair)\b"
    r".{0,80}\b(?:bot|strategy|trading|exchange|api|execution|bybit|binance|okx)\b|"
    r"\b(?:bot|strategy|trading|api)\b.{0,80}\b(?:broken|bug|not\s+working|fix|repair)\b|"
    r"\bпочинить\b.{0,60}\b(?:бот|стратеги|api|бирж)\w*\b|"
    r"\b(?:бот|стратеги)\w*.{0,60}\bпочинить\b"
    r")",
    re.IGNORECASE,
)
_AUTOMATION_RX = re.compile(
    r"(?:"
    r"\bautomate\s+(?:my|our|this|the)\s+(?:strategy|trading|process|workflow)\b|"
    r"\b(?:turn|convert)\s+(?:my|our|this)\s+strategy\s+into\s+(?:a\s+)?bot\b|"
    r"\bimplement\s+(?:my|our|this|the)\s+strategy\b|"
    r"\bcode\s+this\s+strategy\b|"
    r"\bbuild\s+(?:an?\s+)?execution\s+system\b|"
    r"\bавтоматизировать\s+(?:стратеги|торгов)\w*\b|"
    r"\bреализовать\s+стратеги\w*\b"
    r")",
    re.IGNORECASE,
)
_PROCUREMENT_RX = re.compile(
    r"(?:"
    r"\b(?:budget|quote|rfp|rfq|fixed[- ]price|hourly|deadline|timeline|"
    r"contractor|freelancer|commission|deliverable|milestone|scope)\b|"
    r"\b(?:бюджет|стоимость|сроки|оплата|подрядчик|фриланс|проект)\w*\b"
    r")",
    re.IGNORECASE,
)
_PROJECT_PROCUREMENT_RX = re.compile(
    r"(?:"
    r"\bclient\s+(?:needs|is\s+looking\s+for|looking\s+for)\b.{0,100}"
    r"\b(?:contractor|freelancer|developer|engineer)\b|"
    r"\blooking\s+for\s+(?:a\s+)?(?:contractor|freelancer)\s+(?:to|for)\b|"
    r"\b(?:i|we)\s+(?:need|are\s+looking\s+for)\b.{0,80}"
    r"\b(?:contractor|freelancer|developer)\b.{0,80}"
    r"\b(?:budget|fixed[- ]price|project)\b|"
    r"\bнужен\s+(?:подрядчик|фрилансер|разработчик)\b.{0,60}\b(?:бюджет|проект|бот)\b"
    r")",
    re.IGNORECASE,
)
_DOMAIN_LOOSE_RX = re.compile(
    r"\b(?:bybit|binance|okx|futures|websocket|trading\s+bot|trading\s+strategy|"
    r"arbitrage|copy\s+trad|market\s+mak|solana|dex|quant|pybit|exchange\s*api|"
    r"execution\s+engine|grid\s+bot)\b",
    re.IGNORECASE,
)

# Freelance marketplace / LaborX-style listings (not first-person buyers).
_GIG_MARKETPLACE_RX = re.compile(
    r"(?:"
    r"🌟\s*freelance\s+opportunity\b|"
    r"\bfreelance\s+opportunity\s*:|"
    r"\bnew\s+project\s+on\s+laborx\b|"
    r"\blaborx\b|"
    r"\bgig\s+of\s+the\s+day\b|"
    r"\bwant\s+your\s+message\s+here\b|"
    r"🤝\s*with\s*:|"
    r"\bwith:\s*[A-Z][a-z]+\s+[A-Z]"  # LaborX "With: First Last"
    r")",
    re.IGNORECASE,
)

# Narrow FO listing templates eligible for disc_v6 project carve (not bare laborx / GIG OF THE DAY).
_FO_LISTING_RX = re.compile(
    r"(?:"
    r"🌟\s*freelance\s+opportunity\b|"
    r"\bfreelance\s+opportunity\s*:|"
    r"\bnew\s+project\s+on\s+laborx\b"
    r")",
    re.IGNORECASE,
)
# Trading-bot / repair project body inside an FO listing.
_FO_TRADING_BOT_PROJECT_RX = re.compile(
    r"(?:"
    r"\b(?:trading\s+bot|crypto\s+(?:trading\s+)?bot|grid\s+bot|"
    r"python\s+(?:crypto\s+)?(?:trading\s+)?bot|copy\s+trading\s+bot)\b|"
    r"\b(?:fix|repair|quick\s+fix)\b.{0,60}\b(?:trading\s+)?bot\b|"
    r"\b(?:trading\s+)?bot\s+(?:developer|engineer|fix|repair)\b|"
    r"\b(?:developer|engineer)\b.{0,40}\b(?:trading\s+)?bot\b"
    r")",
    re.IGNORECASE,
)
_FO_EMPLOYMENT_FLOOD_RX = re.compile(
    r"(?:"
    r"\b(?:full[- ]time|part[- ]time)\s+(?:role|position|job|engineer|developer)\b|"
    r"\bsalary\s*(?:range|:)|"
    r"\bapply\s+(?:now|here|below)\b|"
    r"#(?:вакансия|hiring|fulltime)\b|"
    r"\bopen\s+(?:role|position|vacancy)\b"
    r")",
    re.IGNORECASE,
)

# Product/support "FIX API" / exchange announcements — not buyer repair RFQs.
_REPAIR_FALSE_POSITIVE_RX = re.compile(
    r"(?:"
    r"\bfix\s+api\b|"
    r"\bpleased\s+to\s+announce\b|"
    r"\bnew\s+feature\b|"
    r"\bbreaking\s*:|"
    r"\benglish-only\s+chat\b|"
    r"\berror\s+message\s+provided\b"
    r")",
    re.IGNORECASE,
)


def _is_gig_project_carve(clean: str) -> bool:
    """Message-level FO carve: listing ∧ (repair ∨ trading-bot project) ∧ budget evidence.

    Does not lift bare laborx / gig-of-the-day / sponsorship marketplace noise.
    """
    if not _FO_LISTING_RX.search(clean):
        return False
    if _CORPORATE_JD_HARD_RX.search(clean) or _FO_EMPLOYMENT_FLOOD_RX.search(clean):
        return False
    repair = bool(_REPAIR_CONJ_RX.search(clean)) and not bool(
        _REPAIR_FALSE_POSITIVE_RX.search(clean)
    )
    trading_bot_project = bool(_FO_TRADING_BOT_PROJECT_RX.search(clean))
    if not (repair or trading_bot_project):
        return False
    budget_or_project = bool(_TRUE_BUDGET_RX.search(clean)) or bool(
        re.search(r"💰\s*budget\b|\bbudget\s*:\s*\$", clean, re.IGNORECASE)
    )
    return budget_or_project


# Concrete project / deliverable buyer language (first-person / RFQ — not JD "to build").
_PROJECT_SCOPE_RX = re.compile(
    r"(?:"
    r"\b(?:my|our)\s+(?:bot|system|strategy|project|exchange|api)\b|"
    r"\bneed\s+someone\s+to\s+(?:build|develop|repair|fix|implement|automate)\b|"
    r"\bwho\s+can\s+(?:build|develop|repair|implement|automate)\b|"
    r"\bcan\s+someone\s+(?:build|develop|repair|fix)\b|"
    r"\b(?:budget|quote|rfp|proposal)\b.{0,40}\b(?:project|bot|system|development)\b|"
    r"\b(?:implement|automate)\b.{0,80}\b(?:strategy|bot|trading)\b|"
    r"\b(?:починить|кастом|нужен\s+разработчик|ищу\s+разработчика)\b|"
    r"\bclient\s+needs\b.{0,80}\b(?:build|develop|implement|contractor)\b|"
    r"\blooking\s+for\s+(?:a\s+)?(?:freelancer|contractor)\s+(?:to|for)\b|"
    r"\b(?:i|we)\s+(?:need|are\s+looking\s+for|looking\s+for)\b.{0,80}"
    r"\b(?:developer|engineer|freelancer|contractor|someone)\b.{0,80}"
    r"\b(?:to\s+)?(?:build|develop|repair|fix|implement|automate)\b|"
    r"\blooking\s+for\s+(?:a\s+)?(?:developer|engineer)\s+(?:to|for)\s+"
    r"(?:build|develop|implement|automate|repair|fix)\b|"
    r"\blooking\s+for\s+(?:a\s+)?(?:python|ml|ai|quant)\s+"
    r"(?:developer|engineer)\s+for\s+(?:a\s+)?(?:trading|bot|strategy|project)\b"
    r")",
    re.IGNORECASE,
)
# disc_v5 project-scope expansions (hire-to-build / bot deliverable / integrate).
_PROJECT_SCOPE_V5_RX = re.compile(
    r"(?:"
    r"\bhire\s+(?:an?\s+)?(?:developer|engineer|freelancer|contractor)\s+to\s+"
    r"(?:build|develop|implement|automate|fix|integrate)\b|"
    r"\bseeking\s+(?:a\s+)?(?:freelancer|contractor|developer)\s+to\s+"
    r"(?:develop|build|implement|integrate)\b|"
    r"\b(?:want|need)\s+(?:a\s+)?custom\s+(?:trading\s+)?bot\b|"
    r"\b(?:build|develop)\s+(?:me\s+|us\s+)?(?:a\s+)?(?:custom\s+)?(?:trading\s+)?bot\b|"
    r"\bintegrate\s+(?:pybit|bybit|binance|okx)\b|"
    r"\bneed\s+an?\s+expert\s+to\s+integrate\b|"
    r"\b(?:quant|python|ml|ai)\s+engineer\s+for\s+(?:a\s+)?(?:short[- ]term\s+)?"
    r"(?:trading|bot|strategy|project)\b|"
    r"\bcontractor\s+for\s+(?:solana|dex|arbitrage|trading|bot)\b|"
    r"\blooking\s+to\s+pay\s+someone\s+to\s+(?:automate|build|develop|fix)\b"
    r")",
    re.IGNORECASE,
)

_PROVIDER_EXTRA_RX = re.compile(
    r"(?:"
    r"\bi\s+can\s+(?:build|develop|fix|create)\b|"
    r"\bi\s+will\s+(?:build|develop|fix|create|automate)\b|"
    r"\bwe\s+(?:develop|build|offer|provide)\b|"
    r"\bour\s+services?\b|"
    r"\blooking\s+for\s+clients?\b|"
    r"\bavailable\s+for\s+freelance\s+work\b|"
    r"\bportfolio\b.{0,40}\b(?:dm|hire|available)\b|"
    r"\bdo\s+you\s+need\s+(?:a\s+)?developer\b|"
    r"\bgig\s+of\s+the\s+day\b|"
    r"\bhire:\s*https?://|"
    r"\b(?:message|dm|pm)\s+me\b.{0,80}\b(?:bot|signal|trade|copy)\b|"
    r"\bwant\s+to\s+know\s+how\s+(?:the\s+)?bot\s+works\b|"
    r"\bdm\s+for\s+more\s+details\b|"
    r"\bfeel\s+free\s+to\s+(?:dm|message|reach\s+out)\b.{0,60}\b(?:developer|build|cbot)\b"
    r")",
    re.IGNORECASE,
)
# disc_v5-only provider guards (do not alter v4 population).
_PROVIDER_EXTRA_V5_RX = re.compile(
    r"(?:"
    r"\bi\s+can\s+offer\b|"
    r"\bhelping\s+startups\b|"
    r"\b\d+\+?\s+years?\b.{0,40}\bportfolio\b"
    r")",
    re.IGNORECASE,
)

_SEEKER_EXTRA_RX = re.compile(
    r"(?:"
    r"#(?:резюме|resume|cv|opentowork|open_to_work)\b|"
    r"\blooking\s+for\s+(?:work|a\s+job|a\s+position|opportunities)\b|"
    r"\bseeking\s+(?:a\s+)?(?:position|role|job)\b|"
    r"\bopen\s+to\s+(?:work|opportunities|new\s+roles)\b|"
    r"\bищу\s+работу\b|"
    r"\b(?:years?\s+of\s+experience|yoe)\b.{0,80}\b(?:looking|seeking|available)\b"
    r")",
    re.IGNORECASE,
)

_SUPPORT_RX = re.compile(
    r"(?:"
    r"\b(?:how\s+(?:do|can)\s+i|please\s+help|help\s+me)\b.{0,60}\b(?:api|account|withdraw|key)\b|"
    r"\b(?:почему|помогите|как\s+настроить)\b|"
    r"\bdear\b.{0,80}\b(?:support|technical\s+support)\b|"
    r"\btechnical\s+support\s+team\b"
    r")",
    re.IGNORECASE,
)
_PRODUCT_PROMO_RX = re.compile(
    r"(?:"
    r"\bstill\s+frustrated\b|"
    r"\bcapture\s+market\s+opportunities\b|"
    r"\b(?:ea|expert\s+advisor)\s+pro\b|"
    r"\bscalper\s+ea\b|"
    r"\bcreate\s+futures\s+trading\s+bots?\s+and\s+let\s+them\b|"
    r"\bif\s+you(?:'re| are)\s+looking\s+for\s+an\s+automated\s+trading\s+solution\b|"
    r"\bno\s+need\s+to\s+watch\s+the\s+market\b|"
    r"\blet\s+them\s+capture\b"
    r")",
    re.IGNORECASE,
)
_EDITORIAL_RX = re.compile(
    r"(?:"
    r"\banalytical\s+report\b|"
    r"#{2,}\s+(?:introduction|executive\s+summary)\b|"
    r"\bthis\s+report\s+(?:examines|analyzes|presents)\b"
    r")",
    re.IGNORECASE,
)


@dataclass
class DiscoveryFeatures:
    """Read-only retrieval signals; must not alter score()."""

    commercial_patterns: list[str] = field(default_factory=list)
    hiring_patterns: list[str] = field(default_factory=list)
    repair_patterns: list[str] = field(default_factory=list)
    implementation_patterns: list[str] = field(default_factory=list)
    contractor_patterns: list[str] = field(default_factory=list)
    budget_language: bool = False
    timeline_language: bool = False
    technical_project_terms: list[str] = field(default_factory=list)
    domain_categories: list[str] = field(default_factory=list)
    matched_categories: list[str] = field(default_factory=list)
    hard_exclude: bool = False
    exclude_reasons: list[str] = field(default_factory=list)
    topic_fingerprint: str = ""
    trigger_type: str | None = None
    trigger_score: float = 0.0
    eligible: bool = False
    # disc_v3 retrieval metadata (not lead score)
    buyer_direction: BuyerDirection = "UNKNOWN"
    employment_signal: bool = False
    project_scope: bool = False
    recruiter_signal: bool = False
    seeker_signal: bool = False
    provider_signal: bool = False
    aggregator_signal: bool = False
    support_signal: bool = False
    marketing_signal: bool = False
    news_signal: bool = False
    repair_signal: bool = False
    budget_signal: bool = False
    timeline_signal: bool = False
    buyer_persistence_signal: float = 0.0
    discovery_score: float = 0.0
    veto_categories: list[str] = field(default_factory=list)
    discovery_version: str = DISCOVERY_VERSION
    buyer_signal_families: list[str] = field(default_factory=list)
    project_procurement_signal: bool = False
    direct_request_signal: bool = False
    ownership_signal: bool = False
    automation_signal: bool = False
    hard_veto: bool = False
    soft_direction: bool = False
    matched_paths: list[str] = field(default_factory=list)
    true_budget_signal: bool = False
    bot_deliverable_signal: bool = False
    gig_project_carve: bool = False

    def to_signals_dict(self) -> dict[str, Any]:
        return {
            "commercial_patterns": self.commercial_patterns,
            "hiring_patterns": self.hiring_patterns,
            "repair_patterns": self.repair_patterns,
            "implementation_patterns": self.implementation_patterns,
            "contractor_patterns": self.contractor_patterns,
            "budget_language": self.budget_language,
            "timeline_language": self.timeline_language,
            "technical_project_terms": self.technical_project_terms,
            "domain_categories": self.domain_categories,
            "matched_categories": self.matched_categories,
            "hard_exclude": self.hard_exclude,
            "exclude_reasons": self.exclude_reasons,
            "topic_fingerprint": self.topic_fingerprint,
            "trigger_type": self.trigger_type,
            "trigger_score": self.trigger_score,
            "eligible": self.eligible,
            "buyer_direction": self.buyer_direction,
            "employment_signal": self.employment_signal,
            "project_scope": self.project_scope,
            "recruiter_signal": self.recruiter_signal,
            "seeker_signal": self.seeker_signal,
            "provider_signal": self.provider_signal,
            "aggregator_signal": self.aggregator_signal,
            "support_signal": self.support_signal,
            "marketing_signal": self.marketing_signal,
            "news_signal": self.news_signal,
            "repair_signal": self.repair_signal,
            "budget_signal": self.budget_signal,
            "timeline_signal": self.timeline_signal,
            "buyer_persistence_signal": self.buyer_persistence_signal,
            "discovery_score": self.discovery_score,
            "veto_categories": self.veto_categories,
            "discovery_version": self.discovery_version,
            "buyer_signal_families": self.buyer_signal_families,
            "project_procurement_signal": self.project_procurement_signal,
            "direct_request_signal": self.direct_request_signal,
            "ownership_signal": self.ownership_signal,
            "automation_signal": self.automation_signal,
            "hard_veto": self.hard_veto,
            "soft_direction": self.soft_direction,
            "matched_paths": self.matched_paths,
            "true_budget_signal": self.true_budget_signal,
            "bot_deliverable_signal": self.bot_deliverable_signal,
            "gig_project_carve": self.gig_project_carve,
        }


def topic_fingerprint_from_hits(
    *,
    categories: list[str],
    pattern_names: list[str],
) -> str:
    """Deterministic fingerprint from sorted YAML category/pattern names (no embeddings)."""
    cats_only = sorted({c.strip().lower() for c in categories if c and c.strip()})
    if not cats_only:
        return "none"
    return "|".join(cats_only)[:64]


def fingerprints_compatible(a: str, b: str) -> bool:
    """True when topic fingerprints share at least one token (or either is empty/none)."""
    if not a or not b or a == "none" or b == "none":
        return False
    sa, sb = set(a.split("|")), set(b.split("|"))
    return bool(sa & sb)


def episode_key(*, author_id: int | None, topic_fp: str, message_date, message_id: int) -> str:
    """Candidate episode key: author + topic + ISO week. Fallback msg:<id>."""
    if author_id is None:
        return f"msg:{message_id}"
    iso = message_date.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    fp = (topic_fp or "none")[:48]
    return f"a{author_id}:{fp}:{week}"


def _population_bucket(feats: DiscoveryFeatures) -> str:
    """Coarse audit bucket — not ground truth."""
    if feats.news_signal or "news_digest" in feats.veto_categories:
        return "News/editorial"
    if feats.marketing_signal or "marketing_broadcast" in feats.veto_categories:
        return "Marketing/promo"
    # disc_v6 FO carve: marketplace template matched but project RFQ recovered.
    if feats.gig_project_carve and (
        feats.eligible or feats.project_scope or feats.repair_signal
    ):
        return "Direct buyer/RFQ candidate"
    if (feats.aggregator_signal or "job_aggregator" in feats.veto_categories) and not (
        feats.gig_project_carve
    ):
        return "Corporate/job-board hiring"
    if feats.buyer_direction == "SEEKER" or feats.seeker_signal:
        return "Job seeker/resume"
    if feats.buyer_direction == "PROVIDER" or (
        feats.provider_signal and not feats.project_scope
    ):
        return "Provider/self-promotion"
    if feats.buyer_direction == "EMPLOYER" or (
        feats.employment_signal and not feats.project_scope
    ):
        return "Corporate/job-board hiring"
    if feats.buyer_direction == "RECRUITER" and not feats.project_scope:
        return "Recruiter"
    if feats.support_signal and not feats.project_scope:
        return "Support/question"
    if feats.buyer_direction == "BUYER" or (
        feats.eligible and feats.project_scope
    ):
        return "Direct buyer/RFQ candidate"
    if feats.buyer_direction == "RECRUITER" and feats.project_scope:
        return "Direct buyer/RFQ candidate"
    return "Other"


def classify_buyer_direction(
    *,
    clean: str,
    scorer: Any,
    feats: DiscoveryFeatures,
    version: str | None = None,
) -> BuyerDirection:
    """Deterministic retrieval label — not commercial truth."""
    ver = version or DISCOVERY_VERSION
    v5 = ver == DISCOVERY_VERSION_V5
    v6 = ver == DISCOVERY_VERSION_V6

    seeker_hits = scorer._pattern_hits(clean, "job_seeker")
    provider_hits = scorer._pattern_hits(clean, "service_provider")
    recruiter_hits = scorer._pattern_hits(clean, "recruiter")

    feats.seeker_signal = bool(seeker_hits) or bool(_SEEKER_EXTRA_RX.search(clean))
    feats.provider_signal = bool(provider_hits) or bool(_PROVIDER_EXTRA_RX.search(clean))
    if v5 and not feats.provider_signal:
        feats.provider_signal = bool(_PROVIDER_EXTRA_V5_RX.search(clean))
    feats.recruiter_signal = bool(recruiter_hits)
    feats.employment_signal = bool(_EMPLOYER_RX.search(clean))
    corporate_jd = bool(_CORPORATE_JD_RX.search(clean))
    corporate_jd_hard = bool(_CORPORATE_JD_HARD_RX.search(clean))

    feats.direct_request_signal = bool(_DIRECT_REQUEST_RX.search(clean))
    if v5 and not feats.direct_request_signal:
        feats.direct_request_signal = bool(_DIRECT_REQUEST_V5_RX.search(clean))

    feats.ownership_signal = bool(_OWNERSHIP_RX.search(clean))
    if v5 and not feats.ownership_signal:
        feats.ownership_signal = bool(_OWNERSHIP_V5_RX.search(clean))

    feats.automation_signal = bool(_AUTOMATION_RX.search(clean))
    feats.true_budget_signal = bool(_TRUE_BUDGET_RX.search(clean))
    feats.bot_deliverable_signal = bool(_BOT_DELIVERABLE_RX.search(clean))

    gig_marketplace = bool(_GIG_MARKETPLACE_RX.search(clean))
    feats.gig_project_carve = bool(v6 and _is_gig_project_carve(clean))
    carve = feats.gig_project_carve
    feats.project_procurement_signal = (not gig_marketplace or carve) and (
        bool(_PROJECT_PROCUREMENT_RX.search(clean))
        or (
            bool(_PROCUREMENT_RX.search(clean))
            and (feats.direct_request_signal or feats.ownership_signal)
        )
        or (
            v5
            and feats.direct_request_signal
            and feats.true_budget_signal
            and feats.bot_deliverable_signal
        )
    )
    repair_conj = bool(_REPAIR_CONJ_RX.search(clean)) and not bool(
        _REPAIR_FALSE_POSITIVE_RX.search(clean)
    )
    yaml_repair = bool(feats.repair_patterns) and (
        feats.ownership_signal or feats.direct_request_signal or repair_conj
    )

    project_scope_hit = bool(_PROJECT_SCOPE_RX.search(clean))
    if v5 and not project_scope_hit:
        project_scope_hit = bool(_PROJECT_SCOPE_V5_RX.search(clean))

    raw_project = (
        project_scope_hit
        or yaml_repair
        or bool(feats.implementation_patterns)
        or feats.direct_request_signal
        or feats.ownership_signal
        or feats.automation_signal
        or feats.project_procurement_signal
        or (repair_conj and (feats.ownership_signal or feats.direct_request_signal))
        or (
            v5
            and feats.bot_deliverable_signal
            and feats.direct_request_signal
            and (feats.true_budget_signal or feats.ownership_signal)
        )
        or carve
    )
    # Hard JD boilerplate demotes project scope; soft full-time alone does not when
    # ownership/direct/procurement evidence exists (v4 recall path).
    # disc_v6 FO carve keeps project scope despite gig marketplace template.
    if corporate_jd_hard or (gig_marketplace and not carve):
        feats.project_scope = False
    elif corporate_jd and not (
        feats.ownership_signal
        or feats.direct_request_signal
        or feats.project_procurement_signal
        or carve
    ):
        feats.project_scope = False
    else:
        feats.project_scope = bool(raw_project)

    feats.aggregator_signal = bool(
        scorer._is_job_aggregation(clean) or scorer._is_multi_job_aggregation(clean)
    ) or gig_marketplace
    feats.support_signal = bool(scorer._is_support_like(clean)) or bool(_SUPPORT_RX.search(clean))
    feats.marketing_signal = bool(scorer._is_marketing_broadcast(clean)) or bool(
        _PRODUCT_PROMO_RX.search(clean)
    )
    feats.news_signal = bool(scorer._is_news_digest(clean)) or bool(_EDITORIAL_RX.search(clean))
    feats.repair_signal = (repair_conj or yaml_repair) and (not gig_marketplace or carve)
    feats.budget_signal = feats.budget_language or bool(
        re.search(r"\b(?:budget|бюджет|quote|стоимость)\b", clean, re.I)
    )
    if v5:
        # Prefer true project-budget language over lone $-amounts / prize pools.
        feats.budget_signal = feats.budget_signal or feats.true_budget_signal
        if feats.true_budget_signal:
            feats.budget_language = True
    if feats.budget_signal:
        feats.budget_language = True
    feats.timeline_signal = feats.timeline_language or bool(
        re.search(r"\b(?:deadline|timeline|сроки|asap|urgent)\b", clean, re.I)
    )

    families: list[str] = []
    if feats.direct_request_signal:
        families.append("DIRECT_PROJECT_REQUEST")
    if feats.ownership_signal:
        families.append("SPECIFIC_DELIVERABLE_REQUEST")
    if feats.repair_signal:
        families.append("BOT_REPAIR_REQUEST")
    if feats.automation_signal or feats.implementation_patterns:
        families.append("STRATEGY_AUTOMATION_REQUEST")
    if feats.project_procurement_signal:
        families.append("CUSTOM_DEVELOPMENT_REQUEST")
    if feats.budget_signal or feats.true_budget_signal:
        families.append("BUDGETED_REQUEST")
    if feats.timeline_signal:
        families.append("TIMELINED_REQUEST")
    if feats.bot_deliverable_signal and feats.direct_request_signal:
        families.append("BOT_PURCHASE_REQUEST")
    if carve:
        families.append("FO_GIG_PROJECT_CARVE")
    if _DOMAIN_LOOSE_RX.search(clean):
        if re.search(r"\b(?:bybit|binance|okx|pybit|exchange\s*api)\b", clean, re.I):
            families.append("EXCHANGE_INTEGRATION_REQUEST")
        if re.search(r"\b(?:trading\s+bot|grid\s+bot)\b", clean, re.I):
            families.append("BOT_PURCHASE_REQUEST")
        if re.search(r"\barbitrage\b", clean, re.I):
            families.append("ARBITRAGE_PROJECT_REQUEST")
        if re.search(r"\bcopy\s+trad", clean, re.I):
            families.append("COPY_TRADING_PROJECT_REQUEST")
        if re.search(r"\bmarket\s+mak", clean, re.I):
            families.append("MARKET_MAKING_PROJECT_REQUEST")
        if re.search(r"\b(?:solana|dex)\b", clean, re.I):
            families.append("SOLANA_DEX_PROJECT_REQUEST")
        if re.search(r"\bquant\b", clean, re.I):
            families.append("QUANT_CONTRACT_REQUEST")
        if re.search(r"\b(?:ml|machine\s+learning|ai\s+engineer)\b", clean, re.I):
            families.append("ML_AI_CONTRACT_REQUEST")
        if re.search(r"\btrading\s+system\b", clean, re.I):
            families.append("TRADING_SYSTEM_REQUEST")
    feats.buyer_signal_families = list(dict.fromkeys(families))

    # Soft direction: project procurement may coexist with recruiter-like wording.
    strong_buyer_evidence = bool(
        feats.ownership_signal
        or feats.repair_signal
        or feats.automation_signal
        or (feats.direct_request_signal and (feats.budget_signal or feats.project_scope))
        or feats.project_procurement_signal
        or carve
        or (
            v5
            and feats.direct_request_signal
            and feats.bot_deliverable_signal
            and (feats.true_budget_signal or feats.project_scope)
        )
    ) and (not feats.aggregator_signal or carve)
    feats.soft_direction = strong_buyer_evidence and (
        feats.recruiter_signal or feats.employment_signal
    ) and not corporate_jd_hard and (not feats.aggregator_signal or carve)
    # disc_v5: vacancy hashtags are hard employment — never soft-override (job-board noise).
    if v5 and re.search(r"#(?:вакансия|vacancy|hiring|fulltime)\b", clean, re.I):
        feats.soft_direction = False
        if not feats.project_procurement_signal:
            feats.employment_signal = True

    # Priority: hard non-buyer roles first.
    if feats.aggregator_signal and not carve:
        return "RECRUITER" if feats.recruiter_signal else "EMPLOYER"
    # Vacancy hashtags without project/procurement evidence → employer (v5 job-board guard).
    if v5 and re.search(r"#(?:вакансия|vacancy|hiring|fulltime)\b", clean, re.I):
        if not (
            feats.project_procurement_signal
            or feats.ownership_signal
            or feats.repair_signal
            or (feats.direct_request_signal and feats.bot_deliverable_signal)
        ):
            return "EMPLOYER"
    if feats.seeker_signal and not strong_buyer_evidence:
        return "SEEKER"
    # disc_v5 provider spam (portfolio / "i can offer") wins over weak repair/commercial hits.
    if v5 and bool(_PROVIDER_EXTRA_V5_RX.search(clean)):
        if not (
            feats.ownership_signal
            and feats.direct_request_signal
            and (feats.true_budget_signal or feats.budget_signal)
        ):
            return "PROVIDER"
    if feats.provider_signal and (
        bool(_PROVIDER_EXTRA_RX.search(clean))
        or "gig of the day" in clean.lower()
        or "hire me" in clean.lower()
    ):
        return "PROVIDER"
    if feats.provider_signal and not strong_buyer_evidence:
        return "PROVIDER"
    if corporate_jd_hard and not strong_buyer_evidence:
        return "EMPLOYER"
    if (feats.employment_signal or corporate_jd) and not strong_buyer_evidence and not feats.project_scope:
        return "EMPLOYER"
    if strong_buyer_evidence and feats.recruiter_signal:
        return "RECRUITER"
    if feats.recruiter_signal and feats.project_scope:
        return "RECRUITER"
    if feats.recruiter_signal and not feats.project_scope and not strong_buyer_evidence:
        return "RECRUITER"
    if strong_buyer_evidence or feats.project_scope:
        return "BUYER"
    if (
        (feats.hiring_patterns or feats.commercial_patterns or feats.repair_patterns)
        and (feats.domain_categories or feats.technical_project_terms or _DOMAIN_LOOSE_RX.search(clean))
        and not corporate_jd_hard
    ):
        return "BUYER"
    if feats.hiring_patterns and feats.budget_language and not corporate_jd_hard:
        return "BUYER"
    return "UNKNOWN"


def _apply_vetoes(feats: DiscoveryFeatures, *, soft: bool = False) -> None:
    """Apply non-buyer vetoes. soft=True (disc_v4) allows project-procurement overrides."""
    vetoes: list[str] = []
    if feats.hard_exclude:
        # disc_v6 FO carve: keep marketplace template visible but do not hard-veto
        # solely on job_aggregator when the carve applies.
        if feats.gig_project_carve:
            for reason in feats.exclude_reasons:
                if reason != "job_aggregator":
                    vetoes.append(reason)
        else:
            vetoes.extend(feats.exclude_reasons)
    if feats.aggregator_signal and not feats.gig_project_carve:
        vetoes.append("job_aggregator")
    # Clear resume/seeker: hard veto unless soft mode has ownership+project evidence.
    if feats.seeker_signal:
        seeker_override = soft and (
            (feats.ownership_signal and feats.project_scope)
            or (feats.project_procurement_signal and feats.ownership_signal)
        )
        if not seeker_override:
            vetoes.append("job_seeker")
    if feats.provider_signal and feats.buyer_direction == "PROVIDER":
        vetoes.append("service_provider")
    if feats.news_signal:
        vetoes.append("news_digest")
    if feats.marketing_signal:
        vetoes.append("marketing_broadcast")
    if feats.support_signal and not (
        (feats.repair_signal and feats.ownership_signal)
        or (feats.direct_request_signal and (feats.ownership_signal or feats.budget_signal))
    ):
        vetoes.append("support_question")

    # Employment / recruiter: hard vs soft
    if soft:
        # Hard corporate JD without project evidence
        if feats.buyer_direction == "EMPLOYER" and not feats.soft_direction:
            vetoes.append("corporate_employment")
        elif feats.employment_signal and not feats.project_scope and not feats.soft_direction:
            vetoes.append("corporate_employment")
        if (
            feats.buyer_direction == "RECRUITER"
            and not feats.project_scope
            and not feats.project_procurement_signal
            and not feats.soft_direction
            and not feats.gig_project_carve
        ):
            vetoes.append("generic_recruiter")
    else:
        if feats.employment_signal and not feats.project_scope:
            vetoes.append("corporate_employment")
        if feats.buyer_direction == "RECRUITER" and not feats.project_scope:
            vetoes.append("generic_recruiter")

    feats.veto_categories = list(dict.fromkeys(vetoes))
    feats.hard_veto = bool(feats.veto_categories)


def _eligible_v2(
    feats: DiscoveryFeatures,
    *,
    purchase: list,
    repair: list,
    custom: list,
    hire: list,
    impl: list,
    buyer_intent: list,
    contract: list,
    domain: list,
    tech: list,
    tech_hits: list,
    has_domain_loose: bool,
) -> None:
    """Legacy disc_v2 eligibility (kept for A/B population comparison)."""
    if feats.hard_exclude:
        feats.eligible = False
        return

    explicit_commercial = bool(purchase or repair or custom or hire or impl)
    soft_commercial = bool(buyer_intent or contract)
    has_domain_cat = bool(domain)
    has_tech = bool(tech or tech_hits)

    trigger = None
    score = 0.0
    if explicit_commercial and has_domain_cat:
        trigger = "commercial_domain"
        score = 3.0
    elif explicit_commercial and has_domain_loose and has_tech:
        trigger = "commercial_domain_tech"
        score = 2.8
    elif (hire or contract) and (has_domain_cat or has_tech):
        trigger = "client_hiring"
        score = 2.5
    elif (hire or contract) and has_domain_loose and feats.budget_language:
        trigger = "hire_budget_domain"
        score = 2.2
    elif explicit_commercial and has_tech:
        trigger = "commercial_technical"
        score = 2.0
    elif repair and (has_tech or has_domain_cat):
        trigger = "repair_technical"
        score = 2.0
    elif impl and (has_tech or has_domain_cat):
        trigger = "implementation_technical"
        score = 2.0
    elif soft_commercial and has_domain_cat and feats.budget_language:
        trigger = "buyer_budget_domain"
        score = 1.5
    elif explicit_commercial and feats.budget_language and (has_domain_cat or has_tech):
        trigger = "commercial_budget"
        score = 1.5

    feats.trigger_type = trigger
    feats.trigger_score = score
    feats.eligible = trigger is not None and score > 0
    feats.discovery_score = score


def _eligible_v3(
    feats: DiscoveryFeatures,
    *,
    purchase: list,
    repair: list,
    custom: list,
    hire: list,
    impl: list,
    buyer_intent: list,
    contract: list,
    domain: list,
    tech: list,
    tech_hits: list,
    has_domain_loose: bool,
) -> None:
    """Buyer-side discovery: keep RFQs, veto corporate jobs / seekers / providers."""
    _apply_vetoes(feats, soft=False)
    if feats.veto_categories:
        feats.eligible = False
        feats.trigger_type = None
        feats.trigger_score = 0.0
        feats.discovery_score = 0.0
        return

    explicit_commercial = bool(purchase or repair or custom or hire or impl)
    soft_commercial = bool(buyer_intent or contract)
    has_domain_cat = bool(domain)
    has_tech = bool(tech or tech_hits)
    # Project-scoped RFQs with a financial-domain keyword (Binance/Bybit/…) count
    # even when YAML category domain lists miss the hit.
    has_domain = has_domain_cat or (has_domain_loose and (has_tech or feats.project_scope))

    # Must have commercial/project signal + domain/tech relevance.
    commercial_ok = explicit_commercial or (
        soft_commercial and (feats.project_scope or feats.budget_language)
    )
    domain_ok = has_domain or (feats.project_scope and has_tech) or (
        feats.project_scope and has_domain_loose
    )

    if not commercial_ok or not domain_ok:
        feats.eligible = False
        feats.trigger_type = None
        feats.trigger_score = 0.0
        feats.discovery_score = 0.0
        return

    # Direction gate: allow BUYER, project RECRUITER, and strong UNKNOWN with project scope.
    if feats.buyer_direction in ("SEEKER", "PROVIDER", "EMPLOYER"):
        feats.eligible = False
        feats.veto_categories.append(f"direction_{feats.buyer_direction.lower()}")
        feats.veto_categories = list(dict.fromkeys(feats.veto_categories))
        return
    if feats.buyer_direction == "UNKNOWN" and not (
        feats.project_scope or feats.repair_signal or feats.budget_language
    ):
        feats.eligible = False
        feats.veto_categories.append("direction_unknown_weak")
        return

    trigger = None
    score = 0.0
    if feats.repair_signal and (has_domain or has_tech):
        trigger = "buyer_repair"
        score = 3.2
    elif feats.implementation_patterns and (has_domain or has_tech):
        trigger = "buyer_implementation"
        score = 3.1
    elif feats.project_scope and (explicit_commercial or soft_commercial) and has_domain:
        trigger = "buyer_project_scope"
        score = 3.0
    elif explicit_commercial and has_domain_cat:
        trigger = "buyer_commercial_domain"
        score = 2.8
    elif (hire or contract) and feats.project_scope and (has_domain or has_tech):
        trigger = "buyer_hire_project"
        score = 2.7
    elif feats.buyer_direction == "RECRUITER" and feats.project_scope and has_domain:
        trigger = "project_recruiter"
        score = 2.4
    elif soft_commercial and has_domain_cat and feats.budget_language:
        trigger = "buyer_budget_domain"
        score = 2.2
    elif explicit_commercial and has_tech and feats.project_scope:
        trigger = "buyer_commercial_tech"
        score = 2.0
    else:
        trigger = "buyer_weak"
        score = 1.2

    # Ranking-only discovery_score (not lead score).
    dscore = score
    if feats.budget_signal:
        dscore += 0.3
    if feats.timeline_signal:
        dscore += 0.15
    if feats.project_scope:
        dscore += 0.25
    if feats.repair_signal:
        dscore += 0.2
    dscore += min(0.5, feats.buyer_persistence_signal)
    feats.trigger_type = trigger
    feats.trigger_score = score
    feats.discovery_score = round(dscore, 3)
    feats.eligible = True


def _eligible_v4(
    feats: DiscoveryFeatures,
    *,
    purchase: list,
    repair: list,
    custom: list,
    hire: list,
    impl: list,
    buyer_intent: list,
    contract: list,
    domain: list,
    tech: list,
    tech_hits: list,
    has_domain_loose: bool,
    clean: str,
) -> None:
    """Expand buyer recall via conjunctions; keep hard non-buyer vetoes."""
    _apply_vetoes(feats, soft=True)
    if feats.veto_categories:
        feats.eligible = False
        feats.trigger_type = None
        feats.trigger_score = 0.0
        feats.discovery_score = 0.0
        return

    has_domain_cat = bool(domain)
    has_tech = bool(tech or tech_hits)
    has_domain_kw = bool(_DOMAIN_LOOSE_RX.search(clean)) or has_domain_loose
    has_domain = has_domain_cat or has_domain_kw or (feats.project_scope and has_tech)

    explicit_commercial = bool(purchase or repair or custom or hire or impl)
    soft_commercial = bool(buyer_intent or contract)
    direct = feats.direct_request_signal or soft_commercial or explicit_commercial

    # Paths A–E (any one suffices).
    path_a = direct and has_domain and (
        feats.project_scope
        or feats.ownership_signal
        or feats.direct_request_signal
        or (explicit_commercial and feats.budget_signal)
    )
    # Path B: do NOT use bare ownership — "my bot is not working" sets both
    # repair_signal and ownership_signal, so supportish texts enqueue as
    # v6_repair_domain (Evidence 107/108 seed 8635958). Require RFQ / hire /
    # budget / paid-fix / FO carve (not ownership alone).
    path_b = (
        feats.repair_signal
        and (has_domain or has_tech)
        and (
            feats.direct_request_signal
            or bool(hire)
            or feats.budget_signal
            or feats.true_budget_signal
            or feats.gig_project_carve
        )
    )
    # Path C: do NOT use bare project_scope — classify_buyer_direction sets
    # project_scope whenever automation_signal fires, so automate+domain alone
    # would always pass (EA marketing: "helps automate my trading"). Require a
    # non-circular buyer conjunct (RFQ / hire / budget / ownership).
    path_c = (feats.automation_signal or bool(impl)) and has_domain and (
        feats.direct_request_signal
        or feats.ownership_signal
        or feats.budget_signal
        or bool(hire)
    )
    path_d = (
        feats.project_procurement_signal
        and (feats.project_scope or feats.ownership_signal or feats.direct_request_signal)
        and has_domain
        and (not feats.aggregator_signal or feats.gig_project_carve)
    )
    path_e = (
        (feats.budget_signal or feats.timeline_signal)
        and has_domain
        and (feats.direct_request_signal or feats.ownership_signal or feats.gig_project_carve)
        and (not feats.aggregator_signal or feats.gig_project_carve)
    )

    if not (path_a or path_b or path_c or path_d or path_e):
        feats.eligible = False
        feats.trigger_type = None
        feats.trigger_score = 0.0
        feats.discovery_score = 0.0
        return

    # Soft direction: SEEKER/PROVIDER/EMPLOYER still blocked unless soft_direction override.
    if feats.buyer_direction in ("SEEKER", "PROVIDER") and not feats.soft_direction:
        feats.eligible = False
        feats.veto_categories.append(f"direction_{feats.buyer_direction.lower()}")
        feats.veto_categories = list(dict.fromkeys(feats.veto_categories))
        feats.hard_veto = True
        return
    if feats.buyer_direction == "EMPLOYER" and not feats.soft_direction:
        feats.eligible = False
        feats.veto_categories.append("direction_employer")
        feats.veto_categories = list(dict.fromkeys(feats.veto_categories))
        feats.hard_veto = True
        return

    if path_b:
        trigger, score = "v4_repair_domain", 3.3
    elif path_c:
        trigger, score = "v4_automation_domain", 3.2
    elif path_d:
        trigger, score = "v4_project_procurement", 3.1
    elif path_e:
        trigger, score = "v4_budget_deliverable", 3.0
    elif path_a:
        trigger, score = "v4_direct_project_domain", 2.9
    else:
        trigger, score = "v4_buyer_weak", 1.5

    paths_hit = []
    if path_a:
        paths_hit.append("A")
    if path_b:
        paths_hit.append("B")
    if path_c:
        paths_hit.append("C")
    if path_d:
        paths_hit.append("D")
    if path_e:
        paths_hit.append("E")
    feats.matched_paths = paths_hit

    dscore = score
    if feats.budget_signal:
        dscore += 0.3
    if feats.timeline_signal:
        dscore += 0.15
    if feats.ownership_signal:
        dscore += 0.25
    if feats.repair_signal:
        dscore += 0.2
    if feats.project_procurement_signal:
        dscore += 0.15
    dscore += 0.05 * min(6, len(feats.buyer_signal_families))
    dscore += min(0.5, feats.buyer_persistence_signal)
    feats.trigger_type = trigger
    feats.trigger_score = score
    feats.discovery_score = round(dscore, 3)
    feats.eligible = True


def _eligible_v5(
    feats: DiscoveryFeatures,
    *,
    purchase: list,
    repair: list,
    custom: list,
    hire: list,
    impl: list,
    buyer_intent: list,
    contract: list,
    domain: list,
    tech: list,
    tech_hits: list,
    has_domain_loose: bool,
    clean: str,
) -> None:
    """disc_v5: v4 paths A–E plus bot-deliverable path F; true-budget path E.

    Does not weaken LaborX / gig marketplace / job_aggregator vetoes (D later).
    """
    _apply_vetoes(feats, soft=True)
    if feats.veto_categories:
        feats.eligible = False
        feats.trigger_type = None
        feats.trigger_score = 0.0
        feats.discovery_score = 0.0
        feats.matched_paths = []
        return

    has_domain_cat = bool(domain)
    has_tech = bool(tech or tech_hits)
    has_domain_kw = bool(_DOMAIN_LOOSE_RX.search(clean)) or has_domain_loose
    has_domain = has_domain_cat or has_domain_kw or (feats.project_scope and has_tech)

    explicit_commercial = bool(purchase or repair or custom or hire or impl)
    soft_commercial = bool(buyer_intent or contract)
    direct = feats.direct_request_signal or soft_commercial or explicit_commercial
    # Path E: keep v4 budget_signal recall; true_budget is additive, not restrictive.
    budget_ok = bool(feats.budget_signal or feats.true_budget_signal)

    path_a = direct and has_domain and (
        feats.project_scope
        or feats.ownership_signal
        or feats.direct_request_signal
        or (explicit_commercial and budget_ok)
    )
    # Same non-circular path_b conjunct as v4/v6 (Evidence 109): drop bare
    # ownership so supportish "my bot is not working" cannot unlock alone.
    path_b = (
        feats.repair_signal
        and (has_domain or has_tech)
        and (
            feats.direct_request_signal
            or bool(hire)
            or budget_ok
            or feats.true_budget_signal
            or feats.gig_project_carve
        )
    )
    # Same non-circular path_c conjunct as v4/v6 (Evidence 108).
    path_c = (feats.automation_signal or bool(impl)) and has_domain and (
        feats.direct_request_signal
        or feats.ownership_signal
        or budget_ok
        or bool(hire)
    )
    path_d = (
        feats.project_procurement_signal
        and (feats.project_scope or feats.ownership_signal or feats.direct_request_signal)
        and has_domain
        and not feats.aggregator_signal
    )
    path_e = (
        (budget_ok or feats.timeline_signal)
        and has_domain
        and (feats.direct_request_signal or feats.ownership_signal)
        and not feats.aggregator_signal
    )
    # Path F: bot/system deliverable request + domain + (true budget OR ownership OR project).
    path_f = (
        feats.bot_deliverable_signal
        and feats.direct_request_signal
        and has_domain
        and (budget_ok or feats.ownership_signal or feats.project_scope)
        and not feats.aggregator_signal
    )

    paths_hit: list[str] = []
    if path_a:
        paths_hit.append("A")
    if path_b:
        paths_hit.append("B")
    if path_c:
        paths_hit.append("C")
    if path_d:
        paths_hit.append("D")
    if path_e:
        paths_hit.append("E")
    if path_f:
        paths_hit.append("F")
    feats.matched_paths = paths_hit

    if not paths_hit:
        feats.eligible = False
        feats.trigger_type = None
        feats.trigger_score = 0.0
        feats.discovery_score = 0.0
        return

    # Soft direction: SEEKER/PROVIDER/EMPLOYER still blocked unless soft_direction override.
    if feats.buyer_direction in ("SEEKER", "PROVIDER") and not feats.soft_direction:
        feats.eligible = False
        feats.veto_categories.append(f"direction_{feats.buyer_direction.lower()}")
        feats.veto_categories = list(dict.fromkeys(feats.veto_categories))
        feats.hard_veto = True
        feats.matched_paths = []
        return
    if feats.buyer_direction == "EMPLOYER" and not feats.soft_direction:
        feats.eligible = False
        feats.veto_categories.append("direction_employer")
        feats.veto_categories = list(dict.fromkeys(feats.veto_categories))
        feats.hard_veto = True
        feats.matched_paths = []
        return

    if path_b:
        trigger, score = "v5_repair_domain", 3.3
    elif path_c:
        trigger, score = "v5_automation_domain", 3.2
    elif path_f:
        trigger, score = "v5_bot_deliverable", 3.15
    elif path_d:
        trigger, score = "v5_project_procurement", 3.1
    elif path_e:
        trigger, score = "v5_budget_deliverable", 3.0
    elif path_a:
        trigger, score = "v5_direct_project_domain", 2.9
    else:
        trigger, score = "v5_buyer_weak", 1.5

    dscore = score
    if budget_ok:
        dscore += 0.3
    if feats.timeline_signal:
        dscore += 0.15
    if feats.ownership_signal:
        dscore += 0.25
    if feats.repair_signal:
        dscore += 0.2
    if feats.project_procurement_signal:
        dscore += 0.15
    if feats.bot_deliverable_signal:
        dscore += 0.1
    dscore += 0.05 * min(6, len(feats.buyer_signal_families))
    dscore += min(0.5, feats.buyer_persistence_signal)
    feats.trigger_type = trigger
    feats.trigger_score = score
    feats.discovery_score = round(dscore, 3)
    feats.eligible = True


def evaluate_discovery(
    scorer: Any,
    text: str,
    *,
    version: str | None = None,
    buyer_persistence_signal: float = 0.0,
) -> DiscoveryFeatures:
    """Compute discovery eligibility using existing LeadScorer pattern helpers only."""
    ver = version or DISCOVERY_VERSION
    clean = " ".join((text or "").split())
    feats = DiscoveryFeatures(discovery_version=ver)
    feats.buyer_persistence_signal = float(buyer_persistence_signal or 0.0)

    _, categories, _ = scorer._matches(clean)
    feats.matched_categories = list(categories)

    for cat in _HARD_EXCLUDE_CATS:
        if scorer._pattern_hits(clean, cat) or cat in categories:
            feats.hard_exclude = True
            feats.exclude_reasons.append(cat)

    if scorer._is_marketing_broadcast(clean):
        feats.hard_exclude = True
        feats.exclude_reasons.append("marketing_broadcast")
    if scorer._is_news_digest(clean):
        feats.hard_exclude = True
        feats.exclude_reasons.append("news_digest")
    if scorer._is_job_aggregation(clean) or scorer._is_multi_job_aggregation(clean):
        feats.hard_exclude = True
        feats.exclude_reasons.append("job_aggregator")

    purchase = scorer._pattern_hits(clean, "commercial_purchase")
    repair = scorer._pattern_hits(clean, "commercial_repair")
    custom = scorer._pattern_hits(clean, "commercial_customization")
    hire = scorer._pattern_hits(clean, "commercial_hire")
    impl = scorer._pattern_hits(clean, "commercial_implementation")
    buyer_intent = scorer._pattern_hits(clean, "buyer_intent")
    contract = scorer._pattern_hits(clean, "contract_engagement")
    feats.commercial_patterns = list(dict.fromkeys(purchase + custom + buyer_intent))
    feats.repair_patterns = list(repair)
    feats.hiring_patterns = list(dict.fromkeys(hire + contract))
    feats.implementation_patterns = list(impl)
    feats.contractor_patterns = list(dict.fromkeys(hire + contract))

    domain = [c for c in categories if c in _DOMAIN_CATS]
    tech = [c for c in categories if c in _TECH_CATS]
    feats.domain_categories = domain
    tech_hits = scorer._pattern_hits(clean, "technical_problem")
    feats.technical_project_terms = list(dict.fromkeys(tech + tech_hits))[:12]

    budget_amt, _ = scorer._extract_budget(clean)
    feats.budget_language = budget_amt is not None or bool(
        scorer._pattern_hits(clean, "budget_context")
    )
    feats.timeline_language = bool(scorer._pattern_hits(clean, "urgency"))

    pattern_names: list[str] = []
    for cat in _COMMERCIAL_PATTERN_CATS:
        pattern_names.extend(scorer._pattern_hits(clean, cat))
    feats.topic_fingerprint = topic_fingerprint_from_hits(
        categories=[
            c
            for c in categories
            if c in (_TECH_CATS | _DOMAIN_CATS | frozenset(_COMMERCIAL_PATTERN_CATS))
        ],
        pattern_names=pattern_names,
    )

    has_domain_loose = scorer._has_target_financial_domain(clean)
    feats.buyer_direction = classify_buyer_direction(
        clean=clean, scorer=scorer, feats=feats, version=ver
    )

    common = dict(
        purchase=purchase,
        repair=repair,
        custom=custom,
        hire=hire,
        impl=impl,
        buyer_intent=buyer_intent,
        contract=contract,
        domain=domain,
        tech=tech,
        tech_hits=tech_hits,
        has_domain_loose=has_domain_loose,
    )

    if ver == DISCOVERY_VERSION_V2:
        # v2: ignore new vetoes for eligibility (legacy path)
        if feats.hard_exclude:
            feats.eligible = False
            return feats
        _eligible_v2(feats, **common)
        return feats

    if ver == DISCOVERY_VERSION_V3:
        if feats.hard_exclude and not feats.project_scope:
            _apply_vetoes(feats, soft=False)
            feats.eligible = False
            return feats
        _eligible_v3(feats, **common)
        return feats

    if ver == DISCOVERY_VERSION_V5:
        if feats.hard_exclude and not (
            feats.project_scope or feats.soft_direction or feats.repair_signal
        ):
            _apply_vetoes(feats, soft=True)
            feats.eligible = False
            return feats
        _eligible_v5(feats, clean=clean, **common)
        return feats

    if ver == DISCOVERY_VERSION_V6:
        # disc_v6 = disc_v4 paths + FO gig project carve (D).
        if feats.hard_exclude and not (
            feats.project_scope
            or feats.soft_direction
            or feats.repair_signal
            or feats.gig_project_carve
        ):
            _apply_vetoes(feats, soft=True)
            feats.eligible = False
            return feats
        _eligible_v4(feats, clean=clean, **common)
        if feats.eligible and feats.trigger_type and feats.trigger_type.startswith("v4_"):
            feats.trigger_type = "v6_" + feats.trigger_type[3:]
        if feats.eligible and feats.gig_project_carve and "CARVE" not in feats.matched_paths:
            feats.matched_paths = list(feats.matched_paths) + ["CARVE"]
        return feats

    # Explicit disc_v4 / unknown versions: v4 paths (no FO carve).
    if feats.hard_exclude and not (
        feats.project_scope or feats.soft_direction or feats.repair_signal
    ):
        _apply_vetoes(feats, soft=True)
        feats.eligible = False
        return feats
    _eligible_v4(feats, clean=clean, **common)
    return feats


def signals_json(feats: DiscoveryFeatures) -> str:
    return json.dumps(feats.to_signals_dict(), ensure_ascii=False, sort_keys=True)


def context_hash_parts(
    *,
    seed_id: int,
    context_version: str,
    members: list[dict[str, Any]],
) -> str:
    """Deterministic context hash from seed, version, message ids, text hashes, relations."""
    rows = []
    for m in sorted(members, key=lambda x: int(x["message_id"])):
        rows.append(
            f"{m['message_id']}:{m.get('relation')}:{m.get('text_sha256') or ''}"
        )
    blob = f"{seed_id}|{context_version}|" + "|".join(rows)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Re-export for audits
population_bucket = _population_bucket
