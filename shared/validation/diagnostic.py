"""Server-owned diagnostic batch markers for AI validation gate isolation.

Diagnostic batches must never inflate ai_validated_messages / ai_validated_true.
Isolation is decided solely from sample_batch_id prefix (not client synthetic flags).
"""

from __future__ import annotations

# Hardcoded registry: changing isolation requires a code review, not env/client toggles.
DIAGNOSTIC_BATCH_PREFIXES: tuple[str, ...] = ("aival_diag_",)


def is_diagnostic_batch(sample_batch_id: str | None) -> bool:
    """True when sample_batch_id is a server-recognized diagnostic-only batch."""
    if not sample_batch_id:
        return False
    return sample_batch_id.startswith(DIAGNOSTIC_BATCH_PREFIXES)


def snapshot_gate_eligible(
    *,
    consensus_gate_eligible: bool,
    synthetic: bool,
    diagnostic: bool,
    state: str,
) -> bool:
    """Persistable gate_eligible flag (defense in depth; gate numerators also exclude diag batches)."""
    return bool(
        consensus_gate_eligible
        and not synthetic
        and not diagnostic
        and state in ("VALIDATED_TRUE", "VALIDATED_FALSE")
    )
