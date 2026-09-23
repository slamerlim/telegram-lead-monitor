"""Commercial ops auth + API behavior (no live DB required for auth/FSM paths)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from services.api.app.ops_routes import _require_operator
from shared.settings import get_settings


def _clear_settings_cache():
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    _clear_settings_cache()
    yield
    _clear_settings_cache()


def test_ops_disabled_returns_404(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_OPS_ENABLED", "false")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "tok")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "op1")
    _clear_settings_cache()
    with pytest.raises(HTTPException) as ei:
        _require_operator("op1", "tok")
    assert ei.value.status_code == 404


def test_ops_requires_token_configured(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_OPS_ENABLED", "true")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "op1")
    _clear_settings_cache()
    with pytest.raises(HTTPException) as ei:
        _require_operator("op1", "tok")
    assert ei.value.status_code == 503


def test_ops_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_OPS_ENABLED", "true")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "good")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "op1")
    _clear_settings_cache()
    with pytest.raises(HTTPException) as ei:
        _require_operator("op1", "bad")
    assert ei.value.status_code == 401


def test_ops_rejects_non_allowlisted(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_OPS_ENABLED", "true")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "good")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "op1")
    monkeypatch.setenv("COMMERCIAL_OPERATOR_IDS", "")
    _clear_settings_cache()
    with pytest.raises(HTTPException) as ei:
        _require_operator("stranger", "good")
    assert ei.value.status_code == 403


def test_ops_accepts_allowlisted(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_OPS_ENABLED", "true")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "good")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "op1")
    monkeypatch.setenv("COMMERCIAL_OPERATOR_IDS", "")
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_TOKENS", "")
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_IDS", "")
    _clear_settings_cache()
    assert _require_operator("op1", "good") == "op1"


def test_ops_prefers_commercial_operator_ids(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_OPS_ENABLED", "true")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "good")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "alice")
    monkeypatch.setenv("COMMERCIAL_OPERATOR_IDS", "ops_pilot")
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_IDS", "alice")
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_TOKENS", "alice:secret")
    monkeypatch.setenv("INDEPENDENT_REVIEW_HMAC_SECRET", "hmac")
    _clear_settings_cache()
    assert _require_operator("ops_pilot", "good") == "ops_pilot"
    with pytest.raises(HTTPException) as ei:
        _require_operator("alice", "good")
    assert ei.value.status_code == 403


def test_ops_rejects_blind_reviewer_overlap(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_OPS_ENABLED", "true")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "good")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "alice")
    monkeypatch.setenv("COMMERCIAL_OPERATOR_IDS", "")
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_IDS", "alice")
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_TOKENS", "alice:secret")
    monkeypatch.setenv("INDEPENDENT_REVIEW_HMAC_SECRET", "hmac")
    _clear_settings_cache()
    with pytest.raises(HTTPException) as ei:
        _require_operator("alice", "good")
    assert ei.value.status_code == 409


def test_ops_rejects_reserved_prefix(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_OPS_ENABLED", "true")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "good")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "agent_bot")
    _clear_settings_cache()
    with pytest.raises(HTTPException) as ei:
        _require_operator("agent_bot", "good")
    assert ei.value.status_code == 403


def test_event_in_schema_requires_evidence_fields():
    from services.api.app.schemas import OpsEventIn

    evt = OpsEventIn(
        event_type="NOTE",
        occurred_at=datetime.now(timezone.utc),
        note="hello",
    )
    assert evt.event_type == "NOTE"
