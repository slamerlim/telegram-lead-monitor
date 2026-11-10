"""Commercial AI decision policy unit tests."""

from __future__ import annotations

from shared.commercial_ai.policy import (
    DECISION_CANDIDATE,
    DECISION_CONFIRMED,
    DECISION_REJECTED,
    DECISION_UNCERTAIN,
    OpinionView,
    decide_commercial,
    opinion_from_payload,
    grounded_commercial_evidence,
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


def test_skeptical_rejection_does_not_become_confirmation_from_topic_match():
    op = opinion_from_payload(mode="B", status="ok", synthetic=False, model_family="openai",
        payload={"is_commercial_opportunity": False, "matches_objectives": True,
                 "confidence": .95, "lead_type": "BOT_PURCHASE", "uncertain": False})
    assert not op.commercial


def test_two_votes_from_same_family_are_not_independent_confirmation():
    decision = decide_commercial([_op("A", commercial=True), _op("B", commercial=True)])
    assert decision.decision == DECISION_CANDIDATE
    assert decision.reason_code == "insufficient_confirming_families"


def test_positive_evidence_must_be_present_in_original_request():
    payload = {"is_commercial_opportunity": True, "buyer_action": "COMMISSION", "evidence": ["Budget $3000"]}
    assert grounded_commercial_evidence(payload, "Can someone build a trading bot? Budget $3000") == (True, None)
    assert not grounded_commercial_evidence(payload, "Can someone teach me trading?")[0]
    assert not grounded_commercial_evidence({**payload, "evidence": []}, "Budget $3000")[0]


def test_missing_type_and_nonfinite_confidence_cannot_confirm():
    for value in (None, float('nan'), float('inf'), '0.99', True, 2):
        op = opinion_from_payload(mode="A", status="ok", synthetic=False, model_family="openai",
            payload={"is_commercial_opportunity": True, "matches_objectives": True,
                     "confidence": value, "lead_type": "BOT_PURCHASE", "uncertain": False})
        assert not op.commercial and op.uncertain
    op = opinion_from_payload(mode="B", status="ok", synthetic=False, model_family="openai",
        payload={"is_commercial_opportunity": True, "matches_objectives": True,
                 "confidence": .99, "lead_type": None, "uncertain": False})
    assert not op.commercial
