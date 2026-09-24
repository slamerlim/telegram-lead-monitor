"""Episode plane must not write 0007 / human_labels / CRM promote."""

from __future__ import annotations

import inspect

from shared.commercial_ai import store as cai_store
from services.commercial_episode_shadow.app import main as shadow_main
from services.commercial_discovery.app import main as disc_main


def test_shadow_decision_helper_has_no_promote():
    src = inspect.getsource(cai_store.record_episode_shadow_decision)
    assert "AI_PROMOTE" not in src
    assert "CONTACT_ATTEMPT" not in src
    assert "lead.status" not in src


def test_shadow_worker_never_imports_validation_consensus_writers():
    src = inspect.getsource(shadow_main)
    assert "ai_validation_attempts" not in src
    assert "human_labels" not in src.lower() or "HumanLabel" not in src
    assert "AI_PROMOTE" not in src
    assert "CONTACT_ATTEMPT" not in src


def test_discovery_worker_skips_ai_confirmed():
    src = inspect.getsource(disc_main.process_discovery)
    assert "AI_CONFIRMED" in src
    assert "blind" in src.lower()
