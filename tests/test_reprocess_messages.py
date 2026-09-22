"""Unit tests for reprocess CLI + lead write helpers used by reprocess."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from shared.lead_write import apply_score_result

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


def _blank_lead(**overrides):
    base = {
        "score": 0.0,
        "tier": "LOW",
        "lead_type": None,
        "buyer_type": None,
        "status": "NEW",
        "intent_score": 0.0,
        "technical_score": 0.0,
        "commercial_score": 0.0,
        "promotion_score": 0.0,
        "matched_keywords": "[]",
        "matched_categories": "[]",
        "reasons": "[]",
        "contact_usernames": "[]",
        "contact_urls": "[]",
        "budget_amount": None,
        "budget_currency": None,
        "semantic_score": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_apply_score_result_updates_fields():
    lead = _blank_lead(status="CONTACTED")
    result = _sample_result()
    apply_score_result(lead, result)
    assert lead.score == 80
    assert lead.tier == "HIGH"
    assert lead.lead_type == "BOT_PURCHASE"
    assert lead.status == "CONTACTED"  # preserved when status=None
    assert json.loads(lead.matched_keywords) == ["buy bot"]


def test_apply_score_result_can_set_status():
    lead = _blank_lead(status="NEW")
    apply_score_result(lead, _sample_result(), status="NEW")
    assert lead.status == "NEW"


def test_apply_score_result_preserves_pipeline_status_by_default():
    lead = _blank_lead(status="CONTACTED")
    apply_score_result(lead, _sample_result())
    assert lead.status == "CONTACTED"


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
