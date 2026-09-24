"""Commercial context retrieval V3 — broad same-author recall + deterministic ranking.

Does NOT call AI. Does NOT change LeadScorer.score().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.commercial_ai.discovery import (
    context_hash_parts,
    evaluate_discovery,
    fingerprints_compatible,
)
from shared.commercial_ai.store import text_sha256
from shared.models import LabelReviewSample, Message

CONTEXT_VERSION_V3 = "ctx_v3"
STAGE1_MAX_CANDIDATES = 100
STAGE3_MAX_SELECTED = 20

RELATION_SEED = "SEED"
RELATION_REPLY_PARENT = "REPLY_PARENT"
RELATION_REPLY_CHILD = "REPLY_CHILD"
RELATION_SAME_AUTHOR_24H = "SAME_AUTHOR_24H"
RELATION_SAME_AUTHOR_7D = "SAME_AUTHOR_7D"
RELATION_SAME_AUTHOR_30D = "SAME_AUTHOR_30D"

# Exclusion taxonomy (must match actual filter reasons).
EXCL_TOPIC_MISMATCH = "TOPIC_MISMATCH"
EXCL_OUTSIDE_TIME_WINDOW = "OUTSIDE_TIME_WINDOW"
EXCL_COMMERCIAL_IRRELEVANCE = "COMMERCIAL_IRRELEVANCE"
EXCL_CAP_EXCEEDED = "CAP_EXCEEDED"
EXCL_DUPLICATE = "DUPLICATE"
EXCL_MISSING_AUTHOR = "MISSING_AUTHOR"
EXCL_BLIND_SAMPLE = "BLIND_SAMPLE"
EXCL_DOMAIN_INCOMPATIBLE = "DOMAIN_INCOMPATIBLE"
EXCL_OTHER = "OTHER"

_DOMAIN_RX = re.compile(
    r"\b(?:bybit|binance|okx|pybit|exchange\s*api|/v5/|futures|trading\s*bot|"
    r"arbitrage|market\s*mak(?:e|ing)|copy\s*trad(?:e|ing)|solana|dex|"
    r"websocket|python|execution|grid\s*bot|dca)\b",
    re.IGNORECASE,
)
_BUYER_RX = re.compile(
    r"\b(?:looking\s+for|need(?:ed)?|hiring|seeking|require|budget|project|"
    r"developer|engineer|repair|build|implement|commission|freelance|"
    r"ищу|нужен|разработ|починить)\b",
    re.IGNORECASE,
)
_TECH_RX = re.compile(
    r"\b(?:api|sdk|websocket|python|rust|go\b|typescript|mt4|mt5|"
    r"smart\s*contract|solidity|fastapi|order(?:s)?|latency|backtest)\b",
    re.IGNORECASE,
)
_ENTITY_RX = re.compile(
    r"\b(?:bybit|binance|okx|bitget|mexc|coinex|solana|ethereum|eth\b|"
    r"pepe|polymarket|arbitrum|base\b)\b",
    re.IGNORECASE,
)
# Domains that must not merge into trading/exchange commercial episodes.
_ALT_PROJECT_RX = re.compile(
    r"\b(?:unity|unreal|solidity|nft|mobile\s*game|react\s*native|"
    r"ios\s*app|android\s*app|wordpress|shopify|laravel)\b",
    re.IGNORECASE,
)
# Employer / aggregator job posts — not buyer commercial intent.
_EMPLOYER_JOB_RX = re.compile(
    r"(?:\bis hiring\b)|(?:\bis looking for (?:a|an)\b)|"
    r"(?:#вакансия\b)|(?:#hiring\b)|(?:job\s*board)|(?:we're hiring\b)|(?:we are hiring\b)",
    re.IGNORECASE,
)


def is_employer_job_post(text: str) -> bool:
    return bool(_EMPLOYER_JOB_RX.search(text or ""))


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _tokens(text: str, rx: re.Pattern[str]) -> set[str]:
    return {m.group(0).lower() for m in rx.finditer(text or "")}


@dataclass
class RelevanceBreakdown:
    domain_overlap: bool = False
    commercial_overlap: bool = False
    technical_overlap: bool = False
    buyer_action_overlap: bool = False
    entity_overlap: bool = False
    fingerprint_compatible: bool = False
    reply_relation: bool = False
    temporal_proximity: float = 0.0
    temporal_band: str = "none"  # 24h | 7d | 30d
    relevance_score: float = 0.0
    selection_reason: str = ""
    incompatible_project: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain_overlap": self.domain_overlap,
            "commercial_overlap": self.commercial_overlap,
            "technical_overlap": self.technical_overlap,
            "buyer_action_overlap": self.buyer_action_overlap,
            "entity_overlap": self.entity_overlap,
            "fingerprint_compatible": self.fingerprint_compatible,
            "reply_relation": self.reply_relation,
            "temporal_proximity": round(self.temporal_proximity, 4),
            "temporal_band": self.temporal_band,
            "relevance_score": round(self.relevance_score, 4),
            "selection_reason": self.selection_reason,
            "incompatible_project": self.incompatible_project,
        }


@dataclass
class RankedCandidate:
    message: Message
    relation: str
    breakdown: RelevanceBreakdown
    exclusion_reason: str | None = None  # set when filtered out of final selection


@dataclass
class ContextMemberV3:
    message_id: int
    telegram_message_id: int
    author_id: int | None
    text: str
    message_date: datetime
    relation: str
    selection_reason: str
    text_sha256: str
    is_seed_author: bool
    relevance: dict[str, Any] = field(default_factory=dict)
    selection_rank: int | None = None


@dataclass
class CommercialContextV3:
    seed_message_id: int
    seed_author_id: int | None
    community_id: int
    context_version: str
    context_hash: str
    members: list[ContextMemberV3]
    n_stage1_candidates: int = 0
    n_excluded: int = 0
    n_excluded_blind: int = 0
    exclusion_counts: dict[str, int] = field(default_factory=dict)
    include_related: bool = True
    topic_fingerprint: str = ""

    def allowed_message_ids(self) -> set[int]:
        return {m.message_id for m in self.members}

    def seed_author_message_ids(self) -> set[int]:
        return {m.message_id for m in self.members if m.is_seed_author}

    def to_members_json(self) -> list[dict[str, Any]]:
        return [
            {
                "message_id": m.message_id,
                "telegram_message_id": m.telegram_message_id,
                "author_id": m.author_id,
                "relation": m.relation,
                "selection_reason": m.selection_reason,
                "text_sha256": m.text_sha256,
                "is_seed_author": m.is_seed_author,
                "message_date": m.message_date.isoformat(),
                "relevance": m.relevance,
                "selection_rank": m.selection_rank,
            }
            for m in self.members
        ]


def has_episode_affinity(
    bd: RelevanceBreakdown,
    cand_feats: Any,
    *,
    seed_text: str = "",
    cand_text: str = "",
) -> bool:
    """Prevent same-author cross-project / job-board bundling without a content bridge."""
    if bd.reply_relation:
        return True
    if bd.incompatible_project:
        return False
    # Never pull employer job-board posts into commercial buyer context.
    if is_employer_job_post(cand_text):
        return False
    # If the seed itself is an employer post, do not invent a fake episode from
    # other same-author posts unless reply-linked (handled above).
    if is_employer_job_post(seed_text):
        return False
    if bd.fingerprint_compatible or bd.domain_overlap:
        return True
    # Shared entity alone (e.g. ETH/MEXC) is too weak without commercial/tech bridge.
    if bd.entity_overlap and (
        bd.commercial_overlap or bd.technical_overlap or bd.buyer_action_overlap
    ):
        return True
    if bd.technical_overlap and (bd.commercial_overlap or bd.buyer_action_overlap):
        return True
    if getattr(cand_feats, "budget_language", False) or getattr(cand_feats, "timeline_language", False):
        return True
    if getattr(cand_feats, "eligible", False) and (
        bd.buyer_action_overlap or bd.technical_overlap or bd.commercial_overlap or bd.domain_overlap
    ):
        return True
    return False


def score_relevance(
    *,
    seed_text: str,
    cand_text: str,
    seed_fp: str,
    cand_fp: str,
    seed_feats: Any,
    cand_feats: Any,
    seed_dt: datetime,
    cand_dt: datetime,
    is_reply_rel: bool,
) -> RelevanceBreakdown:
    b = RelevanceBreakdown()
    seed_dom = _tokens(seed_text, _DOMAIN_RX)
    cand_dom = _tokens(cand_text, _DOMAIN_RX)
    seed_buy = _tokens(seed_text, _BUYER_RX)
    cand_buy = _tokens(cand_text, _BUYER_RX)
    seed_tech = _tokens(seed_text, _TECH_RX)
    cand_tech = _tokens(cand_text, _TECH_RX)
    seed_ent = _tokens(seed_text, _ENTITY_RX)
    cand_ent = _tokens(cand_text, _ENTITY_RX)

    b.domain_overlap = bool(seed_dom & cand_dom) or bool(
        set(seed_feats.domain_categories or []) & set(cand_feats.domain_categories or [])
    )
    # Prefer structured commercial categories over bare hiring-regex hits (job boards).
    seed_comm = bool(
        seed_feats.commercial_patterns
        or seed_feats.repair_patterns
        or seed_feats.implementation_patterns
        or (seed_feats.eligible and seed_feats.hiring_patterns)
    )
    cand_comm = bool(
        cand_feats.commercial_patterns
        or cand_feats.repair_patterns
        or cand_feats.implementation_patterns
        or (cand_feats.eligible and cand_feats.hiring_patterns)
    )
    b.commercial_overlap = seed_comm and cand_comm
    b.buyer_action_overlap = bool(seed_buy & cand_buy)
    b.technical_overlap = bool(seed_tech & cand_tech) or bool(
        set(seed_feats.technical_project_terms or []) & set(cand_feats.technical_project_terms or [])
    )
    b.entity_overlap = bool(seed_ent & cand_ent)
    b.fingerprint_compatible = bool(
        seed_fp
        and cand_fp
        and seed_fp != "none"
        and cand_fp != "none"
        and fingerprints_compatible(seed_fp, cand_fp)
    )
    b.reply_relation = is_reply_rel

    seed_has_domain = bool(seed_dom or seed_feats.domain_categories)
    cand_alt = _tokens(cand_text, _ALT_PROJECT_RX)
    # Cross-project: seed is trading/exchange-scoped and candidate is a different
    # software domain (Unity/Solidity/…) with no shared domain/entity/fingerprint.
    if seed_has_domain and cand_alt and not (
        b.domain_overlap or b.entity_overlap or b.fingerprint_compatible or b.technical_overlap
    ):
        b.incompatible_project = True
    # Distinct exchange/protocol entities with no shared commercial+tech bridge.
    elif seed_ent and cand_ent and not (seed_ent & cand_ent) and not b.fingerprint_compatible:
        if not (b.domain_overlap or b.technical_overlap):
            b.incompatible_project = True
    # Commercial-only overlap without any domain/tech/entity/fp affinity while seed
    # is domain-specific → treat as weak cross-project (still scored, heavily downranked).
    elif (
        seed_has_domain
        and (b.commercial_overlap or b.buyer_action_overlap)
        and not (
            b.domain_overlap
            or b.entity_overlap
            or b.fingerprint_compatible
            or b.technical_overlap
            or b.reply_relation
        )
        and not cand_feats.budget_language
        and not cand_feats.timeline_language
    ):
        b.incompatible_project = True

    delta_h = abs((_aware(cand_dt) - _aware(seed_dt)).total_seconds()) / 3600.0
    if delta_h <= 24:
        b.temporal_band = "24h"
        b.temporal_proximity = 1.0
    elif delta_h <= 24 * 7:
        b.temporal_band = "7d"
        b.temporal_proximity = 0.7
    elif delta_h <= 24 * 30:
        b.temporal_band = "30d"
        b.temporal_proximity = 0.4
    else:
        b.temporal_band = "older"
        b.temporal_proximity = 0.1

    score = 0.0
    if b.reply_relation:
        score += 0.45
    if b.fingerprint_compatible:
        score += 0.25
    if b.commercial_overlap:
        score += 0.20
    if b.domain_overlap:
        score += 0.15
    if b.buyer_action_overlap:
        score += 0.12
    if b.technical_overlap:
        score += 0.12
    if b.entity_overlap:
        score += 0.10
    score += 0.20 * b.temporal_proximity
    if cand_feats.eligible:
        score += 0.08
    if b.incompatible_project:
        score *= 0.15  # heavily downrank, do not hard-drop until selection

    b.relevance_score = min(1.0, score)
    reasons = []
    if b.reply_relation:
        reasons.append("reply")
    if b.commercial_overlap:
        reasons.append("commercial")
    if b.domain_overlap:
        reasons.append("domain")
    if b.technical_overlap:
        reasons.append("technical")
    if b.fingerprint_compatible:
        reasons.append("fingerprint")
    if b.buyer_action_overlap:
        reasons.append("buyer_action")
    reasons.append(f"same_author_{b.temporal_band}")
    b.selection_reason = "+".join(reasons) if reasons else "same_author"
    return b


def _relation_for_band(band: str, is_reply: bool, reply_kind: str | None) -> str:
    if is_reply and reply_kind:
        return reply_kind
    if band == "24h":
        return RELATION_SAME_AUTHOR_24H
    if band == "7d":
        return RELATION_SAME_AUTHOR_7D
    return RELATION_SAME_AUTHOR_30D


async def forensic_v2_exclusions(
    session: AsyncSession,
    seed_message_id: int,
    *,
    scorer: Any,
) -> dict[str, Any]:
    """Classify why V2 would discard each same-author history message (read-only)."""
    seed = await session.get(Message, seed_message_id)
    if not seed:
        return {"seed_message_id": seed_message_id, "error": "missing"}

    seed_feats = evaluate_discovery(scorer, seed.text or "")
    seed_fp = seed_feats.topic_fingerprint
    seed_dt = _aware(seed.message_date)
    counts: dict[str, int] = {}
    excluded_samples: list[dict[str, Any]] = []
    case = "A"  # no history

    if seed.author_id is None:
        return {
            "seed_message_id": seed_message_id,
            "case": "A",
            "available_history": 0,
            "exclusion_counts": {EXCL_MISSING_AUTHOR: 1},
            "plausibly_relevant_excluded": 0,
        }

    hist = (
        await session.scalars(
            select(Message)
            .where(
                Message.author_id == seed.author_id,
                Message.community_id == seed.community_id,
                Message.id != seed.id,
                Message.message_date >= seed_dt - timedelta(days=30),
                Message.message_date <= seed_dt + timedelta(hours=1),
            )
            .order_by(Message.message_date.desc())
            .limit(STAGE1_MAX_CANDIDATES)
        )
    ).all()

    if not hist:
        return {
            "seed_message_id": seed_message_id,
            "case": "A",
            "available_history": 0,
            "exclusion_counts": {},
            "plausibly_relevant_excluded": 0,
        }

    case = "C"  # assume unrelated until proven B
    plausibly_relevant = 0
    selected_v2 = 0
    win_24h = seed_dt - timedelta(hours=24)

    for msg in hist:
        reason = None
        feats = evaluate_discovery(scorer, msg.text or "")
        other_fp = feats.topic_fingerprint
        in_24h = _aware(msg.message_date) >= win_24h

        # Simulate V2 filters
        if in_24h:
            if seed_fp and other_fp and not fingerprints_compatible(seed_fp, other_fp):
                reason = EXCL_TOPIC_MISMATCH
            else:
                selected_v2 += 1
                continue
        else:
            # 30d history path
            if not feats.eligible and not fingerprints_compatible(seed_fp, other_fp):
                reason = EXCL_COMMERCIAL_IRRELEVANCE
            elif seed_fp and other_fp and not fingerprints_compatible(seed_fp, other_fp):
                reason = EXCL_TOPIC_MISMATCH
            else:
                selected_v2 += 1
                continue

        counts[reason] = counts.get(reason, 0) + 1

        # Plausible relevance (engineering judgment, not truth): commercial or domain overlap.
        bd = score_relevance(
            seed_text=seed.text or "",
            cand_text=msg.text or "",
            seed_fp=seed_fp,
            cand_fp=other_fp,
            seed_feats=seed_feats,
            cand_feats=feats,
            seed_dt=seed_dt,
            cand_dt=_aware(msg.message_date),
            is_reply_rel=False,
        )
        if (
            has_episode_affinity(
                bd, feats, seed_text=seed.text or "", cand_text=msg.text or ""
            )
            and bd.relevance_score >= 0.40
            and not bd.incompatible_project
        ):
            plausibly_relevant += 1
            case = "B"
            if len(excluded_samples) < 5:
                excluded_samples.append(
                    {
                        "message_id": msg.id,
                        "reason": reason,
                        "relevance_score": bd.relevance_score,
                        "preview": (msg.text or "")[:120].replace("\n", " "),
                    }
                )
        elif case != "B":
            case = "C"

    return {
        "seed_message_id": seed_message_id,
        "case": case,
        "available_history": len(hist),
        "v2_would_select_support": selected_v2,
        "exclusion_counts": counts,
        "plausibly_relevant_excluded": plausibly_relevant,
        "excluded_samples": excluded_samples,
        "seed_fp": seed_fp,
    }


async def build_commercial_context_v3(
    session: AsyncSession,
    seed_message_id: int,
    *,
    include_related: bool = True,
    scorer: Any | None = None,
    max_messages: int = STAGE3_MAX_SELECTED,
    max_chars: int = 12000,
    stage1_max: int = STAGE1_MAX_CANDIDATES,
    min_relevance: float = 0.40,
    context_version: str = CONTEXT_VERSION_V3,
) -> CommercialContextV3:
    """Two-stage retrieval: broad same-author pool → deterministic rank → cap."""
    seed = await session.get(Message, seed_message_id)
    if not seed:
        raise ValueError(f"seed message {seed_message_id} not found")
    if scorer is None:
        raise ValueError("scorer required for V3 retrieval")

    seed_feats = evaluate_discovery(scorer, seed.text or "")
    seed_fp = seed_feats.topic_fingerprint
    seed_dt = _aware(seed.message_date)
    exclusion_counts: dict[str, int] = {}
    n_blind = 0

    members: list[ContextMemberV3] = [
        ContextMemberV3(
            message_id=seed.id,
            telegram_message_id=seed.telegram_message_id,
            author_id=seed.author_id,
            text=seed.text or "",
            message_date=seed_dt,
            relation=RELATION_SEED,
            selection_reason="seed_message",
            text_sha256=text_sha256(seed.text or ""),
            is_seed_author=True,
            relevance={"relevance_score": 1.0, "selection_reason": "seed"},
            selection_rank=0,
        )
    ]

    if not include_related:
        chash = context_hash_parts(
            seed_id=seed.id,
            context_version=context_version,
            members=[{"message_id": seed.id, "relation": RELATION_SEED, "text_sha256": members[0].text_sha256}],
        )
        return CommercialContextV3(
            seed_message_id=seed.id,
            seed_author_id=seed.author_id,
            community_id=seed.community_id,
            context_version=context_version,
            context_hash=chash,
            members=members,
            include_related=False,
            topic_fingerprint=seed_fp,
        )

    ranked: list[RankedCandidate] = []
    cand_feats_by_id: dict[int, Any] = {}
    reply_parent_id = None
    if seed.reply_to_telegram_message_id is not None:
        parent = await session.scalar(
            select(Message).where(
                Message.community_id == seed.community_id,
                Message.telegram_message_id == seed.reply_to_telegram_message_id,
            )
        )
        if parent and parent.id != seed.id:
            reply_parent_id = parent.id
            feats = evaluate_discovery(scorer, parent.text or "")
            cand_feats_by_id[parent.id] = feats
            bd = score_relevance(
                seed_text=seed.text or "",
                cand_text=parent.text or "",
                seed_fp=seed_fp,
                cand_fp=feats.topic_fingerprint,
                seed_feats=seed_feats,
                cand_feats=feats,
                seed_dt=seed_dt,
                cand_dt=_aware(parent.message_date),
                is_reply_rel=True,
            )
            ranked.append(
                RankedCandidate(parent, RELATION_REPLY_PARENT, bd)
            )

    children = (
        await session.scalars(
            select(Message)
            .where(
                Message.community_id == seed.community_id,
                Message.reply_to_telegram_message_id == seed.telegram_message_id,
            )
            .order_by(Message.message_date.asc())
            .limit(12)
        )
    ).all()
    for ch in children:
        if ch.id == seed.id:
            continue
        feats = evaluate_discovery(scorer, ch.text or "")
        cand_feats_by_id[ch.id] = feats
        bd = score_relevance(
            seed_text=seed.text or "",
            cand_text=ch.text or "",
            seed_fp=seed_fp,
            cand_fp=feats.topic_fingerprint,
            seed_feats=seed_feats,
            cand_feats=feats,
            seed_dt=seed_dt,
            cand_dt=_aware(ch.message_date),
            is_reply_rel=True,
        )
        ranked.append(RankedCandidate(ch, RELATION_REPLY_CHILD, bd))

    stage1_n = 0
    if seed.author_id is not None:
        # STAGE 1 — high-recall same-author pool (capped).
        pool = (
            await session.scalars(
                select(Message)
                .where(
                    Message.author_id == seed.author_id,
                    Message.community_id == seed.community_id,
                    Message.id != seed.id,
                    Message.message_date >= seed_dt - timedelta(days=30),
                    Message.message_date <= seed_dt + timedelta(hours=1),
                )
                .order_by(Message.message_date.desc())
                .limit(stage1_max)
            )
        ).all()
        stage1_n = len(pool)
        seen = {r.message.id for r in ranked} | {seed.id}
        for msg in pool:
            if msg.id in seen:
                exclusion_counts[EXCL_DUPLICATE] = exclusion_counts.get(EXCL_DUPLICATE, 0) + 1
                continue
            seen.add(msg.id)
            feats = evaluate_discovery(scorer, msg.text or "")
            cand_feats_by_id[msg.id] = feats
            bd = score_relevance(
                seed_text=seed.text or "",
                cand_text=msg.text or "",
                seed_fp=seed_fp,
                cand_fp=feats.topic_fingerprint,
                seed_feats=seed_feats,
                cand_feats=feats,
                seed_dt=seed_dt,
                cand_dt=_aware(msg.message_date),
                is_reply_rel=False,
            )
            rel = _relation_for_band(bd.temporal_band, False, None)
            ranked.append(RankedCandidate(msg, rel, bd))

    # STAGE 2 — rank; drop hard-incompatible projects below threshold.
    ranked.sort(key=lambda r: (-r.breakdown.relevance_score, r.message.message_date))

    # Blind exclusion
    support_ids = [r.message.id for r in ranked]
    blind_ids: set[int] = set()
    if support_ids:
        blind_ids = set(
            (
                await session.scalars(
                    select(LabelReviewSample.message_id).where(
                        LabelReviewSample.message_id.in_(support_ids)
                    )
                )
            ).all()
        )

    # STAGE 3 — select
    selected: list[ContextMemberV3] = list(members)
    chars = len(seed.text or "")
    seen_text_hashes = {members[0].text_sha256}
    rank_i = 0
    for cand in ranked:
        if cand.message.id in blind_ids:
            n_blind += 1
            exclusion_counts[EXCL_BLIND_SAMPLE] = exclusion_counts.get(EXCL_BLIND_SAMPLE, 0) + 1
            continue
        feats = cand_feats_by_id.get(cand.message.id)
        if feats is not None and getattr(feats, "hard_exclude", False):
            exclusion_counts[EXCL_COMMERCIAL_IRRELEVANCE] = (
                exclusion_counts.get(EXCL_COMMERCIAL_IRRELEVANCE, 0) + 1
            )
            continue
        if cand.breakdown.incompatible_project and cand.breakdown.relevance_score < 0.55:
            exclusion_counts[EXCL_DOMAIN_INCOMPATIBLE] = (
                exclusion_counts.get(EXCL_DOMAIN_INCOMPATIBLE, 0) + 1
            )
            continue
        if feats is not None and not has_episode_affinity(
            cand.breakdown,
            feats,
            seed_text=seed.text or "",
            cand_text=cand.message.text or "",
        ):
            exclusion_counts[EXCL_DOMAIN_INCOMPATIBLE] = (
                exclusion_counts.get(EXCL_DOMAIN_INCOMPATIBLE, 0) + 1
            )
            continue
        if not cand.breakdown.reply_relation and cand.breakdown.relevance_score < min_relevance:
            exclusion_counts[EXCL_COMMERCIAL_IRRELEVANCE] = (
                exclusion_counts.get(EXCL_COMMERCIAL_IRRELEVANCE, 0) + 1
            )
            continue
        th = text_sha256(cand.message.text or "")
        if th in seen_text_hashes:
            exclusion_counts[EXCL_DUPLICATE] = exclusion_counts.get(EXCL_DUPLICATE, 0) + 1
            continue
        if len(selected) >= max_messages:
            exclusion_counts[EXCL_CAP_EXCEEDED] = exclusion_counts.get(EXCL_CAP_EXCEEDED, 0) + 1
            continue
        add_len = len(cand.message.text or "")
        if chars + add_len > max_chars:
            exclusion_counts[EXCL_CAP_EXCEEDED] = exclusion_counts.get(EXCL_CAP_EXCEEDED, 0) + 1
            continue
        rank_i += 1
        seen_text_hashes.add(th)
        selected.append(
            ContextMemberV3(
                message_id=cand.message.id,
                telegram_message_id=cand.message.telegram_message_id,
                author_id=cand.message.author_id,
                text=cand.message.text or "",
                message_date=_aware(cand.message.message_date),
                relation=cand.relation,
                selection_reason=cand.breakdown.selection_reason,
                text_sha256=th,
                is_seed_author=(
                    seed.author_id is not None and cand.message.author_id == seed.author_id
                ),
                relevance=cand.breakdown.to_dict(),
                selection_rank=rank_i,
            )
        )
        chars += add_len

    selected.sort(key=lambda m: m.message_date)
    hash_members = [
        {
            "message_id": m.message_id,
            "relation": m.relation,
            "text_sha256": m.text_sha256,
        }
        for m in selected
    ]
    chash = context_hash_parts(
        seed_id=seed.id,
        context_version=context_version,
        members=hash_members,
    )
    return CommercialContextV3(
        seed_message_id=seed.id,
        seed_author_id=seed.author_id,
        community_id=seed.community_id,
        context_version=context_version,
        context_hash=chash,
        members=selected,
        n_stage1_candidates=stage1_n,
        n_excluded=sum(exclusion_counts.values()),
        n_excluded_blind=n_blind,
        exclusion_counts=exclusion_counts,
        include_related=True,
        topic_fingerprint=seed_fp,
    )

