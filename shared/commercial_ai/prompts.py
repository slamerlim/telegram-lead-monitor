"""Commercial AI prompt builders (separate from cursor_ai_val_v*)."""

from __future__ import annotations

from shared.validation.blind_input import BlindItem
from shared.validation.definition import objectives_text

PROMPT_VERSION = "commercial_ai_v1"

_LEAD_TYPES = (
    "BOT_PURCHASE|BOT_REPAIR|BOT_CUSTOMIZATION|STRATEGY_IMPLEMENTATION|"
    "TRADING_SYSTEM_CONTRACT|QUANT_ENGINEERING_CONTRACT|ML_AI_ENGINEERING_CONTRACT|"
    "ARBITRAGE_PROJECT|COPY_TRADING_PROJECT|MARKET_MAKING_PROJECT|SOLANA_DEX_BOT_PROJECT|null"
)

_SCHEMA = (
    '{"is_commercial_opportunity":boolean,'
    f'"lead_type":"{_LEAD_TYPES}",'
    '"matches_objectives":boolean,'
    '"buyer_action":"PURCHASE|REPAIR|HIRE|COMMISSION|IMPLEMENT|UNKNOWN",'
    '"buyer_role":"DIRECT_BUYER|COMPANY_REPRESENTATIVE|RECRUITER|UNKNOWN",'
    '"contactability":"HIGH|MEDIUM|LOW",'
    '"confidence":0.0,'
    '"hard_veto":boolean,'
    '"uncertain":boolean,'
    '"rationale_short":"string",'
    '"evidence":["short verbatim quote"]}'
)


def build_commercial_mode_prompt(
    mode: str,
    item: BlindItem,
    *,
    scoring_yaml: str = "config/scoring.yaml",
    scorer_meta: dict | None = None,
) -> str:
    """Build commercial review prompt. Layer is commercial_ai_review (not blind validation)."""
    objectives = objectives_text(scoring_yaml)
    meta = ""
    if scorer_meta:
        meta = (
            "\nDeterministic scorer metadata (advisory, may be wrong):\n"
            f"- tier: {scorer_meta.get('tier')}\n"
            f"- lead_type: {scorer_meta.get('lead_type')}\n"
            f"- buyer_type: {scorer_meta.get('buyer_type')}\n"
            f"- score: {scorer_meta.get('score')}\n"
            f"- reasons: {scorer_meta.get('reasons')}\n"
        )
    role = {
        "A": "Identify genuine commercial buyers of trading-bot / quant / ML engineering services.",
        "B": "Skeptical reviewer: aggressively reject false positives (jobs, support, spam, seekers).",
        "C": "Commercial-intent specialist: confirm buyer/hire/commission/repair signals.",
    }.get(mode.upper(), "Commercial opportunity reviewer.")
    return (
        f"You are Mode {mode.upper()} commercial AI reviewer for a trading-systems engineering business.\n"
        f"Layer: commercial_ai_review (NOT independent_ai_validation).\n"
        f"Prompt version: {PROMPT_VERSION}\n"
        f"Role: {role}\n\n"
        f"Commercial objectives:\n{objectives}\n"
        f"{meta}\n"
        "Rules:\n"
        "- matches_objectives must be JSON boolean true only when objectives clearly match.\n"
        "- hard_veto=true for clear non-commercial (job seeker, support, spam, education-only).\n"
        "- evidence must be short verbatim quotes from MESSAGE_TEXT.\n"
        "- Do not invent contact data, prices, or prior conversations.\n"
        "- Return ONLY one JSON object matching the schema.\n\n"
        f"Schema: {_SCHEMA}\n\n"
        f"<<<MESSAGE_TEXT>>>\n{item.text}\n<<<END_MESSAGE_TEXT>>>\n"
        f"nonce={item.nonce}\n"
    )


def build_outreach_draft_prompt(
    *,
    message_text: str,
    lead_type: str | None,
    rationale: str | None,
) -> str:
    return (
        "Draft a short outreach message for a trading-systems / bot engineering services provider.\n"
        "Constraints: max 600 chars; no URLs; no price/ETA guarantees; no fake portfolio claims;\n"
        "reference a concrete need from the source message; polite and specific; not spammy.\n"
        f"Detected lead_type: {lead_type}\n"
        f"Rationale: {rationale}\n\n"
        f"<<<SOURCE>>>\n{message_text}\n<<<END>>>\n"
        'Return JSON: {"draft_text":"...", "language":"en|ru|other"}'
    )
