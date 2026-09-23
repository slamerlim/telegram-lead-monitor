"""AI validator registry (Cursor models only) + row/task attestation helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from shared.independent_review_auth import parse_id_csv, parse_reviewer_tokens, reserved_id_match


AI_PREFIX = "aival"
REQUIRED_MODES = ("A", "B", "C")


@dataclass(frozen=True)
class ValidatorSlot:
    id: str
    mode: str
    provider: str  # always "cursor" for this design
    model: str
    model_family: str
    prompt_version: str
    enabled: bool = True


class RegistryError(ValueError):
    pass


def load_validator_config(path: Path | str) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RegistryError("ai_validators.yaml must be a mapping")
    return raw


def parse_slots(config: dict[str, Any]) -> list[ValidatorSlot]:
    slots_raw = config.get("validators") or []
    out: list[ValidatorSlot] = []
    for item in slots_raw:
        out.append(
            ValidatorSlot(
                id=str(item["id"]).strip(),
                mode=str(item["mode"]).strip().upper(),
                provider=str(item.get("provider", "cursor")).strip().lower(),
                model=str(item["model"]).strip(),
                model_family=str(item["model_family"]).strip().lower(),
                prompt_version=str(item.get("prompt_version", "cursor_ai_val_v1")).strip(),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return out


def validate_registry(
    slots: list[ValidatorSlot],
    *,
    require_distinct_families: bool = True,
) -> list[str]:
    """Return closed reasons (empty if OK)."""
    reasons: list[str] = []
    enabled = [s for s in slots if s.enabled]
    if not enabled:
        return ["ai_auth_not_configured"]
    for s in enabled:
        if s.provider != "cursor":
            reasons.append("non_cursor_provider_forbidden")
        if not s.id.startswith(f"{AI_PREFIX}_"):
            reasons.append("ai_id_prefix_invalid")
        if reserved_id_match(s.id, ("agent", "audit", "blind_adjudicator", "smoke", "phaseD_smoke", "prod_phase0")):
            reasons.append("ai_id_reserved_collision")
    modes = {s.mode for s in enabled if s.mode in REQUIRED_MODES}
    if modes != set(REQUIRED_MODES):
        reasons.append("ai_modes_missing")
    # One slot per mode among A/B/C
    for m in REQUIRED_MODES:
        if sum(1 for s in enabled if s.mode == m) != 1:
            reasons.append(f"ai_mode_{m}_slot_count")
    models = [s.model for s in enabled if s.mode in REQUIRED_MODES]
    if len(models) != len(set(models)):
        reasons.append("ai_duplicate_models")
    families = [s.model_family for s in enabled if s.mode in REQUIRED_MODES]
    if require_distinct_families and len(set(families)) < 3:
        reasons.append("ai_family_diversity<3")
    return sorted(set(reasons))


def ai_allowlist_ready(
    *,
    ids_csv: str,
    tokens_csv: str,
    queue_secret: str,
    row_secret: str,
    slots: list[ValidatorSlot],
) -> tuple[set[str], dict[str, str], bool, list[str]]:
    tokens = parse_reviewer_tokens(tokens_csv)
    ids = parse_id_csv(ids_csv)
    enabled_ids = {s.id for s in slots if s.enabled}
    if ids:
        allow = ids & set(tokens.keys()) & enabled_ids
    else:
        allow = set(tokens.keys()) & enabled_ids
    reasons: list[str] = []
    if not tokens or not (queue_secret or "").strip() or not (row_secret or "").strip() or not allow:
        reasons.append("ai_auth_not_configured")
        return set(), tokens, False, reasons
    return allow, tokens, True, reasons


def config_version(slot: ValidatorSlot, prompt_body: str) -> str:
    payload = f"{slot.id}|{slot.mode}|{slot.provider}|{slot.model}|{slot.model_family}|{slot.prompt_version}|{prompt_body}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mint_ai_task_token(
    secret: str,
    *,
    reviewer_id: str,
    sample_batch_id: str,
    message_id: int,
    mode: str,
    peer_shown: bool,
    ttl_seconds: int = 86_400,
) -> str:
    import time

    exp = int(time.time()) + ttl_seconds
    body = (
        f"v2|{reviewer_id}|{sample_batch_id}|{message_id}|1|"
        f"{mode}|{1 if peer_shown else 0}|{exp}"
    )
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


@dataclass(frozen=True)
class AiTaskAttestation:
    reviewer_id: str
    sample_batch_id: str
    message_id: int
    mode: str
    peer_shown: bool
    exp: int


def parse_ai_task_token(secret: str, token: str | None) -> AiTaskAttestation | None:
    import time

    if not secret or not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    parts = body.split("|")
    if len(parts) != 8 or parts[0] != "v2":
        return None
    _, reviewer_id, sample_batch_id, mid_s, blind_s, mode, peer_s, exp_s = parts
    if blind_s != "1":
        return None
    try:
        message_id = int(mid_s)
        exp = int(exp_s)
        peer_shown = peer_s == "1"
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    return AiTaskAttestation(
        reviewer_id=reviewer_id,
        sample_batch_id=sample_batch_id,
        message_id=message_id,
        mode=mode.upper(),
        peer_shown=peer_shown,
        exp=exp,
    )


def sign_ai_row(secret: str, fields: dict[str, Any]) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_ai_row(secret: str, fields: dict[str, Any], sig: str | None) -> bool:
    if not secret or not sig:
        return False
    return hmac.compare_digest(sign_ai_row(secret, fields), sig)
