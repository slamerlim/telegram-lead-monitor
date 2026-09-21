"""Unit tests for reprocess helpers (batch skip-write / status preservation)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reprocess_messages.py"


def _load_reprocess_module():
    spec = importlib.util.spec_from_file_location(
        "reprocess_messages",
        SCRIPT,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


reprocess_mod = _load_reprocess_module()


def _sample_result(**overrides):
    base = {
        "score": 80,
        "tier": "HIGH",
        "lead_type": "BOT_PURCHASE",
        "buyer_type": "CLIENT",
        "intent_score": 1.0,
        "technical_score": 1.0,
        "commercial_score": 1.0,
        "promotion_score": 0.0,
        "matched_keywords": ["buy bot"],
        "matched_categories": ["commercial_purchase"],
        "reasons": ["explicit commercial intent"],
        "contact_usernames": ["alice"],
        "contact_urls": [],
        "budget_amount": 3000.0,
        "budget_currency": "USD",
        "semantic_score": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_apply_lead_values_detects_unchanged():
    result = _sample_result()
    values = reprocess_mod.lead_values_from_result(result, "NEW")
    lead = SimpleNamespace(**values)
    assert reprocess_mod.apply_lead_values(lead, values) is False


def test_apply_lead_values_detects_update():
    result = _sample_result(score=80)
    values = reprocess_mod.lead_values_from_result(result, "NEW")
    lead = SimpleNamespace(**values)
    lead.score = 10
    assert reprocess_mod.apply_lead_values(lead, values) is True
    assert lead.score == 80


def test_lead_values_preserve_non_new_status():
    result = _sample_result()
    values = reprocess_mod.lead_values_from_result(result, "CONTACTED")
    assert values["status"] == "CONTACTED"


def test_cli_requires_positive_max_messages():
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "TELEGRAM_API_ID": "123456",
        "TELEGRAM_API_HASH": "test-hash",
        "TELEGRAM_PHONE": "+10000000000",
        "SCORING_CONFIG": str(ROOT / "config" / "scoring.yaml"),
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--max-messages",
            "0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode != 0
    assert "--max-messages must be > 0" in (proc.stderr + proc.stdout)


def test_cli_exposes_max_messages_flag():
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "TELEGRAM_API_ID": "123456",
        "TELEGRAM_API_HASH": "test-hash",
        "TELEGRAM_PHONE": "+10000000000",
        "SCORING_CONFIG": str(ROOT / "config" / "scoring.yaml"),
    }
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0
    assert "--max-messages" in proc.stdout
    assert "--batch-size" in proc.stdout
