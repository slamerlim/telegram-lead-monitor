"""Deterministic commercial opportunity identity.

Opportunity unit for CRM/dedup is (text_hash, community_id), not message_id.
Message-level uniqueness on leads.message_id is preserved separately.
"""

from __future__ import annotations

import hashlib


def normalize_message_text(text: str) -> str:
    return " ".join((text or "").split())


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_message_text(text).encode("utf-8")).hexdigest()


def opportunity_key(community_id: int, text: str) -> str:
    return f"{int(community_id)}:{text_hash(text)}"
