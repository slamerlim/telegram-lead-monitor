"""Small private SDK health snapshot; never stores response bodies or credentials."""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from shared.settings import get_settings


def _path() -> Path:
    return Path(get_settings().telegram_session_path).parent.parent / "config/sdk-health.json"


def classify_sdk_error(detail: str | None) -> str:
    value = (detail or "").lower()
    if "plan_required" in value:
        return "blocked_plan"
    if "unauthorized" in value or "invalid api key" in value or "401" in value:
        return "blocked_credentials"
    return "error"


def record_sdk_health(status: str, *, models: list[dict] | None = None) -> None:
    cfg = get_settings()
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Fingerprint is private and only invalidates health when the key changes.
    data = {"status": status, "checked_at": datetime.now(timezone.utc).isoformat(),
            "credential_fingerprint": hashlib.sha256(cfg.cursor_api_key.encode()).hexdigest(),
            "models": models or []}
    fd, temporary = tempfile.mkstemp(prefix=".sdk-health-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def sdk_health() -> dict:
    result = {"ai_status": "unverified", "ai_detail": "Cursor SDK review has not been verified", "ai_checked_at": None}
    try:
        data = json.loads(_path().read_text())
        fingerprint = hashlib.sha256(get_settings().cursor_api_key.encode()).hexdigest()
        checked = datetime.fromisoformat(data["checked_at"])
        if data.get("credential_fingerprint") != fingerprint or datetime.now(timezone.utc) - checked > timedelta(hours=24):
            return result
        status = data.get("status", "unverified")
        details = {"ok": "Cursor SDK connectivity checked; lead quality still requires owner feedback",
                   "blocked_plan": "Cursor SDK account requires plan access",
                   "blocked_credentials": "Cursor SDK credential was rejected",
                   "error": "Cursor SDK review is unavailable; inspect the preflight result"}
        return {"ai_status": status if status in details else "unverified",
                "ai_detail": details.get(status, result["ai_detail"]), "ai_checked_at": data["checked_at"]}
    except (OSError, ValueError, KeyError, TypeError):
        return result
