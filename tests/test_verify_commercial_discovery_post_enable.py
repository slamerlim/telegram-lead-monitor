"""Unit tests for post-enable commercial discovery verifier classifiers.

No Docker/DB required — pure failure-class and SQL guard regressions.
Stubs heavy imports so host pytest can collect without sqlalchemy.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

for mod_name in (
    "sqlalchemy",
    "services",
    "services.analyzer",
    "services.analyzer.app",
    "services.analyzer.app.scoring",
    "shared",
    "shared.commercial_ai",
    "shared.commercial_ai.discovery",
    "shared.db",
    "shared.settings",
    "scripts.audit_disc_v6_refined_population_cycle2",
):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        if mod_name == "sqlalchemy":
            stub.text = lambda *a, **k: None  # type: ignore[attr-defined]
        if mod_name.endswith("scoring"):
            stub.LeadScorer = object  # type: ignore[attr-defined]
        if mod_name.endswith("discovery"):
            stub.DISCOVERY_VERSION_V4 = "disc_v4"
            stub.DISCOVERY_VERSION_V6 = "disc_v6"
            stub.evaluate_discovery = None
            stub.population_bucket = None
        if mod_name.endswith("db"):
            stub.SessionLocal = None
        if mod_name.endswith("settings"):
            stub.get_settings = lambda: None  # type: ignore[attr-defined]
        if mod_name.endswith("cycle2"):
            stub.E101_SINCE_ISO = ""
            stub.LEGACY_COMMERCIAL = ""
            stub.POOL_DOMAIN = ""
            stub.POOL_SELECT_SQL = ""
            stub.REFINED_COMMERCIAL = ""
            stub.contamination = lambda *a, **k: 0.0
            stub.first_loss = lambda *a, **k: ""
            stub.is_exchange_community = lambda *a, **k: False
        sys.modules[mod_name] = stub

_SPEC = importlib.util.spec_from_file_location(
    "_verify_cd_cls",
    _ROOT / "scripts" / "verify_commercial_discovery_post_enable.py",
)
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)

FailureClass = _mod.FailureClass
assert_sql_uses_message_scores = _mod.assert_sql_uses_message_scores
classify_rate = _mod.classify_rate
prioritize_failure = _mod.prioritize_failure
qualitative_live_contamination = _mod.qualitative_live_contamination


def test_assert_sql_rejects_bare_scores_table() -> None:
    import pytest

    with pytest.raises(ValueError, match="QUERY_FAILURE"):
        assert_sql_uses_message_scores("SELECT COUNT(*) FROM scores")
    with pytest.raises(ValueError, match="QUERY_FAILURE"):
        assert_sql_uses_message_scores(
            "SELECT 1 FROM scores s JOIN messages m ON true"
        )


def test_assert_sql_allows_message_scores() -> None:
    assert_sql_uses_message_scores(
        "SELECT COUNT(*) FROM message_scores AS message_scores"
    )
    assert_sql_uses_message_scores(
        "SELECT (SELECT COUNT(*) FROM message_scores) AS scores"
    )


def test_classify_short_zero_is_expected_low_rate() -> None:
    assert (
        classify_rate(hours=60 / 3600, cand_count=0)
        == FailureClass.EXPECTED_LOW_RATE
    )


def test_classify_contaminated_above_threshold() -> None:
    assert (
        classify_rate(hours=11.0, cand_count=6, contamination_pct=100.0)
        == FailureClass.CONTAMINATED
    )


def test_classify_above_expected_clean() -> None:
    assert (
        classify_rate(hours=11.0, cand_count=6, contamination_pct=0.0)
        == FailureClass.ABOVE_EXPECTED
    )


def test_classify_near_expected() -> None:
    assert (
        classify_rate(hours=24.0, cand_count=1, contamination_pct=0.0)
        == FailureClass.EXPECTED_LOW_RATE
    )


def test_prioritize_db_over_rate() -> None:
    assert (
        prioritize_failure(
            FailureClass.EXPECTED_LOW_RATE,
            FailureClass.DB_FAILURE,
            FailureClass.CONTAMINATED,
        )
        == FailureClass.DB_FAILURE
    )


def test_prioritize_contaminated_over_above_expected() -> None:
    assert (
        prioritize_failure(
            FailureClass.ABOVE_EXPECTED,
            FailureClass.CONTAMINATED,
        )
        == FailureClass.CONTAMINATED
    )


def test_qualitative_live_contamination_promo_dup() -> None:
    items = [
        {
            "preview": "Honestly, I’ve been enjoying my experience with Forex High Way EA so far. What I really like is how it helps automate",
            "trigger_type": "v6_automation_domain",
            "score": 0.0,
            "v6_automation": True,
        }
        for _ in range(5)
    ]
    items.append(
        {
            "preview": "Boss can you help me with my position and point my bot is not working",
            "trigger_type": "v6_repair_domain",
            "score": 18.0,
            "v6_automation": False,
        }
    )
    out = qualitative_live_contamination(items)
    assert out["n"] == 6
    assert out["pct"] == 100.0
    assert out["reason_counts"].get("promo_marketing", 0) >= 5
    assert out["reason_counts"].get("supportish", 0) >= 1
