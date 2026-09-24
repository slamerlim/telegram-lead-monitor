"""Episode key dedup / unrelated project separation."""

from __future__ import annotations

from datetime import datetime, timezone

from shared.commercial_ai.discovery import episode_key, fingerprints_compatible


def test_episode_key_fallback_without_author():
    dt = datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert episode_key(author_id=None, topic_fp="trading_bot", message_date=dt, message_id=42) == "msg:42"


def test_episode_key_includes_iso_week():
    dt = datetime(2026, 9, 20, tzinfo=timezone.utc)
    k = episode_key(author_id=7, topic_fp="trading_bot|repair", message_date=dt, message_id=1)
    assert k.startswith("a7:")
    assert "2026-W" in k


def test_unrelated_fingerprints_not_merged():
    assert not fingerprints_compatible("trading_bot|repair", "news_digest|markets")
    assert fingerprints_compatible("trading_bot|exchange_api", "exchange_api|python")
