"""Blind validation inputs — forbid scorer/community/prior-label leakage."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping

FORBIDDEN_KEYS = frozenset(
    {
        "score",
        "tier",
        "decision",
        "lead_id",
        "lead_type",
        "buyer_type",
        "veto",
        "vetoes",
        "matched_categories",
        "community_id",
        "community_username",
        "community_name",
        "author_username",
        "author_id",
        "message_url",
        "message_date",
        "stratum",
        "prior_label",
        "prior_agent_label",
        "scorer_score",
        "scorer_tier",
        "scorer_decision",
        "scorer_lead_type",
        "scorer_buyer_type",
        "opportunity_key",
        "human_label",
        "predicted_label",
    }
)


class BlindnessViolation(ValueError):
    """Raised when forbidden metadata is injected into a blind validation input."""


def _scan_mapping(obj: Any, path: str = "") -> None:
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            key = str(k).lower()
            here = f"{path}.{key}" if path else key
            if key in FORBIDDEN_KEYS:
                raise BlindnessViolation(f"forbidden blind-input key: {here}")
            _scan_mapping(v, here)
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            _scan_mapping(item, f"{path}[{i}]")


@dataclass(frozen=True, slots=True)
class BlindItem:
    """Minimum fields for Modes A/B/C. No scorer or community metadata."""

    text: str
    nonce: str
    text_length: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise BlindnessViolation("text must be a non-empty string")
        if not isinstance(self.nonce, str) or not self.nonce.strip():
            raise BlindnessViolation("nonce must be a non-empty string")
        # Reject unexpected construction via object.__setattr__ tricks by scanning dict form
        raw = {f.name: getattr(self, f.name) for f in fields(self)}
        _scan_mapping(raw)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> BlindItem:
        if not isinstance(data, Mapping):
            raise BlindnessViolation("blind input must be a mapping")
        _scan_mapping(data)
        allowed = {f.name for f in fields(cls)}
        extra = set(data.keys()) - allowed
        if extra:
            raise BlindnessViolation(f"unexpected blind-input keys: {sorted(extra)}")
        text = data.get("text")
        nonce = data.get("nonce")
        text_length = data.get("text_length")
        if text_length is None and isinstance(text, str):
            text_length = len(text)
        return cls(text=text, nonce=nonce, text_length=text_length)  # type: ignore[arg-type]
