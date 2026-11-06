from shared.settings import Settings


def test_credentials_are_absent_from_settings_repr():
    settings = Settings(
        _env_file=None, telegram_api_id=1, telegram_api_hash="private-telegram-hash",
        telegram_phone="private-phone", cursor_api_key="private-cursor-key",
        postgres_password="private-db-password", label_write_token="private-label-token",
        independent_human_reviewer_tokens="alice:private-review-token",
        ai_validator_tokens="aival_a:private-ai-token",
    )
    assert "private-" not in repr(settings)
