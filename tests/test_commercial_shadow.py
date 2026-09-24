"""Shadow kill switches + rate-limit settings defaults."""

from __future__ import annotations

from shared.settings import Settings


def test_discovery_and_shadow_default_off():
    s = Settings(
        telegram_api_id=1,
        telegram_api_hash="x",
        telegram_phone="+10000000000",
    )
    assert s.commercial_discovery_enabled is False
    assert s.commercial_episode_shadow_enabled is False
    assert s.commercial_discovery_max_candidates_per_hour > 0
    assert s.commercial_episode_max_reviews_per_hour > 0
    assert s.commercial_episode_max_context_messages <= 20
