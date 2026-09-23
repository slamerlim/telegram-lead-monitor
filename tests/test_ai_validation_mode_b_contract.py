"""Mode B schema↔mapper contract and AI_TRUE reachability."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from shared.validation.blind_input import BlindItem
from shared.validation.consensus import (
    AI_FALSE,
    AI_TRUE,
    AI_UNCERTAIN,
    VALIDATED_TRUE,
    Opinion,
    compute_consensus,
    mode_a_to_label,
    mode_b_to_label,
    mode_c_to_label,
)
from shared.validation.prompts import PROMPT_VERSION, build_mode_prompt
from shared.validation.registry import parse_slots


def _schema_keys(mode: str) -> set[str]:
    item = BlindItem(text="need a trading bot built for bybit", nonce="n1")
    prompt = build_mode_prompt(mode, item, scoring_yaml="config/scoring.yaml")
    m = re.search(r"Schema:\s*(\{.*?\})", prompt)
    assert m, f"no schema in mode {mode} prompt"
    # crude key extraction from JSON-like schema string
    return set(re.findall(r'"([a-z_]+)"\s*:', m.group(1)))


def test_prompt_version_is_v2():
    assert PROMPT_VERSION == "cursor_ai_val_v2"


def test_yaml_prompt_versions_match_constant():
    raw = yaml.safe_load(Path("config/ai_validators.yaml").read_text(encoding="utf-8"))
    slots = parse_slots(raw)
    for s in slots:
        assert s.prompt_version == PROMPT_VERSION, s.id


def test_mode_schemas_declare_true_path_keys():
    assert {"is_target_lead", "lead_type", "uncertain"} <= _schema_keys("A")
    assert {"is_false_positive", "matches_objectives", "lead_type", "uncertain"} <= _schema_keys("B")
    assert {"has_commercial_intent", "lead_type", "uncertain"} <= _schema_keys("C")


def test_mode_b_mapper_true_path():
    assert mode_b_to_label(
        {
            "uncertain": False,
            "is_false_positive": False,
            "matches_objectives": True,
            "lead_type": "BOT_PURCHASE",
        }
    ) == (AI_TRUE, "BOT_PURCHASE")


def test_mode_b_mapper_matches_false_is_uncertain():
    assert mode_b_to_label(
        {"uncertain": False, "is_false_positive": False, "matches_objectives": False}
    ) == (AI_UNCERTAIN, None)


def test_mode_b_mapper_false_positive():
    assert mode_b_to_label(
        {"uncertain": False, "is_false_positive": True, "fp_class": "SUPPORT_REQUEST"}
    ) == (AI_FALSE, "SUPPORT_REQUEST")


def test_mode_b_mapper_uncertain_overrides():
    assert mode_b_to_label(
        {
            "uncertain": True,
            "is_false_positive": False,
            "matches_objectives": True,
            "lead_type": "BOT_PURCHASE",
        }
    ) == (AI_UNCERTAIN, None)


def test_mode_b_mapper_rejects_stringy_matches():
    assert mode_b_to_label(
        {
            "uncertain": False,
            "is_false_positive": False,
            "matches_objectives": "true",
            "lead_type": "BOT_PURCHASE",
        }
    ) == (AI_UNCERTAIN, None)


def test_mode_b_v1_payload_without_new_keys_is_uncertain():
    assert mode_b_to_label(
        {"uncertain": False, "is_false_positive": False, "confidence": 0.99}
    ) == (AI_UNCERTAIN, None)


def test_unanimous_true_reachable_when_b_qualified():
    text = "I need a developer to build a trading bot. Budget $2000."
    quote = "build a trading bot"
    ops = [
        Opinion(
            mode="A",
            label=AI_TRUE,
            confidence=0.9,
            lead_type="BOT_PURCHASE",
            evidence=(quote,),
            model_family="anthropic",
            model="m1",
        ),
        Opinion(
            mode="B",
            label=AI_TRUE,
            confidence=0.9,
            lead_type="BOT_PURCHASE",
            evidence=(quote,),
            model_family="openai",
            model="m2",
        ),
        Opinion(
            mode="C",
            label=AI_TRUE,
            confidence=0.9,
            lead_type="BOT_PURCHASE",
            evidence=(quote,),
            model_family="xai",
            model="m3",
        ),
    ]
    # Labels already AI_TRUE; compute_consensus will normalize
    r = compute_consensus(ops, text)
    assert r.state == VALIDATED_TRUE
    assert r.gate_eligible is True


def test_mode_a_c_mappers_still_work():
    assert mode_a_to_label({"uncertain": False, "is_target_lead": True, "lead_type": "BOT_REPAIR"}) == (
        AI_TRUE,
        "BOT_REPAIR",
    )
    assert mode_c_to_label(
        {"uncertain": False, "has_commercial_intent": True, "lead_type": "STRATEGY_IMPLEMENTATION"}
    ) == (AI_TRUE, "STRATEGY_IMPLEMENTATION")
