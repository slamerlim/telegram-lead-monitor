"""Paired A/B commercial episode shadow worker — NO CRM promotion / NO outreach."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

import yaml
from sqlalchemy import func, select

from services.analyzer.app.scoring import LeadScorer
from services.validator.app.providers.cursor_backend import FakeCursorBackend, SdkCursorBackend
from shared.commercial_ai.context import build_commercial_context
from shared.commercial_ai.evidence import validate_episode_evidence
from shared.commercial_ai.policy import decide_commercial, opinion_from_payload
from shared.commercial_ai.prompts import (
    EPISODE_PROMPT_VERSION,
    PROMPT_VERSION,
    build_commercial_mode_prompt,
    build_episode_mode_prompt,
)
from shared.commercial_ai.store import (
    already_episode_arm_decided,
    record_episode_opinion,
    record_episode_shadow_decision,
    text_sha256,
)
from shared.commercial_ai.streams import (
    COMMERCIAL_EPISODE_SHADOW_DLQ,
    COMMERCIAL_EPISODE_SHADOW_GROUP,
    COMMERCIAL_EPISODE_SHADOW_STREAM,
)
from shared.db import SessionLocal
from shared.logging import configure_logging
from shared.models import CommercialAIReview, LabelReviewSample, Lead, Message
from shared.redis_bus import RedisBus
from shared.settings import get_settings
from shared.validation.blind_input import BlindItem

logger = logging.getLogger("commercial_episode_shadow")
MAX_DELIVERY = 5


def _load_roster(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    slots = [v for v in (raw.get("validators") or []) if v.get("enabled", True)]
    modes = {s.get("mode") for s in slots}
    if not {"A", "B", "C"}.issubset(modes):
        raise RuntimeError("commercial AI roster requires enabled A/B/C slots")
    return slots


def _backend():
    settings = get_settings()
    kind = (settings.commercial_ai_backend or "fake").lower()
    if kind == "sdk":
        key = (settings.cursor_api_key or "").strip()
        if not key:
            raise RuntimeError("CURSOR_API_KEY required when COMMERCIAL_AI_BACKEND=sdk")
        cwd = str(Path("/app") if Path("/app").exists() else Path.cwd())
        return SdkCursorBackend(api_key=key, cwd=cwd)
    return FakeCursorBackend()


async def _episode_reviews_last_hour(session) -> int:
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    return int(
        (
            await session.scalar(
                select(func.count(CommercialAIReview.id)).where(
                    CommercialAIReview.row_kind == "DECISION",
                    CommercialAIReview.experiment_arm.isnot(None),
                    CommercialAIReview.created_at >= since,
                )
            )
        )
        or 0
    )


async def process_shadow(payload: dict, backend, scorer: LeadScorer) -> None:
    settings = get_settings()
    if not settings.commercial_episode_shadow_enabled:
        return

    seed_id = int(payload["seed_message_id"])
    arm = str(payload.get("experiment_arm") or "A").upper()
    include_related = bool(payload.get("include_related", arm == "B"))
    episode_id = payload.get("episode_id")
    episode_id_i = int(episode_id) if episode_id is not None else None

    async with SessionLocal() as session:
        message = await session.get(Message, seed_id)
        if not message:
            return
        if await session.scalar(
            select(LabelReviewSample.id).where(LabelReviewSample.message_id == seed_id).limit(1)
        ):
            return
        if await session.scalar(
            select(Lead.id).where(Lead.message_id == seed_id, Lead.status == "AI_CONFIRMED").limit(1)
        ):
            return

        max_rev = int(settings.commercial_episode_max_reviews_per_hour or 0)
        if max_rev > 0 and await _episode_reviews_last_hour(session) >= max_rev:
            logger.warning("episode_shadow rate-limit seed=%s", seed_id)
            return

        ctx = await build_commercial_context(
            session,
            seed_id,
            include_related=include_related,
            scorer=scorer,
            max_messages=int(settings.commercial_episode_max_context_messages or 20),
            max_chars=int(settings.commercial_episode_max_context_chars or 12000),
            context_version=settings.commercial_context_version or "ctx_v1",
        )
        prompt_ver = EPISODE_PROMPT_VERSION if include_related else PROMPT_VERSION
        prior = await already_episode_arm_decided(
            session,
            seed_message_id=seed_id,
            context_hash=ctx.context_hash,
            experiment_arm=arm,
            prompt_version=prompt_ver,
        )
        if prior:
            return

        cfg_path = Path(settings.commercial_ai_reviewers_config)
        if not cfg_path.exists():
            cfg_path = Path("config/commercial_ai_reviewers.yaml")
        slots = _load_roster(cfg_path)
        run_id = f"cep_{arm}_{uuid.uuid4().hex[:12]}"
        text_hash = text_sha256(message.text)
        scorer_meta = {
            "tier": "LOW",
            "lead_type": None,
            "buyer_type": None,
            "score": None,
        }

        members_for_prompt = [
            {
                "message_id": m.message_id,
                "relation": m.relation,
                "is_seed_author": m.is_seed_author,
                "text": m.text,
            }
            for m in ctx.members
        ]

        opinions = []
        last_payload = None
        for slot in slots:
            mode = str(slot["mode"]).upper()
            t0 = time.monotonic()
            if include_related:
                prompt = build_episode_mode_prompt(
                    mode,
                    seed_message_id=seed_id,
                    members=members_for_prompt,
                    scorer_meta=scorer_meta,
                    scoring_yaml=settings.scoring_config,
                    arm=arm,
                )
            else:
                item = BlindItem(text=message.text, nonce=run_id)
                prompt = build_commercial_mode_prompt(
                    mode, item, scoring_yaml=settings.scoring_config, scorer_meta=scorer_meta
                )
            result = await asyncio.to_thread(
                backend.validate, model=str(slot["model"]), prompt=prompt
            )
            latency = int((time.monotonic() - t0) * 1000)
            await record_episode_opinion(
                session,
                run_id=run_id,
                message=message,
                text_hash=text_hash,
                slot_id=str(slot["id"]),
                mode=mode,
                model=str(slot["model"]),
                model_family=str(slot.get("model_family") or ""),
                status=result.status,
                payload=result.structured,
                synthetic=bool(result.synthetic),
                request_id=result.request_id,
                latency_ms=latency,
                raw=getattr(result, "raw", None),
                episode_id=episode_id_i,
                context_version=ctx.context_version,
                context_hash=ctx.context_hash,
                experiment_arm=arm,
                prompt_version=prompt_ver,
            )
            ov = opinion_from_payload(
                mode=mode,
                status=result.status,
                payload=result.structured,
                synthetic=bool(result.synthetic),
                model_family=str(slot.get("model_family") or ""),
                confidence_floor=float(settings.commercial_ai_confidence_floor or 0.75),
            )
            # Map episode_commercial onto commercial for decide_commercial when present.
            if result.structured and "episode_commercial" in (result.structured or {}):
                patched = dict(result.structured)
                if patched.get("episode_commercial") is True:
                    patched["is_commercial_opportunity"] = True
                elif patched.get("episode_commercial") is False:
                    patched["is_commercial_opportunity"] = False
                ov = opinion_from_payload(
                    mode=mode,
                    status=result.status,
                    payload=patched,
                    synthetic=bool(result.synthetic),
                    model_family=str(slot.get("model_family") or ""),
                    confidence_floor=float(settings.commercial_ai_confidence_floor or 0.75),
                )
                last_payload = patched
            else:
                last_payload = result.structured
            opinions.append(ov)

        decision = decide_commercial(opinions)
        ev = validate_episode_evidence(last_payload, ctx)
        # Arm A (message-only): seed is the only allowed evidence; still validate IDs if present.
        if not include_related and last_payload:
            # Soften: if no evidence ids, invent seed citation for validity path.
            if not ev.evidence_message_ids:
                ev = validate_episode_evidence(
                    {
                        **(last_payload or {}),
                        "primary_evidence_message_ids": [seed_id],
                        "episode_commercial": (last_payload or {}).get(
                            "is_commercial_opportunity"
                        ),
                    },
                    ctx,
                )

        await record_episode_shadow_decision(
            session,
            run_id=run_id,
            message=message,
            text_hash=text_hash,
            episode_id=episode_id_i,
            context_version=ctx.context_version,
            context_hash=ctx.context_hash,
            experiment_arm=arm,
            prompt_version=prompt_ver,
            decision=decision,
            evidence_message_ids=ev.evidence_message_ids,
            evidence_valid=ev.ok,
            evidence_reject_reason=ev.reason,
        )
        await session.commit()
        logger.info(
            "episode_shadow done seed=%s arm=%s decision=%s evidence_ok=%s",
            seed_id,
            arm,
            decision.decision,
            ev.ok,
        )


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    bus = RedisBus(settings.redis_url)
    backend = _backend()
    scorer = LeadScorer(settings.scoring_config)
    consumer = f"cep-{os.getpid()}"
    deliveries: dict[str, int] = {}

    if not settings.commercial_episode_shadow_enabled:
        logger.warning("COMMERCIAL_EPISODE_SHADOW_ENABLED=false — worker idle")

    try:
        await bus.redis.xgroup_create(
            COMMERCIAL_EPISODE_SHADOW_STREAM,
            COMMERCIAL_EPISODE_SHADOW_GROUP,
            id="0",
            mkstream=True,
        )
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    while True:
        settings = get_settings()
        if not settings.commercial_episode_shadow_enabled:
            await asyncio.sleep(5)
            continue
        rows = await bus.consume(
            COMMERCIAL_EPISODE_SHADOW_STREAM,
            COMMERCIAL_EPISODE_SHADOW_GROUP,
            consumer,
            block_ms=5000,
            count=3,
        )
        if not rows:
            continue
        for _stream, messages in rows:
            for msg_id, fields in messages:
                try:
                    payload = json.loads(fields.get("payload") or "{}")
                    await process_shadow(payload, backend, scorer)
                    await bus.ack(
                        COMMERCIAL_EPISODE_SHADOW_STREAM, COMMERCIAL_EPISODE_SHADOW_GROUP, msg_id
                    )
                    deliveries.pop(msg_id, None)
                except Exception as exc:
                    n = deliveries.get(msg_id, 0) + 1
                    deliveries[msg_id] = n
                    logger.exception("shadow_failed id=%s n=%s", msg_id, n)
                    if n >= MAX_DELIVERY:
                        await bus.publish(
                            COMMERCIAL_EPISODE_SHADOW_DLQ,
                            {"orig_id": msg_id, "error": str(exc)},
                            maxlen=5_000,
                        )
                        await bus.ack(
                            COMMERCIAL_EPISODE_SHADOW_STREAM,
                            COMMERCIAL_EPISODE_SHADOW_GROUP,
                            msg_id,
                        )
                        deliveries.pop(msg_id, None)


if __name__ == "__main__":
    asyncio.run(main())
