"""Fail-closed AI validation gate evaluation (pure; no DB)."""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.validation.consensus import VALIDATED_FALSE, VALIDATED_TRUE


@dataclass
class AiGateInput:
    auth_ready: bool
    modes_abc_present: bool
    distinct_families: int
    require_families: int = 3
    id_overlap: bool = False
    unattested_rows: int = 0
    config_mismatch_rows: int = 0
    validated_messages: int = 0
    validated_true: int = 0
    contested: int = 0
    uncertain: int = 0
    insufficient: int = 0
    failed: int = 0
    error: int = 0
    min_validated_messages: int = 100
    min_validated_true: int = 30
    max_error_rate: float = 0.20
    max_contested_rate: float = 0.25
    sample_has_negatives_strata: bool = True


@dataclass
class AiGateResult:
    ready: bool
    closed_reasons: list[str] = field(default_factory=list)
    ml_training_enabled: bool = False  # always False until explicit future policy


def evaluate_ai_gate(inp: AiGateInput) -> AiGateResult:
    reasons: list[str] = []
    if not inp.auth_ready:
        reasons.append("ai_auth_not_configured")
    if not inp.modes_abc_present:
        reasons.append("ai_modes_missing")
    if inp.distinct_families < inp.require_families:
        reasons.append(f"ai_family_diversity<{inp.require_families}")
    if inp.id_overlap:
        reasons.append("validator_id_overlap")
    if inp.unattested_rows > 0:
        reasons.append("ai_unattested_rows>0")
    if inp.config_mismatch_rows > 0:
        reasons.append("ai_config_mismatch_rows>0")
    if inp.validated_messages < inp.min_validated_messages:
        reasons.append(f"ai_validated_messages<{inp.min_validated_messages}")
    if inp.validated_true < inp.min_validated_true:
        reasons.append(f"ai_validated_true<{inp.min_validated_true}")

    decided = inp.validated_messages + inp.contested + inp.uncertain + inp.insufficient + inp.failed + inp.error
    if decided > 0:
        err_rate = (inp.failed + inp.error) / decided
        if err_rate > inp.max_error_rate:
            reasons.append("ai_error_rate>threshold")
        cont_rate = inp.contested / decided
        if cont_rate > inp.max_contested_rate:
            reasons.append("ai_contested_rate>threshold")
    if not inp.sample_has_negatives_strata:
        reasons.append("ai_sample_scorer_biased")

    return AiGateResult(ready=len(reasons) == 0, closed_reasons=reasons, ml_training_enabled=False)


def positive_eligible(state: str) -> bool:
    """Hard rule: only VALIDATED_TRUE is positive for ML/commercial AI gate numerators."""
    return state == VALIDATED_TRUE


def gate_eligible_states() -> frozenset[str]:
    return frozenset({VALIDATED_TRUE, VALIDATED_FALSE})
