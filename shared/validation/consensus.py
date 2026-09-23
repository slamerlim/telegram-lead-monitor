"""Deterministic consensus for AI validator opinions (Modes A/B/C; D audit-only in v1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from shared.validation.definition import COMMERCIAL_LEAD_TYPES

AI_TRUE = "AI_TRUE"
AI_FALSE = "AI_FALSE"
AI_UNCERTAIN = "AI_UNCERTAIN"
AI_INSUFFICIENT = "AI_INSUFFICIENT_EVIDENCE"

VALIDATED_TRUE = "VALIDATED_TRUE"
VALIDATED_FALSE = "VALIDATED_FALSE"
CONTESTED = "CONTESTED"
UNCERTAIN = "UNCERTAIN"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
FAILED_VALIDATION = "FAILED_VALIDATION"
VALIDATION_ERROR = "VALIDATION_ERROR"

POLICY_VERSION = "v1"


@dataclass(frozen=True)
class Opinion:
    mode: str  # A|B|C|D
    label: str
    confidence: float
    lead_type: str | None = None
    fp_class: str | None = None
    evidence: tuple[str, ...] = ()
    uncertain: bool = False
    provider: str = "cursor"
    model: str = ""
    model_family: str = ""
    blind_ok: bool = True
    signed: bool = True
    allowlisted: bool = True


@dataclass
class ConsensusResult:
    state: str
    rationale_code: str
    gate_eligible: bool
    lead_type: str | None = None
    n_true: int = 0
    n_false: int = 0
    n_uncertain: int = 0
    n_insufficient: int = 0
    distinct_families: int = 0
    modes_present: str = ""
    adjudicator_label: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _evidence_ok(message_text: str, evidence: Iterable[str]) -> bool:
    if not evidence:
        return False
    for quote in evidence:
        if not quote or quote not in message_text:
            return False
    return True


def normalize_opinion(
    op: Opinion,
    message_text: str,
    *,
    true_floor: float = 0.80,
    false_floor: float = 0.60,
) -> Opinion:
    label = op.label
    if op.uncertain:
        label = AI_UNCERTAIN
    if label == AI_TRUE:
        if op.lead_type not in COMMERCIAL_LEAD_TYPES:
            label = AI_INSUFFICIENT
        elif not _evidence_ok(message_text, op.evidence):
            label = AI_INSUFFICIENT
        elif op.confidence < true_floor:
            label = AI_UNCERTAIN
    elif label == AI_FALSE:
        if op.confidence < false_floor:
            label = AI_UNCERTAIN
    return Opinion(
        mode=op.mode,
        label=label,
        confidence=op.confidence,
        lead_type=op.lead_type if label == AI_TRUE else None,
        fp_class=op.fp_class,
        evidence=op.evidence,
        uncertain=label == AI_UNCERTAIN,
        provider=op.provider,
        model=op.model,
        model_family=op.model_family,
        blind_ok=op.blind_ok,
        signed=op.signed,
        allowlisted=op.allowlisted,
    )


def compute_consensus(
    opinions: list[Opinion],
    message_text: str,
    *,
    require_distinct_families: int = 3,
    true_floor: float = 0.80,
    false_floor: float = 0.60,
    false_majority_ok: bool = True,
    missing_modes_exhausted: bool = False,
    diversity_precondition_failed: bool = False,
) -> ConsensusResult:
    abc = [o for o in opinions if o.mode in ("A", "B", "C")]
    d_ops = [o for o in opinions if o.mode == "D"]
    adj = d_ops[-1].label if d_ops else None

    if diversity_precondition_failed:
        return ConsensusResult(
            state=FAILED_VALIDATION,
            rationale_code="PROVIDER_DIVERSITY_FAILED",
            gate_eligible=False,
            adjudicator_label=adj,
        )

    modes = {o.mode for o in abc}
    if modes != {"A", "B", "C"}:
        if missing_modes_exhausted:
            return ConsensusResult(
                state=VALIDATION_ERROR,
                rationale_code="MODES_INCOMPLETE_EXHAUSTED",
                gate_eligible=False,
                modes_present="".join(sorted(modes)),
                adjudicator_label=adj,
            )
        return ConsensusResult(
            state=UNCERTAIN,
            rationale_code="MODES_PENDING",
            gate_eligible=False,
            modes_present="".join(sorted(modes)),
            adjudicator_label=adj,
        )

    if any(not (o.blind_ok and o.signed and o.allowlisted) for o in abc):
        return ConsensusResult(
            state=FAILED_VALIDATION,
            rationale_code="BLINDNESS_OR_AUTH_FAILED",
            gate_eligible=False,
            adjudicator_label=adj,
        )

    families = {o.model_family for o in abc if o.model_family}
    if len(families) < require_distinct_families:
        return ConsensusResult(
            state=FAILED_VALIDATION,
            rationale_code="PROVIDER_DIVERSITY_FAILED",
            gate_eligible=False,
            distinct_families=len(families),
            modes_present="ABC",
            adjudicator_label=adj,
        )

    normed = [normalize_opinion(o, message_text, true_floor=true_floor, false_floor=false_floor) for o in abc]
    labels = [o.label for o in normed]
    n_true = labels.count(AI_TRUE)
    n_false = labels.count(AI_FALSE)
    n_unc = labels.count(AI_UNCERTAIN)
    n_ins = labels.count(AI_INSUFFICIENT)

    base = dict(
        n_true=n_true,
        n_false=n_false,
        n_uncertain=n_unc,
        n_insufficient=n_ins,
        distinct_families=len(families),
        modes_present="ABC",
        adjudicator_label=adj,  # v1: recorded only; does not change state
    )

    if n_true >= 1 and n_false >= 1:
        return ConsensusResult(state=CONTESTED, rationale_code="DEFINITE_VOTE_CONFLICT", gate_eligible=False, **base)

    if n_true == 3:
        # Prefer Mode C lead_type when present
        lead = next((o.lead_type for o in normed if o.mode == "C" and o.lead_type), None)
        if lead is None:
            lead = next((o.lead_type for o in normed if o.lead_type), None)
        return ConsensusResult(
            state=VALIDATED_TRUE,
            rationale_code="UNANIMOUS_QUALIFIED_TRUE",
            gate_eligible=True,
            lead_type=lead,
            **base,
        )

    if n_false == 3 or (false_majority_ok and n_false >= 2 and n_true == 0 and n_unc + n_ins <= 1):
        return ConsensusResult(
            state=VALIDATED_FALSE,
            rationale_code="UNANIMOUS_OR_MAJORITY_FALSE",
            gate_eligible=True,
            **base,
        )

    if n_ins >= 1 and n_true == 0 and n_false == 0:
        return ConsensusResult(
            state=INSUFFICIENT_EVIDENCE,
            rationale_code="INSUFFICIENT_EVIDENCE",
            gate_eligible=False,
            **base,
        )

    return ConsensusResult(state=UNCERTAIN, rationale_code="NO_CLEAR_MAJORITY", gate_eligible=False, **base)


def mode_b_to_label(payload: dict[str, Any]) -> tuple[str, str | None]:
    """Map Mode B structured output to AI_* label."""
    if payload.get("uncertain"):
        return AI_UNCERTAIN, None
    if payload.get("is_false_positive"):
        return AI_FALSE, payload.get("fp_class")
    # Require exact JSON boolean true (not truthy strings).
    if payload.get("is_false_positive") is False and payload.get("matches_objectives") is True:
        return AI_TRUE, payload.get("lead_type")
    # Skeptic says not FP but unclear commercial match → uncertain
    if payload.get("is_false_positive") is False:
        return AI_UNCERTAIN, None
    return AI_UNCERTAIN, None


def mode_a_to_label(payload: dict[str, Any]) -> tuple[str, str | None]:
    if payload.get("uncertain"):
        return AI_UNCERTAIN, None
    if payload.get("is_target_lead"):
        return AI_TRUE, payload.get("lead_type")
    return AI_FALSE, payload.get("fp_class")


def mode_c_to_label(payload: dict[str, Any]) -> tuple[str, str | None]:
    if payload.get("uncertain"):
        return AI_UNCERTAIN, None
    if payload.get("has_commercial_intent"):
        return AI_TRUE, payload.get("lead_type")
    return AI_FALSE, payload.get("fp_class")
