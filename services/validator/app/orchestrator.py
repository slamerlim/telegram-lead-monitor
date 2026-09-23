"""Orchestrate Cursor validators (SDK or fake) against the validation API."""

from __future__ import annotations

import argparse
import hashlib
import os
import uuid
from pathlib import Path

import httpx

from shared.validation.blind_input import BlindItem
from shared.validation.prompts import build_mode_prompt
from shared.validation.registry import load_validator_config, parse_slots, validate_registry
from services.validator.app.providers.cursor_backend import (
    FakeCursorBackend,
    SdkCursorBackend,
    input_sha256,
)


def _token_map(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, v = part.split(":", 1)
        if k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def run_batch(
    *,
    api_base: str,
    sample_batch_id: str,
    run_id: str,
    config_path: str,
    tokens: dict[str, str],
    backend_kind: str,
    limit: int,
    scoring_yaml: str,
    cwd: str,
    cursor_api_key: str,
) -> dict:
    slots = parse_slots(load_validator_config(config_path))
    _ = validate_registry(slots, require_distinct_families=True)
    if backend_kind == "fake":
        backend = FakeCursorBackend()
    else:
        if not cursor_api_key:
            raise SystemExit("CURSOR_API_KEY required for sdk backend")
        backend = SdkCursorBackend(api_key=cursor_api_key, cwd=cwd)

    abc = [s for s in slots if s.enabled and s.mode in ("A", "B", "C")]
    if not abc:
        raise SystemExit("no A/B/C slots enabled")

    client = httpx.Client(base_url=api_base.rstrip("/"), timeout=120.0)
    stats = {"submitted": 0, "attempts_ok": 0, "errors": 0, "run_id": run_id, "messages": 0}

    # Per-slot queues keyed by message_id → queue item
    by_slot: dict[str, dict[int, dict]] = {}
    for slot in abc:
        tok = tokens.get(slot.id)
        if not tok:
            raise SystemExit(f"missing token for {slot.id}")
        resp = client.get(
            "/validation/queue",
            params={
                "sample_batch_id": sample_batch_id,
                "reviewer_id": slot.id,
                "limit": min(max(limit * 5, limit), 200),
            },
            headers={"X-Validator-Token": tok},
        )
        if resp.status_code != 200:
            raise SystemExit(f"queue failed for {slot.id}: {resp.status_code} {resp.text}")
        by_slot[slot.id] = {item["message_id"]: item for item in resp.json()}

    message_ids = set.intersection(*(set(m.keys()) for m in by_slot.values())) if by_slot else set()
    if not message_ids:
        raise SystemExit("no overlapping unread sample messages across A/B/C queues; widen sample or clear prior AI reviews for those slots")
    message_ids = sorted(message_ids)[:limit]

    for message_id in message_ids:
        # text from any slot item
        text = next(items[message_id]["text"] for items in by_slot.values() if message_id in items)
        stats["messages"] += 1
        for slot in abc:
            item = by_slot[slot.id].get(message_id)
            if not item:
                stats["errors"] += 1
                continue
            stok = tokens[slot.id]
            nonce = hashlib.sha256(f"{run_id}:{message_id}:{slot.mode}".encode()).hexdigest()[:16]
            blind = BlindItem(text=text, nonce=nonce, text_length=len(text))
            prompt = build_mode_prompt(slot.mode, blind, scoring_yaml=scoring_yaml)
            result = backend.validate(model=slot.model, prompt=prompt)
            payload = {
                "message_id": message_id,
                "sample_batch_id": sample_batch_id,
                "reviewer_id": slot.id,
                "validation_run_id": run_id,
                "task_token": item["task_token"],
                "attempt_no": 1,
                "status": result.status,
                "request_id": result.request_id,
                "latency_ms": result.latency_ms,
                "input_sha256": input_sha256(text, nonce),
                "structured_result": result.structured,
                "error_detail": result.error_detail,
                "synthetic": result.synthetic or backend_kind == "fake",
                "confidence": (result.structured or {}).get("confidence"),
                "evidence": (result.structured or {}).get("evidence"),
                "rationale_short": (result.structured or {}).get("rationale_short"),
            }
            posted = client.post(
                "/validation/results",
                json=payload,
                headers={"X-Validator-Token": stok},
            )
            stats["submitted"] += 1
            if posted.status_code == 200 and result.status == "ok":
                stats["attempts_ok"] += 1
            else:
                stats["errors"] += 1

        lead = abc[0]
        client.post(
            "/validation/consensus/recompute",
            params={
                "sample_batch_id": sample_batch_id,
                "message_id": message_id,
                "validation_run_id": run_id,
                "reviewer_id": lead.id,
            },
            headers={"X-Validator-Token": tokens[lead.id]},
        )
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cursor AI validation batch orchestrator")
    p.add_argument("--api-base", default=os.environ.get("API_BASE_URL", "http://127.0.0.1:8010"))
    p.add_argument("--sample-batch-id", required=True)
    p.add_argument("--run-id", default=None)
    p.add_argument("--config", default=os.environ.get("AI_VALIDATORS_CONFIG", "config/ai_validators.yaml"))
    p.add_argument("--backend", choices=("fake", "sdk"), default="fake")
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--scoring-yaml", default="config/scoring.yaml")
    p.add_argument("--cwd", default=str(Path(__file__).resolve().parents[3]))
    args = p.parse_args(argv)
    tokens = _token_map(os.environ.get("AI_VALIDATOR_TOKENS", ""))
    run_id = args.run_id or f"aival_run_{uuid.uuid4().hex[:10]}"
    stats = run_batch(
        api_base=args.api_base,
        sample_batch_id=args.sample_batch_id,
        run_id=run_id,
        config_path=args.config,
        tokens=tokens,
        backend_kind=args.backend,
        limit=args.limit,
        scoring_yaml=args.scoring_yaml,
        cwd=args.cwd,
        cursor_api_key=os.environ.get("CURSOR_API_KEY", ""),
    )
    print(stats)
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
