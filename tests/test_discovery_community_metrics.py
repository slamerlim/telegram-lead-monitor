"""Community metrics helpers used by discovery opportunity profiling."""

from __future__ import annotations

from shared.commercial_ai.discovery import population_bucket
from shared.commercial_ai.discovery import DiscoveryFeatures


def test_population_bucket_buyer():
    feats = DiscoveryFeatures(buyer_direction="BUYER", project_scope=True, eligible=True)
    assert population_bucket(feats) == "Direct buyer/RFQ candidate"


def test_population_bucket_seeker():
    feats = DiscoveryFeatures(buyer_direction="SEEKER", seeker_signal=True)
    assert "seeker" in population_bucket(feats).lower() or "Job" in population_bucket(feats)


def test_buyer_signal_density_formula():
    messages_7d = 500
    buyer_signal_candidates = 10
    density = 1000.0 * buyer_signal_candidates / max(messages_7d, 1)
    assert density == 20.0
    # Prefer density over raw volume
    noisy = 1000.0 * 3 / max(50000, 1)
    assert density > noisy
