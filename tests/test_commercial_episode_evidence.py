"""Episode evidence validation fail-closed rules."""

from __future__ import annotations

from datetime import datetime, timezone

from shared.commercial_ai.context import CommercialContext, ContextMember
from shared.commercial_ai.evidence import validate_episode_evidence


def _ctx(*, seed_author_id=5):
    now = datetime.now(timezone.utc)
    members = [
        ContextMember(
            message_id=10,
            telegram_message_id=100,
            author_id=seed_author_id,
            text="seed hire trading bot",
            message_date=now,
            relation="SEED",
            selection_reason="seed",
            text_sha256="a" * 64,
            is_seed_author=True,
        ),
        ContextMember(
            message_id=20,
            telegram_message_id=200,
            author_id=99,
            text="community noise about trading",
            message_date=now,
            relation="COMMUNITY_CONTEXT",
            selection_reason="community",
            text_sha256="b" * 64,
            is_seed_author=False,
        ),
    ]
    return CommercialContext(
        seed_message_id=10,
        seed_author_id=seed_author_id,
        community_id=1,
        context_version="ctx_v1",
        context_hash="h",
        members=members,
    )


def test_rejects_unknown_evidence_id():
    v = validate_episode_evidence(
        {
            "episode_commercial": False,
            "primary_evidence_message_ids": [999],
        },
        _ctx(),
    )
    assert not v.ok
    assert v.reason == "evidence_id_not_in_context"


def test_rejects_commercial_from_community_only():
    v = validate_episode_evidence(
        {
            "episode_commercial": True,
            "primary_evidence_message_ids": [20],
            "supporting_evidence_message_ids": [],
        },
        _ctx(),
    )
    assert not v.ok
    assert v.reason == "commercial_without_seed_author_evidence"


def test_accepts_seed_author_evidence():
    v = validate_episode_evidence(
        {
            "episode_commercial": True,
            "primary_evidence_message_ids": [10],
            "supporting_evidence_message_ids": [20],
        },
        _ctx(),
    )
    assert v.ok
