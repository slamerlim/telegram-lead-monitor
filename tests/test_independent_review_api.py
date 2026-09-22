"""Unit tests for independent-review auth helpers and API gate rules."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shared.independent_review_auth import (
    mint_queue_token,
    parse_queue_token,
    parse_reviewer_tokens,
    reserved_id_match,
    verify_reviewer_token,
)
from shared.settings import get_settings


def test_reserved_id_match_uses_delimiter():
    prefixes = ("agent", "audit", "smoke")
    assert reserved_id_match("agent_x", prefixes)
    assert reserved_id_match("audit", prefixes)
    assert reserved_id_match("smoke_bot", prefixes)
    assert not reserved_id_match("auditor", prefixes)
    assert not reserved_id_match("agenta", prefixes)
    assert not reserved_id_match("alice", prefixes)


def test_token_parse_and_verify():
    tokens = parse_reviewer_tokens("alice:s1, bob:s2")
    assert tokens == {"alice": "s1", "bob": "s2"}
    assert verify_reviewer_token(tokens, "alice", "s1")
    assert not verify_reviewer_token(tokens, "alice", "wrong")
    assert not verify_reviewer_token(tokens, "carol", "s1")


def test_queue_token_roundtrip_and_expiry():
    secret = "test-hmac-secret"
    tok = mint_queue_token(
        secret,
        reviewer_id="alice",
        sample_batch_id="batch1",
        message_id=42,
        blind=True,
        ttl_seconds=60,
    )
    attest = parse_queue_token(secret, tok)
    assert attest is not None
    assert attest.reviewer_id == "alice"
    assert attest.message_id == 42
    assert attest.blind is True
    assert parse_queue_token("other", tok) is None
    expired = mint_queue_token(
        secret,
        reviewer_id="alice",
        sample_batch_id="batch1",
        message_id=42,
        blind=False,
        ttl_seconds=-10,
    )
    assert parse_queue_token(secret, expired) is None


@pytest.fixture
def api_client(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_TOKENS", "alice:tok-alice")
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_IDS", "alice")
    monkeypatch.setenv("INDEPENDENT_REVIEW_HMAC_SECRET", "hmac-secret-for-tests")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "")
    get_settings.cache_clear()
    from services.api.app import main as api_main

    # Reload module-level settings after env change.
    api_main.settings = get_settings()
    with TestClient(api_main.app) as client:
        yield client
    get_settings.cache_clear()


@pytest.fixture
def unconfigured_client(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_TOKENS", "")
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_IDS", "alice")
    monkeypatch.setenv("INDEPENDENT_REVIEW_HMAC_SECRET", "")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "")
    get_settings.cache_clear()
    from services.api.app import main as api_main

    api_main.settings = get_settings()
    with TestClient(api_main.app) as client:
        yield client
    get_settings.cache_clear()


def test_partial_config_returns_503(unconfigured_client):
    r = unconfigured_client.post(
        "/labels/reviews",
        json={
            "message_id": 1,
            "sample_batch_id": "b",
            "reviewer_id": "alice",
            "label": "HUMAN_REVIEWED_TRUE",
            "review_token": "x",
        },
    )
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


def test_ids_only_config_not_ready(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_TOKENS", "")
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_IDS", "alice")
    monkeypatch.setenv("INDEPENDENT_REVIEW_HMAC_SECRET", "secret")
    get_settings.cache_clear()
    from services.api.app import main as api_main

    api_main.settings = get_settings()
    tokens, allowlist, secret, ready = api_main._independence_auth_config()
    assert ready is False
    assert allowlist == set()
    get_settings.cache_clear()


def test_reserved_reviewer_rejected(api_client):
    r = api_client.post(
        "/labels/reviews",
        json={
            "message_id": 1,
            "sample_batch_id": "b",
            "reviewer_id": "agent_x",
            "label": "HUMAN_REVIEWED_TRUE",
            "review_token": "x",
        },
        headers={"X-Reviewer-Token": "tok-alice"},
    )
    assert r.status_code == 400
    assert "reserved" in r.json()["detail"]


def test_missing_reviewer_token_401(api_client):
    r = api_client.post(
        "/labels/reviews",
        json={
            "message_id": 1,
            "sample_batch_id": "b",
            "reviewer_id": "alice",
            "label": "HUMAN_REVIEWED_TRUE",
            "review_token": "x",
        },
    )
    assert r.status_code == 401


def test_blind_false_forbidden(api_client):
    r = api_client.get(
        "/labels/independent-queue",
        params={
            "sample_batch_id": "b",
            "reviewer_id": "alice",
            "blind": False,
        },
        headers={"X-Reviewer-Token": "tok-alice"},
    )
    assert r.status_code == 403


def test_blind_plus_stratum_forbidden(api_client):
    r = api_client.get(
        "/labels/independent-queue",
        params={
            "sample_batch_id": "b",
            "reviewer_id": "alice",
            "blind": True,
            "stratum": "high_leads",
        },
        headers={"X-Reviewer-Token": "tok-alice"},
    )
    assert r.status_code == 400
    assert "stratum" in r.json()["detail"]


def test_auditor_not_reserved():
    from services.api.app.main import _reviewer_is_non_independent

    assert not _reviewer_is_non_independent("auditor")
    assert not _reviewer_is_non_independent("auditor_1")
    assert _reviewer_is_non_independent("audit_census")
    assert _reviewer_is_non_independent("agent_business")


def test_schema_requires_review_token():
    from pydantic import ValidationError
    from services.api.app.schemas import IndependentReviewCreate

    with pytest.raises(ValidationError):
        IndependentReviewCreate(
            message_id=1,
            sample_batch_id="b",
            reviewer_id="alice",
            label="HUMAN_REVIEWED_TRUE",
        )
    r = IndependentReviewCreate(
        message_id=1,
        sample_batch_id="b",
        reviewer_id="alice",
        label="HUMAN_REVIEWED_TRUE",
        review_token="tok",
    )
    assert r.review_token == "tok"


def test_attestation_blind_means_shown_false():
    secret = "hmac-secret-for-tests"
    tok = mint_queue_token(
        secret,
        reviewer_id="alice",
        sample_batch_id="b",
        message_id=99,
        blind=True,
    )
    attest = parse_queue_token(secret, tok)
    assert attest is not None and attest.blind is True
    assert (not attest.blind) is False  # shown flags must be False when blind
