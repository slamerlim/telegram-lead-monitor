"""Scoring decision labels for persisted training frames."""

from __future__ import annotations

from typing import Any


def decision_from_result(result: Any) -> str:
    tier = (getattr(result, "tier", None) or "").upper()
    if tier == "HIGH":
        return "POSITIVE"
    if tier == "MEDIUM":
        return "AMBIGUOUS"
    return "NEGATIVE"
