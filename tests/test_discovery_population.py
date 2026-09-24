"""Population-bucket helper smoke tests."""

from __future__ import annotations

from shared.commercial_ai.discovery import DiscoveryFeatures, population_bucket


def test_population_bucket_labels():
    assert (
        population_bucket(
            DiscoveryFeatures(buyer_direction="BUYER", eligible=True, project_scope=True)
        )
        == "Direct buyer/RFQ candidate"
    )
    assert (
        population_bucket(
            DiscoveryFeatures(buyer_direction="SEEKER", seeker_signal=True)
        )
        == "Job seeker/resume"
    )
    assert (
        population_bucket(
            DiscoveryFeatures(employment_signal=True, buyer_direction="EMPLOYER")
        )
        == "Corporate/job-board hiring"
    )
