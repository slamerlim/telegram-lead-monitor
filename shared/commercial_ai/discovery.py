"""HIGH-recall commercial discovery from LOW-tier messages (YAML patterns only)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

DISCOVERY_VERSION = "disc_v1"

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
        }


def topic_fingerprint_from_hits(
    *,
    categories: list[str],
    pattern_names: list[str],
) -> str:
    """Deterministic fingerprint from sorted YAML category/pattern names (no embeddings)."""
    parts = sorted({c.strip().lower() for c in categories if c and c.strip()})
    # Keep a few distinctive pattern tokens (regex source truncated) for disambiguation.
    for p in sorted({x.strip().lower()[:40] for x in pattern_names if x and x.strip()})[:6]:
        # Prefer human-readable category tokens already in parts; skip raw regex noise.
        if p.isidentifier() or "|" not in p and len(p) < 24 and p.isalnum():
            parts.append(p)
    # Prefer category-only fingerprints for stability.
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


def evaluate_discovery(scorer: Any, text: str) -> DiscoveryFeatures:
    """Compute discovery eligibility using existing LeadScorer pattern helpers only."""
    clean = " ".join((text or "").split())
    feats = DiscoveryFeatures()
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
    feats.commercial_patterns = list(dict.fromkeys(purchase + custom))
    feats.repair_patterns = list(repair)
    feats.hiring_patterns = list(hire)
    feats.implementation_patterns = list(impl)
    feats.contractor_patterns = list(scorer._pattern_hits(clean, "commercial_hire"))

    domain = [c for c in categories if c in _DOMAIN_CATS]
    tech = [c for c in categories if c in _TECH_CATS]
    feats.domain_categories = domain
    tech_hits = scorer._pattern_hits(clean, "technical_problem")
    feats.technical_project_terms = list(dict.fromkeys(tech + tech_hits))[:12]

    budget_amt, _ = scorer._extract_budget(clean)
    feats.budget_language = budget_amt is not None or bool(scorer._pattern_hits(clean, "urgency"))
    feats.timeline_language = bool(scorer._pattern_hits(clean, "urgency"))

    pattern_names: list[str] = []
    for cat in _COMMERCIAL_PATTERN_CATS:
        pattern_names.extend(scorer._pattern_hits(clean, cat))
    feats.topic_fingerprint = topic_fingerprint_from_hits(
        categories=[c for c in categories if c in (_TECH_CATS | _DOMAIN_CATS | frozenset(_COMMERCIAL_PATTERN_CATS))],
        pattern_names=pattern_names,
    )

    if feats.hard_exclude:
        feats.eligible = False
        return feats

    commercial_any = bool(purchase or repair or custom or hire or impl)
    has_domain = bool(domain) or scorer._has_target_financial_domain(clean)
    has_tech = bool(tech or tech_hits)

    trigger = None
    score = 0.0
    if commercial_any and has_domain:
        trigger = "commercial_domain"
        score = 3.0
    elif hire and (has_domain or has_tech):
        trigger = "client_hiring"
        score = 2.5
    elif commercial_any and has_tech:
        trigger = "commercial_technical"
        score = 2.0
    elif repair and (has_tech or has_domain):
        trigger = "repair_technical"
        score = 2.0
    elif impl and (has_tech or has_domain):
        trigger = "implementation_technical"
        score = 2.0

    feats.trigger_type = trigger
    feats.trigger_score = score
    feats.eligible = trigger is not None and score > 0
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
