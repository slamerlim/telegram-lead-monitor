"""AI validation API routes (Cursor validators). Mounted from main."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import get_session
from shared.models import AIValidationAttempt, LabelReview, LabelReviewSample, Message, ValidationConsensus
from shared.settings import get_settings
from shared.validation.consensus import (
    POLICY_VERSION,
    Opinion,
    compute_consensus,
    mode_a_to_label,
    mode_b_to_label,
    mode_c_to_label,
)
from shared.validation.definition import COMMERCIAL_LEAD_TYPES
from shared.validation.diagnostic import (
    DIAGNOSTIC_BATCH_PREFIXES,
    is_diagnostic_batch,
    snapshot_gate_eligible,
)
from shared.validation.gate import AiGateInput, evaluate_ai_gate
from shared.validation.registry import (
    AI_PREFIX,
    ai_allowlist_ready,
    load_validator_config,
    mint_ai_task_token,
    parse_ai_task_token,
    parse_slots,
    sign_ai_row,
    validate_registry,
    verify_ai_row,
)
from shared.independent_review_auth import parse_id_csv, verify_reviewer_token

router = APIRouter(prefix="/validation", tags=["ai-validation"])

_AI_LABELS = frozenset({"AI_TRUE", "AI_FALSE", "AI_UNCERTAIN", "AI_INSUFFICIENT_EVIDENCE"})


def gate_latest_consensus_subquery():
    """Latest non-synthetic, non-diagnostic consensus id per message_id.

    Exclusion of diagnostic batches MUST happen inside this subquery (before max(id)),
    otherwise a newer diagnostic row for a reused message_id would displace production
    consensus in gate numerators.
    """
    q = select(
        ValidationConsensus.message_id,
        func.max(ValidationConsensus.id).label("max_id"),
    ).where(ValidationConsensus.synthetic.is_(False))
    for prefix in DIAGNOSTIC_BATCH_PREFIXES:
        # Literal prefix match (avoid LIKE '_' wildcards).
        q = q.where(func.left(ValidationConsensus.sample_batch_id, len(prefix)) != prefix)
    return q.group_by(ValidationConsensus.message_id).subquery()


class AIValidationQueueItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: int
    sample_batch_id: str
    text: str
    task_token: str


class AIValidationResultCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: int
    sample_batch_id: str = Field(min_length=1, max_length=64)
    reviewer_id: str = Field(min_length=1, max_length=64)
    validation_run_id: str = Field(min_length=1, max_length=64)
    task_token: str = Field(min_length=1, max_length=512)
    attempt_no: int = Field(ge=1, le=20)
    status: str = Field(min_length=1, max_length=32)
    request_id: str | None = None
    latency_ms: int | None = None
    input_sha256: str | None = None
    structured_result: dict[str, Any] | None = None
    error_detail: str | None = None
    synthetic: bool = False
    # Client-claimed identity must match YAML slot; API overwrites from config.
    label: str | None = None
    confidence: float | None = None
    evidence: list[str] | None = None
    rationale_short: str | None = None
    lead_type: str | None = None
    fp_class: str | None = None


class ValidationGatesOut(BaseModel):
    ai_validation_gate_ready: bool = False
    ai_validation_gate_closed_reasons: list[str] = Field(default_factory=list)
    ai_validated_messages: int = 0
    ai_validated_true: int = 0
    ai_validated_false: int = 0
    ai_contested: int = 0
    ai_uncertain: int = 0
    ai_insufficient: int = 0
    ai_failed: int = 0
    ai_error: int = 0
    ai_distinct_families_configured: int = 0
    ai_unattested_rows: int = 0
    ml_training_enabled: bool = False
    human_gate_closed_reasons: list[str] = Field(default_factory=list)
    ml_gate_independence_ready: bool = False


def _load_slots():
    cfg = get_settings()
    try:
        raw = load_validator_config(cfg.ai_validators_config)
    except Exception:
        return [], ["ai_validators_config_unreadable"]
    return parse_slots(raw), []


def _ai_auth(reviewer_id: str, token: str | None):
    cfg = get_settings()
    slots, load_reasons = _load_slots()
    allow, tokens, ready, reasons = ai_allowlist_ready(
        ids_csv=cfg.ai_validator_ids,
        tokens_csv=cfg.ai_validator_tokens,
        queue_secret=cfg.ai_validation_queue_hmac_secret,
        row_secret=cfg.ai_validation_row_secret,
        slots=slots,
    )
    reg_reasons = validate_registry(
        slots, require_distinct_families=cfg.ai_validation_require_distinct_families
    )
    reasons = list(reasons) + load_reasons + reg_reasons
    human_ids = parse_id_csv(cfg.independent_human_reviewer_ids)
    if human_ids & allow:
        reasons.append("validator_id_overlap")
    # Fail closed on ANY registry / overlap issue — not only missing tokens.
    blocking = sorted(set(reasons))
    if not ready or blocking:
        raise HTTPException(
            status_code=503,
            detail={"error": "ai auth not ready", "reasons": blocking or ["ai_auth_not_configured"]},
        )
    if reviewer_id not in allow:
        raise HTTPException(status_code=403, detail="reviewer_id not on AI allowlist")
    if not reviewer_id.startswith(f"{AI_PREFIX}_"):
        raise HTTPException(status_code=400, detail="AI reviewer_id must use aival_ prefix")
    if not verify_reviewer_token(tokens, reviewer_id, token):
        raise HTTPException(status_code=401, detail="invalid or missing X-Validator-Token")
    slot = next((s for s in slots if s.id == reviewer_id and s.enabled), None)
    if slot is None:
        raise HTTPException(status_code=403, detail="validator slot not enabled")
    return slots, slot, cfg


def _row_fields_for_sig(
    *,
    message_id: int,
    sample_batch_id: str,
    reviewer_id: str,
    mode: str,
    provider: str,
    model: str,
    model_family: str,
    prompt_version: str,
    config_version: str,
    label: str,
    confidence: float | None,
    lead_type: str | None,
    evidence_json: str | None,
    input_sha256: str | None,
) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "sample_batch_id": sample_batch_id,
        "reviewer_id": reviewer_id,
        "validation_mode": mode,
        "provider": provider,
        "model": model,
        "model_family": model_family,
        "prompt_version": prompt_version,
        "config_version": config_version,
        "label": label,
        "confidence": confidence,
        "lead_type": lead_type,
        "evidence_json": evidence_json,
        "input_sha256": input_sha256,
    }


@router.get("/queue", response_model=list[AIValidationQueueItem])
async def ai_validation_queue(
    sample_batch_id: str = Query(..., min_length=1, max_length=64),
    reviewer_id: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(default=10, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    x_validator_token: str | None = Header(default=None),
) -> list[AIValidationQueueItem]:
    _, slot, cfg = _ai_auth(reviewer_id, x_validator_token)
    if slot.mode == "D":
        raise HTTPException(status_code=400, detail="use /validation/adjudication-queue for mode D")
    already = (
        select(LabelReview.message_id)
        .where(
            LabelReview.reviewer_id == reviewer_id,
            LabelReview.sample_batch_id == sample_batch_id,
            LabelReview.validator_kind == "ai",
        )
    )
    q = (
        select(LabelReviewSample, Message)
        .join(Message, Message.id == LabelReviewSample.message_id)
        .where(
            LabelReviewSample.sample_batch_id == sample_batch_id,
            LabelReviewSample.message_id.not_in(already),
        )
        .order_by(LabelReviewSample.message_id.asc())
        .limit(limit)
    )
    rows = (await session.execute(q)).all()
    secret = cfg.ai_validation_queue_hmac_secret
    out: list[AIValidationQueueItem] = []
    for sample, message in rows:
        token = mint_ai_task_token(
            secret,
            reviewer_id=reviewer_id,
            sample_batch_id=sample_batch_id,
            message_id=message.id,
            mode=slot.mode,
            peer_shown=False,
        )
        out.append(
            AIValidationQueueItem(
                message_id=message.id,
                sample_batch_id=sample_batch_id,
                text=message.text or "",
                task_token=token,
            )
        )
    return out


@router.get("/adjudication-queue", response_model=list[AIValidationQueueItem])
async def ai_adjudication_queue(
    sample_batch_id: str = Query(..., min_length=1, max_length=64),
    reviewer_id: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(default=10, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    x_validator_token: str | None = Header(default=None),
) -> list[AIValidationQueueItem]:
    """Mode D only — returns blind text; peers fetched server-side at POST time conceptually.

    Queue item still forbids scorer fields. Peer outputs are NOT included here to avoid
    leaking into Modes A–C clients; Mode D orchestrator should call after A/B/C and pass
    anonymized peers only in the Cursor prompt (not via this queue schema).
    """
    _, slot, cfg = _ai_auth(reviewer_id, x_validator_token)
    if slot.mode != "D":
        raise HTTPException(status_code=400, detail="adjudication-queue requires mode D slot")
    # Messages with A/B/C present and no D yet
    already_d = select(LabelReview.message_id).where(
        LabelReview.sample_batch_id == sample_batch_id,
        LabelReview.validator_kind == "ai",
        LabelReview.validation_mode == "D",
    )
    q = (
        select(LabelReviewSample, Message)
        .join(Message, Message.id == LabelReviewSample.message_id)
        .where(
            LabelReviewSample.sample_batch_id == sample_batch_id,
            LabelReviewSample.message_id.not_in(already_d),
        )
        .order_by(LabelReviewSample.message_id.asc())
        .limit(limit)
    )
    rows = (await session.execute(q)).all()
    secret = cfg.ai_validation_queue_hmac_secret
    return [
        AIValidationQueueItem(
            message_id=message.id,
            sample_batch_id=sample_batch_id,
            text=message.text or "",
            task_token=mint_ai_task_token(
                secret,
                reviewer_id=reviewer_id,
                sample_batch_id=sample_batch_id,
                message_id=message.id,
                mode="D",
                peer_shown=True,
            ),
        )
        for sample, message in rows
    ]


@router.post("/results")
async def post_ai_validation_result(
    body: AIValidationResultCreate,
    session: AsyncSession = Depends(get_session),
    x_validator_token: str | None = Header(default=None),
) -> dict[str, Any]:
    slots, slot, cfg = _ai_auth(body.reviewer_id, x_validator_token)
    att = parse_ai_task_token(cfg.ai_validation_queue_hmac_secret, body.task_token)
    if (
        att is None
        or att.reviewer_id != body.reviewer_id
        or att.sample_batch_id != body.sample_batch_id
        or att.message_id != body.message_id
        or att.mode != slot.mode
    ):
        raise HTTPException(status_code=403, detail="invalid task_token attestation")

    member = await session.scalar(
        select(LabelReviewSample.id).where(
            LabelReviewSample.sample_batch_id == body.sample_batch_id,
            LabelReviewSample.message_id == body.message_id,
        )
    )
    if member is None:
        raise HTTPException(status_code=404, detail="message not in sample batch")

    # Always log attempt
    attempt = AIValidationAttempt(
        validation_run_id=body.validation_run_id,
        sample_batch_id=body.sample_batch_id,
        message_id=body.message_id,
        reviewer_id=body.reviewer_id,
        validation_mode=slot.mode,
        provider=slot.provider,
        model=slot.model,
        model_family=slot.model_family,
        prompt_version=slot.prompt_version,
        config_version=None,
        attempt_no=body.attempt_no,
        status=body.status,
        error_detail=(body.error_detail or "")[:2000] or None,
        request_id=body.request_id,
        latency_ms=body.latency_ms,
        input_sha256=body.input_sha256,
        raw_response=json.dumps(body.structured_result)[:16000] if body.structured_result else None,
        synthetic=body.synthetic,
        finished_at=datetime.utcnow(),
    )
    session.add(attempt)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="duplicate attempt")

    if body.status != "ok" or not body.structured_result:
        await session.commit()
        return {"attempt_id": attempt.id, "review_id": None, "status": body.status}

    # Derive label from structured result / explicit label
    payload = body.structured_result
    if slot.mode == "A":
        label, lead_or_fp = mode_a_to_label(payload)
        lead_type = lead_or_fp if label == "AI_TRUE" else body.lead_type
        fp_class = None if label == "AI_TRUE" else (body.fp_class or lead_or_fp)
    elif slot.mode == "B":
        label, lead_or_fp = mode_b_to_label(payload)
        if label == "AI_TRUE":
            lead_type = lead_or_fp or payload.get("lead_type")
            fp_class = None
        else:
            lead_type = None
            fp_class = body.fp_class or lead_or_fp
    elif slot.mode == "C":
        label, lead_or_fp = mode_c_to_label(payload)
        lead_type = lead_or_fp if label == "AI_TRUE" else None
        fp_class = None
    else:  # D
        label = payload.get("recommended_label") or body.label or "AI_UNCERTAIN"
        lead_type = None
        fp_class = None

    if label not in _AI_LABELS:
        attempt.status = "schema_invalid"
        await session.commit()
        return {"attempt_id": attempt.id, "review_id": None, "status": "schema_invalid"}

    if label == "AI_TRUE" and lead_type not in COMMERCIAL_LEAD_TYPES:
        label = "AI_INSUFFICIENT_EVIDENCE"
        lead_type = None
        fp_class = None

    confidence = body.confidence if body.confidence is not None else payload.get("confidence")
    evidence = body.evidence if body.evidence is not None else payload.get("evidence") or []
    evidence_json = json.dumps(evidence, ensure_ascii=False)
    rationale = body.rationale_short or payload.get("rationale_short")
    config_version = hashlib.sha256(
        f"{slot.id}|{slot.model}|{slot.prompt_version}".encode()
    ).hexdigest()

    peer_shown = att.peer_shown
    sig_fields = _row_fields_for_sig(
        message_id=body.message_id,
        sample_batch_id=body.sample_batch_id,
        reviewer_id=body.reviewer_id,
        mode=slot.mode,
        provider=slot.provider,
        model=slot.model,
        model_family=slot.model_family,
        prompt_version=slot.prompt_version,
        config_version=config_version,
        label=label,
        confidence=float(confidence) if confidence is not None else None,
        lead_type=lead_type,
        evidence_json=evidence_json,
        input_sha256=body.input_sha256,
    )
    sig = sign_ai_row(cfg.ai_validation_row_secret, sig_fields)

    review = LabelReview(
        message_id=body.message_id,
        sample_batch_id=body.sample_batch_id,
        reviewer_id=body.reviewer_id,
        label=label,
        fp_class=fp_class,
        commercially_actionable=True if label == "AI_TRUE" else False if label == "AI_FALSE" else None,
        scorer_shown=False,
        prior_label_shown=False,
        notes=None,
        validator_kind="ai",
        validation_mode=slot.mode,
        provider=slot.provider,
        model=slot.model,
        model_family=slot.model_family,
        prompt_version=slot.prompt_version,
        config_version=config_version,
        validation_run_id=body.validation_run_id,
        request_id=body.request_id,
        latency_ms=body.latency_ms,
        confidence=float(confidence) if confidence is not None else None,
        lead_type=lead_type,
        evidence_json=evidence_json,
        rationale_short=(rationale or "")[:500] or None,
        structured_result=json.dumps(payload, ensure_ascii=False),
        input_sha256=body.input_sha256,
        peer_outputs_shown=peer_shown,
        source_attempt_id=attempt.id,
        attestation_sig=sig,
    )
    session.add(review)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="duplicate AI review for mode/message/batch")
    return {"attempt_id": attempt.id, "review_id": review.id, "label": label, "status": "ok"}


@router.post("/consensus/recompute")
async def recompute_consensus(
    sample_batch_id: str = Query(...),
    message_id: int = Query(...),
    validation_run_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    x_validator_token: str | None = Header(default=None),
    reviewer_id: str = Query(...),
) -> dict[str, Any]:
    _ai_auth(reviewer_id, x_validator_token)
    cfg = get_settings()
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="message not found")
    rows = (
        await session.execute(
            select(LabelReview).where(
                LabelReview.sample_batch_id == sample_batch_id,
                LabelReview.message_id == message_id,
                LabelReview.validator_kind == "ai",
            )
        )
    ).scalars().all()

    # Derive synthetic from attempts — never trust a client query flag.
    attempt_q = select(AIValidationAttempt.synthetic).where(
        AIValidationAttempt.sample_batch_id == sample_batch_id,
        AIValidationAttempt.message_id == message_id,
    )
    if validation_run_id:
        attempt_q = attempt_q.where(AIValidationAttempt.validation_run_id == validation_run_id)
    attempt_flags = (await session.execute(attempt_q)).scalars().all()
    synthetic = any(bool(x) for x in attempt_flags) if attempt_flags else False
    diagnostic = is_diagnostic_batch(sample_batch_id)

    opinions: list[Opinion] = []
    unattested = 0
    for r in rows:
        fields = _row_fields_for_sig(
            message_id=r.message_id,
            sample_batch_id=r.sample_batch_id or "",
            reviewer_id=r.reviewer_id,
            mode=r.validation_mode or "",
            provider=r.provider or "",
            model=r.model or "",
            model_family=r.model_family or "",
            prompt_version=r.prompt_version or "",
            config_version=r.config_version or "",
            label=r.label,
            confidence=r.confidence,
            lead_type=r.lead_type,
            evidence_json=r.evidence_json,
            input_sha256=r.input_sha256,
        )
        signed = verify_ai_row(cfg.ai_validation_row_secret, fields, r.attestation_sig)
        if not signed:
            unattested += 1
        evidence = tuple(json.loads(r.evidence_json or "[]"))
        opinions.append(
            Opinion(
                mode=r.validation_mode or "",
                label=r.label,
                confidence=float(r.confidence or 0),
                lead_type=r.lead_type,
                fp_class=r.fp_class,
                evidence=evidence,
                provider=r.provider or "cursor",
                model=r.model or "",
                model_family=r.model_family or "",
                blind_ok=not r.scorer_shown and not r.prior_label_shown and (
                    r.validation_mode == "D" or not r.peer_outputs_shown
                ),
                signed=signed,
                allowlisted=True,
            )
        )

    result = compute_consensus(
        opinions,
        msg.text or "",
        require_distinct_families=3 if cfg.ai_validation_require_distinct_families else 1,
        diversity_precondition_failed=unattested > 0,
    )
    digest = hashlib.sha256(
        json.dumps(
            sorted([(o.mode, o.label, o.model, o.confidence) for o in opinions]),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    snap = ValidationConsensus(
        sample_batch_id=sample_batch_id,
        message_id=message_id,
        validation_run_id=validation_run_id,
        policy_version=POLICY_VERSION,
        state=result.state,
        rationale_code=result.rationale_code,
        n_true=result.n_true,
        n_false=result.n_false,
        n_uncertain=result.n_uncertain,
        n_insufficient=result.n_insufficient,
        distinct_families=result.distinct_families,
        modes_present=result.modes_present,
        adjudicator_label=result.adjudicator_label,
        lead_type=result.lead_type,
        input_review_ids=json.dumps([r.id for r in rows]),
        input_digest=digest,
        gate_eligible=snapshot_gate_eligible(
            consensus_gate_eligible=bool(result.gate_eligible),
            synthetic=synthetic,
            diagnostic=diagnostic,
            state=result.state,
        ),
        synthetic=synthetic,
    )
    session.add(snap)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(ValidationConsensus.id).where(
                ValidationConsensus.sample_batch_id == sample_batch_id,
                ValidationConsensus.message_id == message_id,
                ValidationConsensus.policy_version == POLICY_VERSION,
                ValidationConsensus.input_digest == digest,
            )
        )
        return {
            "consensus_id": existing,
            "state": result.state,
            "rationale_code": result.rationale_code,
            "synthetic": synthetic,
            "diagnostic": diagnostic,
            "deduped": True,
        }
    return {
        "consensus_id": snap.id,
        "state": result.state,
        "rationale_code": result.rationale_code,
        "gate_eligible": snap.gate_eligible,
        "synthetic": synthetic,
        "diagnostic": diagnostic,
        "unattested": unattested,
    }


async def compute_gates_payload(session: AsyncSession) -> ValidationGatesOut:
    cfg = get_settings()
    slots, load_reasons = _load_slots()
    allow, _tokens, auth_ready, auth_reasons = ai_allowlist_ready(
        ids_csv=cfg.ai_validator_ids,
        tokens_csv=cfg.ai_validator_tokens,
        queue_secret=cfg.ai_validation_queue_hmac_secret,
        row_secret=cfg.ai_validation_row_secret,
        slots=slots,
    )
    reg_reasons = validate_registry(slots, require_distinct_families=cfg.ai_validation_require_distinct_families)
    human_ids = parse_id_csv(cfg.independent_human_reviewer_ids)
    id_overlap = bool(human_ids & allow)

    # Latest consensus per message (simple: count by state from all snapshots — prefer latest via subquery)
    state_counts = dict.fromkeys(
        [
            "VALIDATED_TRUE",
            "VALIDATED_FALSE",
            "CONTESTED",
            "UNCERTAIN",
            "INSUFFICIENT_EVIDENCE",
            "FAILED_VALIDATION",
            "VALIDATION_ERROR",
        ],
        0,
    )
    # Latest id per message among non-synthetic, non-diagnostic batches only.
    latest = gate_latest_consensus_subquery()
    rows = (
        await session.execute(
            select(ValidationConsensus.state, func.count())
            .join(latest, ValidationConsensus.id == latest.c.max_id)
            .group_by(ValidationConsensus.state)
        )
    ).all()
    for st, cnt in rows:
        if st in state_counts:
            state_counts[st] = int(cnt)

    unattested = int(
        (
            await session.scalar(
                select(func.count(LabelReview.id)).where(
                    LabelReview.validator_kind == "ai",
                    LabelReview.attestation_sig.is_(None),
                )
            )
        )
        or 0
    )

    families = {s.model_family for s in slots if s.enabled and s.mode in ("A", "B", "C")}
    gate_in = AiGateInput(
        auth_ready=auth_ready and not load_reasons,
        modes_abc_present="ai_modes_missing" not in reg_reasons,
        distinct_families=len(families),
        require_families=3 if cfg.ai_validation_require_distinct_families else 1,
        id_overlap=id_overlap,
        unattested_rows=unattested,
        config_mismatch_rows=0,
        validated_messages=state_counts["VALIDATED_TRUE"] + state_counts["VALIDATED_FALSE"],
        validated_true=state_counts["VALIDATED_TRUE"],
        contested=state_counts["CONTESTED"],
        uncertain=state_counts["UNCERTAIN"],
        insufficient=state_counts["INSUFFICIENT_EVIDENCE"],
        failed=state_counts["FAILED_VALIDATION"],
        error=state_counts["VALIDATION_ERROR"],
        min_validated_messages=cfg.ai_validation_gate_min_messages,
        min_validated_true=cfg.ai_validation_gate_min_true,
    )
    gate = evaluate_ai_gate(gate_in)
    closed = list(gate.closed_reasons) + auth_reasons + load_reasons + reg_reasons
    if id_overlap and "validator_id_overlap" not in closed:
        closed.append("validator_id_overlap")

    # Human independence closed reasons (display only)
    human_reasons: list[str] = []
    from shared.independent_review_auth import parse_reviewer_tokens

    tokens = parse_reviewer_tokens(cfg.independent_human_reviewer_tokens)
    secret = (cfg.independent_review_hmac_secret or "").strip()
    if not tokens or not secret or not parse_id_csv(cfg.independent_human_reviewer_ids):
        # empty ids with tokens still can be ready in human path — mirror main loosely
        if not tokens or not secret:
            human_reasons.append("human_auth_not_configured")

    all_reasons = sorted(set(list(gate.closed_reasons) + auth_reasons + load_reasons + reg_reasons))
    if id_overlap:
        all_reasons = sorted(set(all_reasons + ["validator_id_overlap"]))

    return ValidationGatesOut(
        ai_validation_gate_ready=len(all_reasons) == 0,
        ai_validation_gate_closed_reasons=all_reasons,
        ai_validated_messages=gate_in.validated_messages,
        ai_validated_true=state_counts["VALIDATED_TRUE"],
        ai_validated_false=state_counts["VALIDATED_FALSE"],
        ai_contested=state_counts["CONTESTED"],
        ai_uncertain=state_counts["UNCERTAIN"],
        ai_insufficient=state_counts["INSUFFICIENT_EVIDENCE"],
        ai_failed=state_counts["FAILED_VALIDATION"],
        ai_error=state_counts["VALIDATION_ERROR"],
        ai_distinct_families_configured=len(families),
        ai_unattested_rows=unattested,
        ml_training_enabled=False,
        human_gate_closed_reasons=human_reasons,
    )


@router.get("/gates", response_model=ValidationGatesOut)
async def validation_gates(session: AsyncSession = Depends(get_session)) -> ValidationGatesOut:
    return await compute_gates_payload(session)
