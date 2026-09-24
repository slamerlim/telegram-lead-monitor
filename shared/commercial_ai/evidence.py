"""Server-side evidence validation for episode commercial AI responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.commercial_ai.context import CommercialContext


@dataclass(frozen=True)
class EvidenceValidation:
    ok: bool
    reason: str | None
    evidence_message_ids: list[int]
    primary_ids: list[int]
    supporting_ids: list[int]


def _as_int_list(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def validate_episode_evidence(
    payload: dict[str, Any] | None,
    ctx: CommercialContext,
    *,
    require_seed_author_for_commercial: bool = True,
) -> EvidenceValidation:
    """Fail closed if AI cites IDs outside context or attributes commercial intent without seed-author evidence."""
    p = payload or {}
    allowed = ctx.allowed_message_ids()
    seed_author_ids = ctx.seed_author_message_ids()

    primary = _as_int_list(p.get("primary_evidence_message_ids"))
    supporting = _as_int_list(p.get("supporting_evidence_message_ids"))
    # Also accept flat evidence_message_ids if present.
    flat = _as_int_list(p.get("evidence_message_ids"))
    all_ids = list(dict.fromkeys(primary + supporting + flat))

    for mid in all_ids:
        if mid not in allowed:
            return EvidenceValidation(
                ok=False,
                reason="evidence_id_not_in_context",
                evidence_message_ids=all_ids,
                primary_ids=primary,
                supporting_ids=supporting,
            )

    episode_commercial = p.get("episode_commercial")
    if episode_commercial is True and require_seed_author_for_commercial:
        cited_seed_author = [mid for mid in all_ids if mid in seed_author_ids]
        if not cited_seed_author:
            # At minimum the seed itself must be cited if commercial=true.
            if ctx.seed_message_id not in all_ids and not any(
                mid in seed_author_ids for mid in all_ids
            ):
                return EvidenceValidation(
                    ok=False,
                    reason="commercial_without_seed_author_evidence",
                    evidence_message_ids=all_ids,
                    primary_ids=primary,
                    supporting_ids=supporting,
                )
            # If they cited seed message id, that counts as seed-author evidence.
            if ctx.seed_message_id in all_ids:
                pass
            elif not cited_seed_author:
                return EvidenceValidation(
                    ok=False,
                    reason="commercial_without_seed_author_evidence",
                    evidence_message_ids=all_ids,
                    primary_ids=primary,
                    supporting_ids=supporting,
                )

    return EvidenceValidation(
        ok=True,
        reason=None,
        evidence_message_ids=all_ids,
        primary_ids=primary,
        supporting_ids=supporting,
    )
