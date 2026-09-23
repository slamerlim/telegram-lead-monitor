"""Commercial AI decision policy unit tests."""

from __future__ import annotations

from shared.commercial_ai.policy import (
    DECISION_CANDIDATE,
    DECISION_CONFIRMED,
    DECISION_REJECTED,
    DECISION_UNCERTAIN,
    OpinionView,
    decide_commercial,
)


def _op(
    mode: str,
    *,
    commercial: bool = False,
    hard_veto: bool = False,
    uncertain: bool = False,
    lead_type: str | None = "BOT_PURCHASE",
    matches: bool = True,
    conf: float = 0.9,
    family: str = "anthropic",
) -> OpinionView:
    return OpinionView(
        mode=mode,
        status="ok",
        commercial=commercial,
        lead_type=lead_type,
        matches_objectives=matches,
        hard_veto=hard_veto,
        uncertain=uncertain,
        confidence=conf,
        synthetic=False,
        model_family=family,
    )


def test_majority_confirm():
    ops = [
        _op("A", commercial=True, family="anthropic"),
        _op("B", commercial=True, family="openai"),
        _op("C", commercial=False, family="xai"),
    ]
    d = decide_commercial(ops, auto_promote=True)
    assert d.decision == DECISION_CONFIRMED
    assert d.n_confirm == 2


def test_hard_veto_rejects():
    ops = [
        _op("A", commercial=True, family="anthropic"),
        _op("B", commercial=True, hard_veto=True, family="openai"),
        _op("C", commercial=True, family="xai"),
    ]
    d = decide_commercial(ops)
    assert d.decision == DECISION_REJECTED
    assert d.reason_code == "hard_veto"


def test_single_confirm_is_candidate():
    ops = [
        _op("A", commercial=True, family="anthropic"),
        _op("B", commercial=False, family="openai"),
        _op("C", commercial=False, family="xai"),
    ]
    d = decide_commercial(ops)
    assert d.decision == DECISION_CANDIDATE


def test_type_conflict_is_candidate():
    ops = [
        _op("A", commercial=True, lead_type="BOT_PURCHASE", family="anthropic"),
        _op("B", commercial=True, lead_type="BOT_REPAIR", family="openai"),
        _op("C", commercial=False, family="xai"),
    ]
    d = decide_commercial(ops)
    assert d.decision == DECISION_CANDIDATE
    assert d.reason_code == "lead_type_conflict"


def test_insufficient_agents():
    ops = [_op("A", commercial=True, family="anthropic")]
    d = decide_commercial(ops)
    assert d.decision == DECISION_UNCERTAIN


def test_shadow_mode_label():
    ops = [
        _op("A", commercial=True, family="anthropic"),
        _op("B", commercial=True, family="openai"),
        _op("C", commercial=True, family="xai"),
    ]
    d = decide_commercial(ops, auto_promote=False)
    assert d.decision == "SHADOW_CONFIRMED"
