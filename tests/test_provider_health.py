from shared.provider_health import classify_sdk_error, record_sdk_health, sdk_health
from shared.settings import get_settings


def test_sdk_health_invalidates_after_credential_change_and_never_stores_key(tmp_path, monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "telegram_session_path", str(tmp_path / "telegram/monitor.session"))
    monkeypatch.setattr(cfg, "cursor_api_key", "synthetic-private-key-one")
    record_sdk_health("blocked_plan")
    assert sdk_health()["ai_status"] == "blocked_plan"
    assert "synthetic-private-key-one" not in (tmp_path / "config/sdk-health.json").read_text()
    monkeypatch.setattr(cfg, "cursor_api_key", "synthetic-private-key-two")
    assert sdk_health()["ai_status"] == "unverified"
    assert classify_sdk_error("internal: [plan_required]") == "blocked_plan"
