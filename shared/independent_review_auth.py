"""Auth + queue attestation for independent human reviews."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass


def parse_id_csv(raw: str | None) -> set[str]:
    if not raw or not raw.strip():
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def parse_reviewer_tokens(raw: str | None) -> dict[str, str]:
    """Parse `id:token,id2:token2` into a mapping. Duplicate ids → last wins."""
    out: dict[str, str] = {}
    if not raw or not raw.strip():
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        rid, token = part.split(":", 1)
        rid, token = rid.strip(), token.strip()
        if rid and token:
            out[rid] = token
    return out


def verify_reviewer_token(tokens: dict[str, str], reviewer_id: str, token: str | None) -> bool:
    expected = tokens.get(reviewer_id)
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, token)


@dataclass(frozen=True)
class QueueAttestation:
    reviewer_id: str
    sample_batch_id: str
    message_id: int
    blind: bool
    exp: int


def mint_queue_token(
    secret: str,
    *,
    reviewer_id: str,
    sample_batch_id: str,
    message_id: int,
    blind: bool,
    ttl_seconds: int = 86_400,
) -> str:
    exp = int(time.time()) + ttl_seconds
    body = f"v1|{reviewer_id}|{sample_batch_id}|{message_id}|{1 if blind else 0}|{exp}"
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def parse_queue_token(secret: str, token: str | None) -> QueueAttestation | None:
    if not secret or not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    parts = body.split("|")
    if len(parts) != 6 or parts[0] != "v1":
        return None
    _, reviewer_id, sample_batch_id, mid_s, blind_s, exp_s = parts
    try:
        message_id = int(mid_s)
        exp = int(exp_s)
        blind = blind_s == "1"
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    return QueueAttestation(
        reviewer_id=reviewer_id,
        sample_batch_id=sample_batch_id,
        message_id=message_id,
        blind=blind,
        exp=exp,
    )


def reserved_id_match(value: str, prefixes: tuple[str, ...]) -> bool:
    """True when value equals a reserved prefix or uses prefix_ as a delimiter.

    Avoids rejecting legitimate ids like ``auditor`` for prefix ``audit``.
    """
    rid = value.lower()
    for p in prefixes:
        pl = p.lower()
        if rid == pl or rid.startswith(pl + "_"):
            return True
    return False


def like_prefix_patterns(prefix: str) -> tuple[str, str]:
    """Return (exact, delimited) ILIKE patterns with SQL wildcards escaped."""
    esc = prefix.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return esc, esc + r"\_%"
