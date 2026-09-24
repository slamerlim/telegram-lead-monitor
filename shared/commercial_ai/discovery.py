"""HIGH-recall commercial discovery from LOW-tier messages (YAML patterns only).

disc_v3 adds buyer_direction + non-buyer vetoes. Does NOT alter LeadScorer.score().
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

DISCOVERY_VERSION = "disc_v3"
DISCOVERY_VERSION_V2 = "disc_v2"

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
# Job-description salary / board boilerplate — overrides weak "to build" JD language.
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
    # Require first-person RFQ, not "Company is looking for an engineer to build…"
    r"\b(?:i|we)\s+(?:need|are\s+looking\s+for|looking\s+for)\b.{0,80}"
    r"\b(?:developer|engineer|freelancer|contractor|someone)\b.{0,80}"
    r"\b(?:to\s+)?(?:build|develop|repair|fix|implement|automate)\b"
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
    r"\bhire:\s*https?://"
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
    if feats.aggregator_signal or "job_aggregator" in feats.veto_categories:
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
) -> BuyerDirection:
    """Deterministic retrieval label — not commercial truth."""
    seeker_hits = scorer._pattern_hits(clean, "job_seeker")
    provider_hits = scorer._pattern_hits(clean, "service_provider")
    recruiter_hits = scorer._pattern_hits(clean, "recruiter")

    feats.seeker_signal = bool(seeker_hits) or bool(_SEEKER_EXTRA_RX.search(clean))
    feats.provider_signal = bool(provider_hits) or bool(_PROVIDER_EXTRA_RX.search(clean))
    feats.recruiter_signal = bool(recruiter_hits)
    feats.employment_signal = bool(_EMPLOYER_RX.search(clean))
    corporate_jd = bool(_CORPORATE_JD_RX.search(clean))
    raw_project = bool(_PROJECT_SCOPE_RX.search(clean)) or bool(
        feats.repair_patterns or feats.implementation_patterns
    )
    # JD boilerplate ("is hiring … to build …") is not project RFQ scope.
    feats.project_scope = bool(raw_project and not corporate_jd)
    feats.aggregator_signal = bool(
        scorer._is_job_aggregation(clean) or scorer._is_multi_job_aggregation(clean)
    )
    feats.support_signal = bool(scorer._is_support_like(clean)) or bool(_SUPPORT_RX.search(clean))
    feats.marketing_signal = bool(scorer._is_marketing_broadcast(clean)) or bool(
        _PRODUCT_PROMO_RX.search(clean)
    )
    feats.news_signal = bool(scorer._is_news_digest(clean)) or bool(_EDITORIAL_RX.search(clean))
    feats.repair_signal = bool(feats.repair_patterns)
    feats.budget_signal = feats.budget_language
    feats.timeline_signal = feats.timeline_language

    # Priority: non-buyer roles first when clear.
    if feats.aggregator_signal:
        return "RECRUITER" if feats.recruiter_signal else "EMPLOYER"
    if feats.seeker_signal and not feats.project_scope:
        return "SEEKER"
    # Strong provider self-promo always wins over weak RFQ-like wording.
    if feats.provider_signal and (
        bool(_PROVIDER_EXTRA_RX.search(clean))
        or "gig of the day" in clean.lower()
        or "hire me" in clean.lower()
    ):
        return "PROVIDER"
    if feats.provider_signal and not (
        feats.project_scope and (feats.hiring_patterns or feats.commercial_patterns)
    ):
        return "PROVIDER"
    if (feats.employment_signal or corporate_jd) and not feats.project_scope:
        return "EMPLOYER"
    if feats.recruiter_signal and feats.project_scope:
        return "RECRUITER"  # project recruitment — may stay eligible
    if feats.recruiter_signal and not feats.project_scope:
        return "RECRUITER"
    if feats.project_scope or (
        (feats.hiring_patterns or feats.commercial_patterns or feats.repair_patterns)
        and (feats.domain_categories or feats.technical_project_terms)
        and not feats.employment_signal
        and not corporate_jd
    ):
        return "BUYER"
    if feats.hiring_patterns and feats.budget_language and not feats.employment_signal and not corporate_jd:
        return "BUYER"
    return "UNKNOWN"


def _apply_vetoes(feats: DiscoveryFeatures) -> None:
    vetoes: list[str] = []
    if feats.hard_exclude:
        vetoes.extend(feats.exclude_reasons)
    if feats.aggregator_signal:
        vetoes.append("job_aggregator")
    if feats.seeker_signal and not feats.project_scope:
        vetoes.append("job_seeker")
    if feats.provider_signal and feats.buyer_direction == "PROVIDER":
        vetoes.append("service_provider")
    if feats.employment_signal and not feats.project_scope:
        vetoes.append("corporate_employment")
    if feats.news_signal:
        vetoes.append("news_digest")
    if feats.marketing_signal:
        vetoes.append("marketing_broadcast")
    if feats.support_signal and not (
        feats.repair_signal or feats.project_scope or feats.commercial_patterns
    ):
        vetoes.append("support_question")
    # Generic recruiter without project scope
    if feats.buyer_direction == "RECRUITER" and not feats.project_scope:
        vetoes.append("generic_recruiter")
    feats.veto_categories = list(dict.fromkeys(vetoes))


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
    _apply_vetoes(feats)
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
    feats.buyer_direction = classify_buyer_direction(clean=clean, scorer=scorer, feats=feats)

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

    # disc_v3 (default)
    if feats.hard_exclude and not feats.project_scope:
        # Still record direction for audits
        _apply_vetoes(feats)
        feats.eligible = False
        return feats
    _eligible_v3(feats, **common)
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
