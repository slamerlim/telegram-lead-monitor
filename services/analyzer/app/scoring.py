import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ScoreResult:
    score: float
    tier: str
    matched_keywords: list[str]
    matched_categories: list[str]
    reasons: list[str]
    semantic_score: float | None = None


class LeadScorer:
    def __init__(self, path: str, high: float = 75, medium: float = 50) -> None:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.thresholds = data.get("thresholds", {})
        self.weights: dict[str, float] = data.get("categories", {})
        self.terms: dict[str, list[str]] = data.get("terms", {})
        self.high = float(self.thresholds.get("high", high))
        self.medium = float(self.thresholds.get("medium", medium))
        self.compiled = {
            category: [(term, re.compile(re.escape(term), re.IGNORECASE | re.UNICODE)) for term in terms]
            for category, terms in self.terms.items()
        }
        self.negative_categories = {"negative_promotion"}

    def score(self, text: str) -> ScoreResult:
        clean = " ".join(text.split())
        matched_keywords: list[str] = []
        categories: list[str] = []
        reasons: list[str] = []
        score = 0.0
        for category, pairs in self.compiled.items():
            matches = [term for term, rx in pairs if rx.search(clean)]
            if not matches:
                continue
            matched_keywords.extend(matches)
            categories.append(category)
            weight = float(self.weights.get(category, 0))
            if category in self.negative_categories:
                score += weight
                reasons.append(f"negative signal: {', '.join(matches[:3])}")
            else:
                score += weight
                reasons.append(f"{category}: {', '.join(matches[:4])}")
        # Small bonuses for multiple independent intent dimensions.
        positives = len([c for c in categories if c not in self.negative_categories])
        if positives >= 3:
            score += 8
            reasons.append("multiple independent lead signals")
        if "customer_intent" in categories and "trading_bot" in categories:
            score += 10
            reasons.append("explicit developer intent + trading-bot context")
        if "customer_intent" in categories and any(c in categories for c in {"exchange_api", "execution", "arbitrage", "solana_dex", "copy_trading"}):
            score += 8
            reasons.append("developer intent paired with technical trading requirement")
        if clean.count("http") > 2 and "customer_intent" not in categories:
            score -= 8
            reasons.append("likely promotional/link-heavy message")
        score = max(0.0, min(100.0, score))
        tier = "HIGH" if score >= self.high else "MEDIUM" if score >= self.medium else "LOW"
        # De-duplicate while preserving order.
        matched_keywords = list(dict.fromkeys(matched_keywords))
        categories = list(dict.fromkeys(categories))
        reasons = list(dict.fromkeys(reasons))
        return ScoreResult(score, tier, matched_keywords, categories, reasons)
