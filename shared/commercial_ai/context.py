"""Bounded commercial context builder (PostgreSQL-only; no Telegram API)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.commercial_ai.discovery import (
    context_hash_parts,
    evaluate_discovery,
    fingerprints_compatible,
)
from shared.models import LabelReviewSample, Message
from shared.commercial_ai.store import text_sha256

CONTEXT_VERSION = "ctx_v1"
RELATION_SEED = "SEED"
RELATION_REPLY_PARENT = "REPLY_PARENT"
RELATION_REPLY_CHILD = "REPLY_CHILD"
RELATION_SAME_AUTHOR_24H = "SAME_AUTHOR_24H"
RELATION_SAME_AUTHOR_HISTORY = "SAME_AUTHOR_RELEVANT_HISTORY"
RELATION_COMMUNITY = "COMMUNITY_CONTEXT"


@dataclass
class ContextMember:
    message_id: int
    telegram_message_id: int
    author_id: int | None
    text: str
    message_date: datetime
    relation: str
    selection_reason: str
    text_sha256: str
    is_seed_author: bool


@dataclass
class CommercialContext:
    seed_message_id: int
    seed_author_id: int | None
    community_id: int
    context_version: str
    context_hash: str
    members: list[ContextMember]
    n_excluded: int = 0
    n_excluded_blind: int = 0
    include_related: bool = False
    topic_fingerprint: str = ""

    def allowed_message_ids(self) -> set[int]:
        return {m.message_id for m in self.members}

    def seed_author_message_ids(self) -> set[int]:
        return {m.message_id for m in self.members if m.is_seed_author}

    def to_members_json(self) -> list[dict[str, Any]]:
        return [
            {
                "message_id": m.message_id,
                "telegram_message_id": m.telegram_message_id,
                "author_id": m.author_id,
                "relation": m.relation,
                "selection_reason": m.selection_reason,
                "text_sha256": m.text_sha256,
                "is_seed_author": m.is_seed_author,
                "message_date": m.message_date.isoformat(),
            }
            for m in self.members
        ]


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def build_commercial_context(
    session: AsyncSession,
    seed_message_id: int,
    *,
    include_related: bool = True,
    scorer: Any | None = None,
    max_messages: int = 20,
    max_chars: int = 12000,
    context_version: str = CONTEXT_VERSION,
) -> CommercialContext:
    seed = await session.get(Message, seed_message_id)
    if not seed:
        raise ValueError(f"seed message {seed_message_id} not found")

    seed_fp = ""
    if scorer is not None:
        feats = evaluate_discovery(scorer, seed.text or "")
        seed_fp = feats.topic_fingerprint

    members: dict[int, ContextMember] = {}
    n_excluded = 0
    n_blind = 0

    def _add(msg: Message, relation: str, reason: str) -> bool:
        nonlocal n_excluded, n_blind
        if msg.id in members:
            return False
        if len(members) >= max_messages:
            n_excluded += 1
            return False
        members[msg.id] = ContextMember(
            message_id=msg.id,
            telegram_message_id=msg.telegram_message_id,
            author_id=msg.author_id,
            text=msg.text or "",
            message_date=_aware(msg.message_date),
            relation=relation,
            selection_reason=reason,
            text_sha256=text_sha256(msg.text or ""),
            is_seed_author=(
                seed.author_id is not None and msg.author_id == seed.author_id
            ),
        )
        return True

    _add(seed, RELATION_SEED, "seed_message")

    if include_related:
        # Reply parent (telegram id within same community).
        if seed.reply_to_telegram_message_id is not None:
            parent = await session.scalar(
                select(Message).where(
                    Message.community_id == seed.community_id,
                    Message.telegram_message_id == seed.reply_to_telegram_message_id,
                )
            )
            if parent and parent.id != seed.id:
                _add(parent, RELATION_REPLY_PARENT, "THREAD_PARENT")

        # Reply children pointing at seed's telegram id.
        children = (
            await session.scalars(
                select(Message)
                .where(
                    Message.community_id == seed.community_id,
                    Message.reply_to_telegram_message_id == seed.telegram_message_id,
                )
                .order_by(Message.message_date.asc())
                .limit(8)
            )
        ).all()
        for ch in children:
            if ch.id == seed.id:
                continue
            _add(ch, RELATION_REPLY_CHILD, "THREAD_CHILD")

        seed_dt = _aware(seed.message_date)
        if seed.author_id is not None:
            win_24h = seed_dt - timedelta(hours=24)
            same_24 = (
                await session.scalars(
                    select(Message)
                    .where(
                        Message.author_id == seed.author_id,
                        Message.community_id == seed.community_id,
                        Message.id != seed.id,
                        Message.message_date >= win_24h,
                        Message.message_date <= seed_dt + timedelta(hours=1),
                    )
                    .order_by(Message.message_date.desc())
                    .limit(30)
                )
            ).all()
            for msg in same_24:
                if scorer is not None:
                    other_fp = evaluate_discovery(scorer, msg.text or "").topic_fingerprint
                    if seed_fp and other_fp and not fingerprints_compatible(seed_fp, other_fp):
                        n_excluded += 1
                        continue
                _add(msg, RELATION_SAME_AUTHOR_24H, "SAME_AUTHOR_24H")

            # Historical 30d — only messages with compatible fingerprint / commercial signal.
            hist_start = seed_dt - timedelta(days=30)
            hist = (
                await session.scalars(
                    select(Message)
                    .where(
                        Message.author_id == seed.author_id,
                        Message.community_id == seed.community_id,
                        Message.id != seed.id,
                        Message.message_date >= hist_start,
                        Message.message_date < win_24h,
                    )
                    .order_by(Message.message_date.desc())
                    .limit(40)
                )
            ).all()
            for msg in hist:
                if scorer is None:
                    continue
                feats = evaluate_discovery(scorer, msg.text or "")
                if not feats.eligible and not fingerprints_compatible(seed_fp, feats.topic_fingerprint):
                    n_excluded += 1
                    continue
                if seed_fp and feats.topic_fingerprint and not fingerprints_compatible(
                    seed_fp, feats.topic_fingerprint
                ):
                    n_excluded += 1
                    continue
                _add(msg, RELATION_SAME_AUTHOR_HISTORY, "SAME_AUTHOR_RELEVANT_HISTORY")

        # Drop blind-review samples from supporting context (never from seed itself).
        support_ids = [mid for mid in members if mid != seed.id]
        if support_ids:
            blind_ids = set(
                (
                    await session.scalars(
                        select(LabelReviewSample.message_id).where(
                            LabelReviewSample.message_id.in_(support_ids)
                        )
                    )
                ).all()
            )
            for mid in list(members):
                if mid in blind_ids and mid != seed.id:
                    del members[mid]
                    n_blind += 1
                    n_excluded += 1

    # Cap by character budget: keep seed + highest-priority relations.
    ordered = sorted(
        members.values(),
        key=lambda m: (
            0 if m.relation == RELATION_SEED else 1,
            0 if m.relation in (RELATION_REPLY_PARENT, RELATION_REPLY_CHILD) else 1,
            0 if m.relation == RELATION_SAME_AUTHOR_24H else 1,
            abs((_aware(m.message_date) - _aware(seed.message_date)).total_seconds()),
        ),
    )
    kept: list[ContextMember] = []
    chars = 0
    for m in ordered:
        add_len = len(m.text or "")
        if kept and chars + add_len > max_chars:
            n_excluded += 1
            continue
        kept.append(m)
        chars += add_len
        if len(kept) >= max_messages:
            break

    # Chronological order; seed remains identifiable via relation.
    kept.sort(key=lambda m: _aware(m.message_date))

    hash_members = [
        {
            "message_id": m.message_id,
            "relation": m.relation,
            "text_sha256": m.text_sha256,
        }
        for m in kept
    ]
    chash = context_hash_parts(
        seed_id=seed.id,
        context_version=context_version,
        members=hash_members,
    )
    return CommercialContext(
        seed_message_id=seed.id,
        seed_author_id=seed.author_id,
        community_id=seed.community_id,
        context_version=context_version,
        context_hash=chash,
        members=kept,
        n_excluded=n_excluded,
        n_excluded_blind=n_blind,
        include_related=include_related,
        topic_fingerprint=seed_fp,
    )
