"""Commercial lead status transition rules (pure; no DB I/O)."""

from __future__ import annotations

ALLOWED_STATUSES = frozenset(
    {"NEW", "REVIEWED", "CONTACTED", "RESPONDED", "QUALIFIED", "REJECTED", "WON", "LOST"}
)

# Terminal for this commercial milestone (no reopen).
TERMINAL_STATUSES = frozenset({"REJECTED", "WON", "LOST"})

# event_type → required destination status (when status changes)
EVENT_STATUS_HINTS = {
    "CONTACT_ATTEMPT": "CONTACTED",
    "RESPONSE": "RESPONDED",
}

# Allowed next statuses keyed by current status.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "NEW": frozenset({"REVIEWED", "QUALIFIED", "REJECTED"}),
    "REVIEWED": frozenset({"QUALIFIED", "REJECTED"}),
    "QUALIFIED": frozenset({"CONTACTED", "REJECTED"}),
    "CONTACTED": frozenset({"RESPONDED", "LOST"}),
    "RESPONDED": frozenset({"WON", "LOST"}),
    "REJECTED": frozenset(),
    "WON": frozenset(),
    "LOST": frozenset(),
}

# Events that do not require a status change.
NON_STATUS_EVENTS = frozenset({"FOLLOW_UP", "NOTE", "ASSIGN", "BACKFILL", "PROMOTE"})


class TransitionError(ValueError):
    """Invalid commercial status transition."""


def normalize_status(status: str | None) -> str:
    s = (status or "NEW").strip().upper()
    if s not in ALLOWED_STATUSES:
        raise TransitionError(f"unknown status: {status!r}")
    return s


def can_transition(from_status: str, to_status: str) -> bool:
    fr = normalize_status(from_status)
    to = normalize_status(to_status)
    if fr == to:
        return True
    return to in _TRANSITIONS.get(fr, frozenset())


def assert_transition(from_status: str, to_status: str) -> tuple[str, str]:
    fr = normalize_status(from_status)
    to = normalize_status(to_status)
    if fr == to:
        return fr, to
    if to not in _TRANSITIONS.get(fr, frozenset()):
        raise TransitionError(f"illegal transition {fr} → {to}")
    return fr, to


def resolve_event_transition(
    *,
    current_status: str,
    event_type: str,
    to_status: str | None,
) -> tuple[str, str | None]:
    """Return (from_status, new_status_or_None).

    None new_status means keep current (FOLLOW_UP/NOTE/etc.).
    """
    fr = normalize_status(current_status)
    et = (event_type or "").strip().upper()
    if not et:
        raise TransitionError("event_type required")

    if et in NON_STATUS_EVENTS and et not in ("PROMOTE",):
        if to_status:
            raise TransitionError(f"{et} must not set to_status")
        return fr, None

    if et == "PROMOTE":
        # Promote lands at REVIEWED (protected); optional QUALIFIED if already reviewed path.
        target = normalize_status(to_status or "REVIEWED")
        if target not in {"REVIEWED", "QUALIFIED"}:
            raise TransitionError("PROMOTE may only land on REVIEWED or QUALIFIED")
        return fr, target

    if et == "STATUS_CHANGE":
        if not to_status:
            raise TransitionError("STATUS_CHANGE requires to_status")
        _, to = assert_transition(fr, to_status)
        return fr, to

    if et == "CONTACT_ATTEMPT":
        target = normalize_status(to_status or "CONTACTED")
        if target != "CONTACTED":
            raise TransitionError("CONTACT_ATTEMPT must transition to CONTACTED")
        _, to = assert_transition(fr, target)
        return fr, to

    if et == "RESPONSE":
        target = normalize_status(to_status or "RESPONDED")
        if target != "RESPONDED":
            raise TransitionError("RESPONSE must transition to RESPONDED")
        _, to = assert_transition(fr, target)
        return fr, to

    raise TransitionError(f"unknown event_type: {event_type!r}")
