"""Cursor-only validation backends: fake (tests) + optional cursor-sdk."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class CursorValidationResult:
    status: str  # ok | timeout | malformed_json | schema_invalid | error
    structured: dict[str, Any] | None
    request_id: str
    latency_ms: int
    raw: str | None = None
    error_detail: str | None = None
    synthetic: bool = False
    model: str = ""


class CursorBackend(Protocol):
    def validate(self, *, model: str, prompt: str) -> CursorValidationResult: ...


class FakeCursorBackend:
    """Deterministic scripted responses for pytest / stub smoke."""

    def __init__(self, scripts: dict[str, dict[str, Any]] | None = None):
        self.scripts = scripts or {}

    def validate(self, *, model: str, prompt: str) -> CursorValidationResult:
        t0 = time.time()
        script = self.scripts.get(model) or self.scripts.get("default")
        if script is None:
            low = prompt.lower()
            # Episode / commercial AI prompts (case-insensitive Mode A/B/C).
            if "commercial_ai_v2_ep" in low or "episode_messages" in low:
                # Extract seed id for evidence citation when present.
                seed_id = 1
                for token in prompt.split():
                    if token.startswith("message_id="):
                        try:
                            seed_id = int(token.split("=", 1)[1])
                            break
                        except ValueError:
                            pass
                if "mode a" in low or "you are mode a" in low:
                    structured = {
                        "episode_commercial": False,
                        "context_supported": True,
                        "context_confidence": 0.7,
                        "is_commercial_opportunity": False,
                        "lead_type": None,
                        "matches_objectives": False,
                        "confidence": 0.85,
                        "hard_veto": False,
                        "uncertain": False,
                        "primary_evidence_message_ids": [seed_id],
                        "supporting_evidence_message_ids": [],
                        "timeline_signal": False,
                        "budget_signal": False,
                        "repeat_intent_signal": False,
                        "problem_persistence_signal": False,
                        "followup_signal": False,
                        "rationale_short": "stub episode: not commercial",
                    }
                elif "mode b" in low:
                    structured = {
                        "episode_commercial": False,
                        "is_commercial_opportunity": False,
                        "is_false_positive": True,
                        "fp_class": "SUPPORT_REQUEST",
                        "matches_objectives": False,
                        "confidence": 0.85,
                        "hard_veto": True,
                        "uncertain": False,
                        "primary_evidence_message_ids": [seed_id],
                        "supporting_evidence_message_ids": [],
                        "rationale_short": "stub episode: FP",
                    }
                else:
                    structured = {
                        "episode_commercial": False,
                        "is_commercial_opportunity": False,
                        "matches_objectives": False,
                        "confidence": 0.8,
                        "hard_veto": False,
                        "uncertain": False,
                        "primary_evidence_message_ids": [seed_id],
                        "supporting_evidence_message_ids": [],
                        "rationale_short": "stub episode: no intent",
                    }
            elif "commercial_ai_review" in low or "commercial ai reviewer" in low:
                if "mode a" in low:
                    structured = {
                        "is_commercial_opportunity": False,
                        "lead_type": None,
                        "matches_objectives": False,
                        "confidence": 0.9,
                        "hard_veto": False,
                        "uncertain": False,
                        "evidence": [],
                        "rationale_short": "stub commercial: no",
                    }
                elif "mode b" in low:
                    structured = {
                        "is_false_positive": True,
                        "fp_class": "SUPPORT_REQUEST",
                        "is_commercial_opportunity": False,
                        "matches_objectives": False,
                        "confidence": 0.85,
                        "hard_veto": True,
                        "uncertain": False,
                        "evidence": [],
                        "rationale_short": "stub commercial: FP",
                    }
                else:
                    structured = {
                        "is_commercial_opportunity": False,
                        "matches_objectives": False,
                        "confidence": 0.88,
                        "hard_veto": False,
                        "uncertain": False,
                        "evidence": [],
                        "rationale_short": "stub commercial: no intent",
                    }
            elif "mode a" in low:
                structured = {
                    "is_target_lead": False,
                    "lead_type": None,
                    "confidence": 0.9,
                    "evidence": [],
                    "uncertain": False,
                    "rationale_short": "stub: not a commercial lead",
                }
            elif "mode b" in low:
                structured = {
                    "is_false_positive": True,
                    "fp_class": "SUPPORT_REQUEST",
                    "confidence": 0.85,
                    "evidence": [],
                    "uncertain": False,
                    "rationale_short": "stub: support-like",
                }
            elif "mode c" in low:
                structured = {
                    "has_commercial_intent": False,
                    "lead_type": None,
                    "confidence": 0.88,
                    "evidence": [],
                    "uncertain": False,
                    "rationale_short": "stub: no commercial intent",
                }
            else:
                structured = {
                    "recommended_label": "AI_UNCERTAIN",
                    "confidence": 0.5,
                    "rationale_short": "stub adjudicator",
                    "preserve_disagreement": True,
                }
        else:
            structured = dict(script)
        raw = json.dumps(structured)
        return CursorValidationResult(
            status="ok",
            structured=structured,
            request_id=f"fake-{uuid.uuid4().hex[:12]}",
            latency_ms=int((time.time() - t0) * 1000),
            raw=raw,
            synthetic=True,
            model=model,
        )


class SdkCursorBackend:
    """Optional cursor-sdk local Agent.prompt backend."""

    def __init__(self, api_key: str, cwd: str):
        self.api_key = api_key
        self.cwd = cwd

    def validate(self, *, model: str, prompt: str) -> CursorValidationResult:
        t0 = time.time()
        try:
            from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
        except ImportError as exc:
            return CursorValidationResult(
                status="error",
                structured=None,
                request_id="",
                latency_ms=0,
                error_detail=f"cursor_sdk not installed: {exc}",
                model=model,
            )
        try:
            result = Agent.prompt(
                prompt,
                AgentOptions(
                    api_key=self.api_key,
                    model=model,
                    local=LocalAgentOptions(cwd=self.cwd),
                    # Validation is JSON-only; block mutating/exec tools (SDK tool names).
                    disallowed_tools=[
                        "shell",
                        "piBash",
                        "piWrite",
                        "piEdit",
                        "edit",
                        "delete",
                        "applyAgentDiff",
                        "task",
                        "mcp",
                        "computerUse",
                        "writeCanvas",
                        "createAgent",
                        "sendToAgent",
                    ],
                ),
            )
            status = getattr(result, "status", None)
            request_id = str(getattr(result, "id", "") or getattr(result, "run_id", "") or "")
            text = _coerce_result_text(result)
            if status == "error":
                return CursorValidationResult(
                    status="error",
                    structured=None,
                    request_id=request_id,
                    latency_ms=int((time.time() - t0) * 1000),
                    raw=(text or "")[:16000] or None,
                    error_detail=f"agent run status=error id={request_id}",
                    model=model,
                )
            structured = _extract_json(text or "")
            if structured is None:
                return CursorValidationResult(
                    status="malformed_json",
                    structured=None,
                    request_id=request_id or uuid.uuid4().hex,
                    latency_ms=int((time.time() - t0) * 1000),
                    raw=(text or "")[:16000] or None,
                    error_detail="no JSON object in agent result",
                    model=model,
                )
            return CursorValidationResult(
                status="ok",
                structured=structured,
                request_id=request_id or uuid.uuid4().hex,
                latency_ms=int((time.time() - t0) * 1000),
                raw=(text or "")[:16000] or None,
                model=model,
            )
        except Exception as exc:  # noqa: BLE001 — surface as attempt error
            return CursorValidationResult(
                status="error",
                structured=None,
                request_id="",
                latency_ms=int((time.time() - t0) * 1000),
                error_detail=str(exc)[:2000],
                model=model,
            )


def _coerce_result_text(result: Any) -> str:
    text = getattr(result, "result", None)
    if isinstance(text, str):
        return text
    if text is not None:
        return str(text)
    # Some SDK versions expose messages; fall back to repr.
    return str(result)



def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def input_sha256(text: str, nonce: str) -> str:
    return hashlib.sha256(f"{nonce}\n{text}".encode("utf-8")).hexdigest()
