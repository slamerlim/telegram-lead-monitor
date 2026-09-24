from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "telegram_leads"
    postgres_user: str = "telegram_leads"
    postgres_password: str = "change-me"

    redis_url: str = "redis://redis:6379/0"

    telegram_api_id: int
    telegram_api_hash: str
    telegram_phone: str
    telegram_session_path: str = "/data/telegram/telegram_lead_monitor.session"

    collector_concurrency: int = 3
    collector_batch_size: int = 200
    collector_flood_wait_buffer_seconds: int = 5
    scan_default_days: int = 30
    scheduler_interval_seconds: int = 900
    scheduler_scan_days: int = 2

    semantic_enabled: bool = False
    semantic_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    semantic_threshold: float = 0.48
    scoring_config: str = "/app/config/scoring.yaml"
    # Comma-separated reviewer_ids that may satisfy ml_gate_independence_ready.
    # Empty (default) => independence gate never opens (safe until humans are allowlisted).
    independent_human_reviewer_ids: str = ""
    # id:token pairs required for independent queue/write when non-empty.
    # Format: alice:secret1,bob:secret2
    independent_human_reviewer_tokens: str = ""
    # HMAC secret for queue attestations that bind blind mode to POST /labels/reviews.
    # Empty => independence gate never opens (blind flags cannot be server-proven).
    independent_review_hmac_secret: str = ""
    # Comma-separated labeled_by values that count toward M1/positive gates.
    # Empty (default) => those gates never open (client cannot inflate via arbitrary labeled_by).
    human_label_reviewer_ids: str = ""
    # Optional shared secret for POST /labels. Empty => writes allowed without header.
    label_write_token: str = ""
    # When true, only review UI + independent-review endpoints + health are reachable.
    # Enable during human blind-review sessions to reduce same-origin scorer/search leaks.
    review_ui_lockdown: bool = False
    # Kill switch for commercial operator API (/ops/*). Default off until configured.
    commercial_ops_enabled: bool = False
    # Optional dedicated commercial operator ids (comma-separated).
    commercial_operator_ids: str = ""
    ops_candidate_max_age_days: int = 90

    # --- Commercial AI adjudication (separate from independent AI validation) ---
    commercial_ai_enabled: bool = False
    commercial_ai_auto_promote: bool = False
    commercial_ai_backend: str = "fake"  # fake | sdk
    commercial_ai_max_reviews_per_hour: int = 60
    commercial_ai_max_autopromotions_per_hour: int = 10
    commercial_ai_reviewers_config: str = "/app/config/commercial_ai_reviewers.yaml"
    commercial_ai_confidence_floor: float = 0.75

    # --- Commercial discovery / episode shadow (isolated from CRM + 0007) ---
    commercial_discovery_enabled: bool = False
    commercial_episode_shadow_enabled: bool = False
    commercial_discovery_max_candidates_per_hour: int = 120
    commercial_episode_max_reviews_per_hour: int = 40
    commercial_episode_max_context_messages: int = 20
    commercial_episode_max_context_chars: int = 12000
    commercial_discovery_version: str = "disc_v3"
    commercial_context_version: str = "ctx_v1"

    # --- Cursor-only AI validation (no third-party LLM APIs) ---
    ai_validators_config: str = "/app/config/ai_validators.yaml"
    ai_validator_ids: str = ""
    ai_validator_tokens: str = ""
    ai_validation_queue_hmac_secret: str = ""
    ai_validation_row_secret: str = ""
    ai_validation_require_distinct_families: bool = True
    ai_validation_gate_min_messages: int = 100
    ai_validation_gate_min_true: int = 30
    cursor_api_key: str = ""  # orchestrator only; never required for API

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        return self.database_url.replace("+asyncpg", "")


@lru_cache

def get_settings() -> Settings:
    return Settings()


def scoring_path(settings: Settings) -> Path:
    return Path(settings.scoring_config)
