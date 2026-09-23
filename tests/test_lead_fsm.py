"""Commercial lead FSM unit tests."""

from __future__ import annotations

import pytest

from shared.lead_fsm import (
    TransitionError,
    assert_transition,
    can_transition,
    resolve_event_transition,
)


def test_happy_path_transitions():
    assert can_transition("NEW", "REVIEWED")
    assert can_transition("REVIEWED", "QUALIFIED")
    assert can_transition("QUALIFIED", "CONTACTED")
    assert can_transition("CONTACTED", "RESPONDED")
    assert can_transition("RESPONDED", "WON")


def test_rejects_illegal_jumps():
    with pytest.raises(TransitionError):
        assert_transition("NEW", "WON")
    with pytest.raises(TransitionError):
        assert_transition("REJECTED", "QUALIFIED")
    with pytest.raises(TransitionError):
        assert_transition("WON", "CONTACTED")


def test_contact_attempt_and_response_events():
    fr, to = resolve_event_transition(
        current_status="QUALIFIED", event_type="CONTACT_ATTEMPT", to_status=None
    )
    assert (fr, to) == ("QUALIFIED", "CONTACTED")
    fr, to = resolve_event_transition(
        current_status="CONTACTED", event_type="RESPONSE", to_status=None
    )
    assert (fr, to) == ("CONTACTED", "RESPONDED")


def test_follow_up_keeps_status():
    fr, to = resolve_event_transition(
        current_status="CONTACTED", event_type="FOLLOW_UP", to_status=None
    )
    assert fr == "CONTACTED" and to is None


def test_follow_up_cannot_set_status():
    with pytest.raises(TransitionError):
        resolve_event_transition(
            current_status="CONTACTED", event_type="FOLLOW_UP", to_status="RESPONDED"
        )
