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
            # Infer mode from prompt
            if "mode A" in prompt:
                structured = {
                    "is_target_lead": False,
                    "lead_type": None,
                    "confidence": 0.9,
                    "evidence": [],
                    "uncertain": False,
                    "rationale_short": "stub: not a commercial lead",
                }
            elif "mode B" in prompt:
                structured = {
                    "is_false_positive": True,
                    "fp_class": "SUPPORT_REQUEST",
                    "confidence": 0.85,
                    "evidence": [],
                    "uncertain": False,
                    "rationale_short": "stub: support-like",
                }
            elif "mode C" in prompt:
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
                ),
            )
            text = getattr(result, "result", None) or str(result)
            structured = _extract_json(text)
            if structured is None:
                return CursorValidationResult(
                    status="malformed_json",
                    structured=None,
                    request_id=str(getattr(result, "id", "")),
                    latency_ms=int((time.time() - t0) * 1000),
                    raw=text[:16000] if isinstance(text, str) else None,
                    error_detail="no JSON object in agent result",
                    model=model,
                )
            return CursorValidationResult(
                status="ok",
                structured=structured,
                request_id=str(getattr(result, "id", uuid.uuid4().hex)),
                latency_ms=int((time.time() - t0) * 1000),
                raw=text[:16000] if isinstance(text, str) else None,
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
