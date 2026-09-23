"""Tests for diagnostic batch gate isolation."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from services.api.app.ai_validation_routes import gate_latest_consensus_subquery
from shared.models import ValidationConsensus
from shared.validation.diagnostic import (
    DIAGNOSTIC_BATCH_PREFIXES,
    is_diagnostic_batch,
    snapshot_gate_eligible,
)


def test_diagnostic_prefixes_hygiene():
    assert DIAGNOSTIC_BATCH_PREFIXES
    for p in DIAGNOSTIC_BATCH_PREFIXES:
        assert p.startswith("aival_")
        assert p.endswith("_")


def test_is_diagnostic_batch():
    assert is_diagnostic_batch("aival_diag_commercial_candidates_20260923")
    assert is_diagnostic_batch("aival_diag_x")
    assert not is_diagnostic_batch("aival_batch_20260923")
    assert not is_diagnostic_batch("indep_review_2026-09-22")
    assert not is_diagnostic_batch("aivalXdiag_x")
    assert not is_diagnostic_batch("AIVAL_DIAG_x")
    assert not is_diagnostic_batch("")
    assert not is_diagnostic_batch(None)


def test_snapshot_gate_eligible_diagnostic_blocks():
    assert (
        snapshot_gate_eligible(
            consensus_gate_eligible=True,
            synthetic=False,
            diagnostic=True,
            state="VALIDATED_TRUE",
        )
        is False
    )
    assert (
        snapshot_gate_eligible(
            consensus_gate_eligible=True,
            synthetic=False,
            diagnostic=False,
            state="VALIDATED_TRUE",
        )
        is True
    )
    assert (
        snapshot_gate_eligible(
            consensus_gate_eligible=True,
            synthetic=True,
            diagnostic=False,
            state="VALIDATED_FALSE",
        )
        is False
    )
    assert (
        snapshot_gate_eligible(
            consensus_gate_eligible=True,
            synthetic=False,
            diagnostic=False,
            state="UNCERTAIN",
        )
        is False
    )


def test_gate_latest_subquery_excludes_diagnostic_prefix():
    q = select(
        ValidationConsensus.message_id,
        func.max(ValidationConsensus.id).label("max_id"),
    ).where(ValidationConsensus.synthetic.is_(False))
    for prefix in DIAGNOSTIC_BATCH_PREFIXES:
        q = q.where(func.left(ValidationConsensus.sample_batch_id, len(prefix)) != prefix)
    q = q.group_by(ValidationConsensus.message_id)
    text = str(q.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "aival_diag_" in text
    assert "left(" in text.lower()
    assert "max(" in text.lower()
    helper_sql = str(
        select(gate_latest_consensus_subquery().c.message_id).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "anon" in helper_sql.lower() or "aival_diag_" in helper_sql
