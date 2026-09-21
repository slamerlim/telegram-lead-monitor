"""Shared pytest fixtures. Set required settings before service imports."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("TELEGRAM_API_ID", "123456")
os.environ.setdefault("TELEGRAM_API_HASH", "test-hash")
os.environ.setdefault("TELEGRAM_PHONE", "+10000000000")
os.environ.setdefault(
    "SCORING_CONFIG",
    str(ROOT / "config" / "scoring.yaml"),
)

# Clear cached settings if something imported Settings earlier.
try:
    from shared.settings import get_settings

    get_settings.cache_clear()
except Exception:
    pass
