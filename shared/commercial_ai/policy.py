"""Deterministic commercial AI decision policy (NOT independent validation consensus)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

POLICY_VERSION = "commercial_v1"

COMMERCIAL_LEAD_TYPES = frozenset(
    {
        "BOT_PURCHASE",
        "BOT_REPAIR",
        "BOT_CUSTOMIZATION",
        "STRATEGY_IMPLEMENTATION",
        "TRADING_SYSTEM_CONTRACT",
        "QUANT_ENGINEERING_CONTRACT",
        "ML_AI_ENGINEERING_CONTRACT",
        "ARBITRAGE_PROJECT",
        "COPY_TRADING_PROJECT",
        "MARKET_MAKING_PROJECT",
        "SOLANA_DEX_BOT_PROJECT",
    }
)

_HARD_VETO_FP = frozenset(
    {
        "JOB_VACANCY",
        "JOB_SEEKER",
        "SERVICE_AD",
        "MARKETING_BROADCAST",
        "SUPPORT_REQUEST",
        "NEWS_DIGEST",
        "OFF_DOMAIN",
        "DUPLICATE",
    }
)

DECISION_CONFIRMED = "AI_CONFIRMED"
DECISION_CANDIDATE = "AI_CANDIDATE"
DECISION_UNCERTAIN = "AI_UNCERTAIN"
DECISION_REJECTED = "AI_REJECTED"
DECISION_ERROR = "ERROR"


@dataclass(frozen=True)
class OpinionView:
    mode: str
    status: str
    commercial: bool
    lead_type: str | None
    matches_objectives: bool
    hard_veto: bool
    uncertain: bool
    confidence: float
    synthetic: bool
    model_family: str


@dataclass(frozen=True)
class CommercialDecision:
    decision: str
    reason_code: str
    n_confirm: int
    n_reject: int
    n_uncertain: int
    lead_type: str | None
    rank_score: float
    model_family_count: int


def _as_bool_true(v: Any) -> bool:
    return v is True


def opinion_from_payload(
    *,
    mode: str,
    status: str,
    payload: dict[str, Any] | None,
    synthetic: bool,
    model_family: str,
    confidence_floor: float = 0.75,
) -> OpinionView:
    p = payload or {}
    if status != "ok" or not payload:
        return OpinionView(
            mode=mode,
            status=status,
            commercial=False,
            lead_type=None,
            matches_objectives=False,
            hard_veto=False,
            uncertain=True,
            confidence=0.0,
            synthetic=synthetic,
            model_family=model_family,
        )
    conf = float(p.get("confidence") or 0.0)
    uncertain = bool(p.get("uncertain"))
    hard_veto = _as_bool_true(p.get("hard_veto"))
    matches = _as_bool_true(p.get("matches_objectives"))
    commercial = _as_bool_true(p.get("is_commercial_opportunity")) and matches and not uncertain
    if conf < confidence_floor:
        commercial = False
    lt = p.get("lead_type")
    if lt is not None:
        lt = str(lt).upper()
        if lt not in COMMERCIAL_LEAD_TYPES:
            lt = None
            commercial = False
    # Mode B may encode FP instead of is_commercial_opportunity
    if mode.upper() == "B":
        if _as_bool_true(p.get("is_false_positive")):
            commercial = False
            fp = str(p.get("fp_class") or "").upper()
            if fp in _HARD_VETO_FP and conf >= 0.75:
                hard_veto = True
        elif matches and not uncertain and conf >= confidence_floor:
            commercial = True
    return OpinionView(
        mode=mode,
        status=status,
        commercial=commercial,
        lead_type=lt,
        matches_objectives=matches,
        hard_veto=hard_veto,
        uncertain=uncertain or status != "ok",
        confidence=conf,
        synthetic=synthetic,
        model_family=model_family,
    )


def decide_commercial(
    opinions: list[OpinionView],
    *,
    scorer_score: float = 0.0,
    auto_promote: bool = True,
) -> CommercialDecision:
    """Apply commercial_v1 majority policy. Never invents RESPONSE/WON."""
    ok = [o for o in opinions if o.status == "ok" and not o.synthetic]
    families = {o.model_family for o in ok if o.model_family}
    if len(ok) < 2:
        return CommercialDecision(
            decision=DECISION_UNCERTAIN,
            reason_code="insufficient_agents",
            n_confirm=0,
            n_reject=0,
            n_uncertain=len(opinions),
            lead_type=None,
            rank_score=0.0,
            model_family_count=len(families),
        )

    confirms = [o for o in ok if o.commercial and not o.hard_veto]
    rejects = [o for o in ok if (not o.commercial and not o.uncertain) or o.hard_veto]
    uncertain = [o for o in ok if o.uncertain and not o.hard_veto]
    n_c, n_r, n_u = len(confirms), len(rejects), len(uncertain)

    if any(o.hard_veto for o in ok):
        return CommercialDecision(
            decision=DECISION_REJECTED,
            reason_code="hard_veto",
            n_confirm=n_c,
            n_reject=n_r,
            n_uncertain=n_u,
            lead_type=None,
            rank_score=0.0,
            model_family_count=len(families),
        )

    if n_r >= 2 and n_c == 0:
        return CommercialDecision(
            decision=DECISION_REJECTED,
            reason_code="majority_reject",
            n_confirm=n_c,
            n_reject=n_r,
            n_uncertain=n_u,
            lead_type=None,
            rank_score=0.0,
            model_family_count=len(families),
        )

    if n_c >= 2:
        types = [o.lead_type for o in confirms if o.lead_type]
        lead_type = types[0] if types and all(t == types[0] for t in types) else (
            types[0] if types else None
        )
        if types and len(set(types)) > 1:
            return CommercialDecision(
                decision=DECISION_CANDIDATE,
                reason_code="lead_type_conflict",
                n_confirm=n_c,
                n_reject=n_r,
                n_uncertain=n_u,
                lead_type=None,
                rank_score=_rank(confirms, scorer_score) * 0.5,
                model_family_count=len(families),
            )
        if not any(o.matches_objectives for o in confirms):
            return CommercialDecision(
                decision=DECISION_CANDIDATE,
                reason_code="objectives_unmatched",
                n_confirm=n_c,
                n_reject=n_r,
                n_uncertain=n_u,
                lead_type=lead_type,
                rank_score=_rank(confirms, scorer_score) * 0.6,
                model_family_count=len(families),
            )
        decision = DECISION_CONFIRMED if auto_promote else "SHADOW_CONFIRMED"
        return CommercialDecision(
            decision=decision,
            reason_code="majority_confirm",
            n_confirm=n_c,
            n_reject=n_r,
            n_uncertain=n_u,
            lead_type=lead_type,
            rank_score=_rank(confirms, scorer_score),
            model_family_count=len(families),
        )

    if n_c == 1:
        return CommercialDecision(
            decision=DECISION_CANDIDATE,
            reason_code="single_confirm",
            n_confirm=n_c,
            n_reject=n_r,
            n_uncertain=n_u,
            lead_type=confirms[0].lead_type if confirms else None,
            rank_score=_rank(confirms, scorer_score) * 0.4,
            model_family_count=len(families),
        )

    return CommercialDecision(
        decision=DECISION_UNCERTAIN,
        reason_code="no_majority",
        n_confirm=n_c,
        n_reject=n_r,
        n_uncertain=n_u,
        lead_type=None,
        rank_score=0.0,
        model_family_count=len(families),
    )


def _rank(confirms: list[OpinionView], scorer_score: float) -> float:
    if not confirms:
        return 0.0
    mean_c = sum(o.confidence for o in confirms) / len(confirms)
    return round(40.0 * mean_c + 25.0 * min(max(scorer_score, 0.0), 100.0) / 100.0, 3)
