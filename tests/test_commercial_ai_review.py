"""Commercial AI FSM + draft validation."""

from __future__ import annotations

import pytest

from shared.commercial_ai.store import validate_draft
from shared.lead_fsm import TransitionError, resolve_event_transition


def test_ai_promote_system_only():
    fr, to = resolve_event_transition(
        current_status="NEW",
        event_type="AI_PROMOTE",
        to_status=None,
        actor_kind="system",
    )
    assert fr == "NEW" and to == "AI_CONFIRMED"
    with pytest.raises(TransitionError):
        resolve_event_transition(
            current_status="NEW",
            event_type="AI_PROMOTE",
            to_status=None,
            actor_kind="operator",
        )


def test_ai_confirmed_to_contacted():
    fr, to = resolve_event_transition(
        current_status="AI_CONFIRMED",
        event_type="CONTACT_ATTEMPT",
        to_status=None,
        actor_kind="operator",
    )
    assert to == "CONTACTED"


def test_draft_rejects_urls_and_guarantees():
    ok, _ = validate_draft("Need help building your trading bot from the message", "trading bot")
    assert ok
    bad, reason = validate_draft("See https://t.me/x for guarantees", "trading bot")
    assert not bad
    assert reason in {"contains_url", "forbidden_claim"}
