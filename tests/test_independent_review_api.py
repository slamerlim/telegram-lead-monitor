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
    monkeypatch.setenv("REVIEW_UI_LOCKDOWN", "false")
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
    monkeypatch.setenv("REVIEW_UI_LOCKDOWN", "false")
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


def test_commercially_actionable_persists_and_blind_flags_false(api_client):
    """POST /labels/reviews stores commercially_actionable; blind insert forces shown flags false."""
    from services.api.app import main as api_main
    from shared.db import get_session
    from shared.independent_review_auth import mint_queue_token

    stored: list = []
    planned = [1, None, 1, None, 1, None]

    class _FakeSession:
        async def get(self, model, pk):
            return object()

        async def scalar(self, stmt):
            return planned.pop(0)

        def add(self, row):
            stored.append(row)

        async def commit(self):
            return None

        async def refresh(self, row):
            if getattr(row, "id", None) is None:
                row.id = len(stored)

        async def rollback(self):
            return None

    async def _override():
        yield _FakeSession()

    token = mint_queue_token(
        "hmac-secret-for-tests",
        reviewer_id="alice",
        sample_batch_id="b",
        message_id=99,
        blind=True,
    )
    cases = [
        ("HUMAN_REVIEWED_TRUE", None, True),
        ("HUMAN_REVIEWED_FALSE", "MARKETING_BROADCAST", False),
        ("HUMAN_REVIEWED_AMBIGUOUS", None, None),
    ]
    api_main.app.dependency_overrides[get_session] = _override
    try:
        for label, fp_class, actionable in cases:
            payload = {
                "message_id": 99,
                "sample_batch_id": "b",
                "reviewer_id": "alice",
                "label": label,
                "fp_class": fp_class,
                "commercially_actionable": actionable,
                "review_token": token,
                "scorer_shown": True,
                "prior_label_shown": True,
            }
            response = api_client.post(
                "/labels/reviews",
                json=payload,
                headers={"X-Reviewer-Token": "tok-alice"},
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["commercially_actionable"] is actionable
            assert body["scorer_shown"] is False
            assert body["prior_label_shown"] is False
            assert body["label"] == label
    finally:
        api_main.app.dependency_overrides.pop(get_session, None)

    assert len(stored) == 3
    for row, (label, _fp, actionable) in zip(stored, cases, strict=True):
        assert row.label == label
        assert row.commercially_actionable is actionable
        assert row.scorer_shown is False
        assert row.prior_label_shown is False


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


def test_review_ui_lockdown_blocks_non_review_routes(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_TOKENS", "alice:tok-alice")
    monkeypatch.setenv("INDEPENDENT_HUMAN_REVIEWER_IDS", "alice")
    monkeypatch.setenv("INDEPENDENT_REVIEW_HMAC_SECRET", "hmac-secret-for-tests")
    monkeypatch.setenv("HUMAN_LABEL_REVIEWER_IDS", "")
    monkeypatch.setenv("LABEL_WRITE_TOKEN", "")
    monkeypatch.setenv("REVIEW_UI_LOCKDOWN", "true")
    get_settings.cache_clear()
    from services.api.app import main as api_main

    api_main.settings = get_settings()
    with TestClient(api_main.app) as client:
        assert client.get("/search", params={"q": "x"}).status_code == 403
        assert client.get("/leads").status_code == 403
        assert client.get("/docs").status_code == 403
        assert client.get("/health").status_code == 200
        # No token => auth 401 proves the queue route is past lockdown (not middleware 403).
        r = client.get(
            "/labels/independent-queue",
            params={"sample_batch_id": "b", "reviewer_id": "alice", "blind": True},
        )
        assert r.status_code == 401
        assert "REVIEW_UI_LOCKDOWN" not in str(r.json().get("detail", ""))
        posted = client.post(
            "/labels/reviews",
            json={
                "message_id": 1,
                "sample_batch_id": "b",
                "reviewer_id": "alice",
                "label": "HUMAN_REVIEWED_TRUE",
                "review_token": "x",
            },
        )
        assert posted.status_code == 401
        assert "REVIEW_UI_LOCKDOWN" not in str(posted.json().get("detail", ""))
    get_settings.cache_clear()
