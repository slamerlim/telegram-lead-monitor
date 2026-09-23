"""Event-driven commercial AI review worker (Cursor SDK A/B/C).

Writes only via shared/commercial_ai/store.py — never LABEL_WRITE_TOKEN /validation gates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

import yaml
from sqlalchemy import select

from services.validator.app.providers.cursor_backend import FakeCursorBackend, SdkCursorBackend
from shared.commercial_ai.policy import decide_commercial, opinion_from_payload
from shared.commercial_ai.prompts import (
    PROMPT_VERSION,
    build_commercial_mode_prompt,
    build_outreach_draft_prompt,
)
from shared.commercial_ai.store import (
    already_decided,
    apply_decision,
    record_draft,
    record_opinion,
    text_sha256,
    validate_draft,
)
from shared.commercial_ai.streams import COMMERCIAL_AI_GROUP, COMMERCIAL_AI_REVIEW_STREAM
from shared.db import SessionLocal
from shared.logging import configure_logging
from shared.models import LabelReviewSample, Lead, Message
from shared.redis_bus import RedisBus
from shared.settings import get_settings
from shared.validation.blind_input import BlindItem

logger = logging.getLogger("commercial_ai")


def _load_roster(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    slots = [v for v in (raw.get("validators") or []) if v.get("enabled", True)]
    modes = {s.get("mode") for s in slots}
    if not {"A", "B", "C"}.issubset(modes):
        raise RuntimeError("commercial AI roster requires enabled A/B/C slots")
    for s in slots:
        if not str(s.get("id", "")).startswith("cai_"):
            raise RuntimeError(f"commercial slot id must start with cai_: {s.get('id')}")
    return slots


def _backend():
    settings = get_settings()
    kind = (settings.commercial_ai_backend or "fake").lower()
    if kind == "sdk":
        return SdkCursorBackend()
    return FakeCursorBackend()


async def process_payload(payload: dict, backend) -> None:
    settings = get_settings()
    if not settings.commercial_ai_enabled:
        return
    lead_id = int(payload["lead_id"])
    message_id = int(payload["message_id"])
    expected_sha = payload.get("text_sha256")

    async with SessionLocal() as session:
        lead = await session.get(Lead, lead_id)
        message = await session.get(Message, message_id)
        if not lead or not message:
            logger.warning("commercial_ai skip missing lead/message %s/%s", lead_id, message_id)
            return
        if (lead.status or "").upper() not in {"NEW", "REVIEWED"}:
            return
        if (lead.tier or "").upper() == "LOW":
            return
        blind = await session.scalar(
            select(LabelReviewSample.id).where(LabelReviewSample.message_id == message.id).limit(1)
        )
        if blind:
            logger.info("commercial_ai skip blind sample message_id=%s", message.id)
            return

        text_hash = text_sha256(message.text)
        if expected_sha and expected_sha != text_hash:
            logger.info("commercial_ai skip text changed lead_id=%s", lead_id)
            return
        prior = await already_decided(session, lead_id=lead.id, text_hash=text_hash)
        if prior:
            return

        cfg_path = Path(settings.commercial_ai_reviewers_config)
        if not cfg_path.exists():
            cfg_path = Path("config/commercial_ai_reviewers.yaml")
        slots = _load_roster(cfg_path)
        run_id = f"cai_{uuid.uuid4().hex[:16]}"
        item = BlindItem(text=message.text, nonce=run_id)
        scorer_meta = {
            "tier": lead.tier,
            "lead_type": lead.lead_type,
            "buyer_type": lead.buyer_type,
            "score": lead.score,
            "reasons": lead.reasons,
        }

        opinions = []
        for slot in slots:
            mode = str(slot["mode"]).upper()
            prompt = build_commercial_mode_prompt(
                mode, item, scoring_yaml=settings.scoring_config, scorer_meta=scorer_meta
            )
            result = await asyncio.to_thread(
                backend.validate, model=str(slot["model"]), prompt=prompt
            )
            await record_opinion(
                session,
                run_id=run_id,
                lead=lead,
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
                latency_ms=result.latency_ms,
                raw=result.raw,
            )
            opinions.append(
                opinion_from_payload(
                    mode=mode,
                    status=result.status,
                    payload=result.structured,
                    synthetic=bool(result.synthetic),
                    model_family=str(slot.get("model_family") or ""),
                    confidence_floor=settings.commercial_ai_confidence_floor,
                )
            )

        decision = decide_commercial(
            opinions,
            scorer_score=float(lead.score or 0),
            auto_promote=bool(settings.commercial_ai_auto_promote),
        )
        await apply_decision(
            session,
            run_id=run_id,
            lead=lead,
            message=message,
            text_hash=text_hash,
            decision=decision,
            opinions=opinions,
        )

        # Outreach draft for confirmed / shadow confirmed
        if decision.decision in {"AI_CONFIRMED", "SHADOW_CONFIRMED"}:
            draft_prompt = build_outreach_draft_prompt(
                message_text=message.text,
                lead_type=decision.lead_type,
                rationale=decision.reason_code,
            )
            draft_model = str(slots[0]["model"])
            draft_res = await asyncio.to_thread(
                backend.validate, model=draft_model, prompt=draft_prompt
            )
            draft_text = ""
            if draft_res.structured and isinstance(draft_res.structured.get("draft_text"), str):
                draft_text = draft_res.structured["draft_text"]
            elif draft_res.raw:
                draft_text = draft_res.raw[:600]
            valid, reason = validate_draft(draft_text, message.text)
            await record_draft(
                session,
                run_id=run_id,
                lead=lead,
                message=message,
                text_hash=text_hash,
                draft_text=draft_text or "",
                valid=valid,
                reject_reason=reason,
            )

        await session.commit()
        logger.info(
            "commercial_ai done lead_id=%s decision=%s prompt=%s",
            lead_id,
            decision.decision,
            PROMPT_VERSION,
        )


async def run_forever() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.commercial_ai_enabled:
        logger.warning("COMMERCIAL_AI_ENABLED=false — worker idle")
    backend = _backend()
    bus = RedisBus(settings.redis_url)
    consumer = f"cai-{os.getpid()}"
    logger.info(
        "commercial_ai worker starting group=%s backend=%s auto_promote=%s",
        COMMERCIAL_AI_GROUP,
        settings.commercial_ai_backend,
        settings.commercial_ai_auto_promote,
    )
    while True:
        try:
            if not settings.commercial_ai_enabled:
                await asyncio.sleep(5)
                get_settings.cache_clear()
                settings = get_settings()
                continue
            rows = await bus.consume(
                COMMERCIAL_AI_REVIEW_STREAM,
                COMMERCIAL_AI_GROUP,
                consumer,
                block_ms=5000,
                count=5,
            )
            if not rows:
                continue
            for _stream, messages in rows:
                for msg_id, fields in messages:
                    try:
                        payload = json.loads(fields.get("payload") or "{}")
                        await process_payload(payload, backend)
                        await bus.ack(COMMERCIAL_AI_REVIEW_STREAM, COMMERCIAL_AI_GROUP, msg_id)
                    except Exception:
                        logger.exception("commercial_ai process failed id=%s", msg_id)
                        # leave pending for retry / XAUTOCLAIM
        except Exception:
            logger.exception("commercial_ai loop error")
            await asyncio.sleep(2)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
