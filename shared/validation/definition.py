"""Commercial-lead definition for AI validators (must match scorer)."""

from __future__ import annotations

import re
from pathlib import Path

# Keep in sync with services/analyzer/app/scoring.py LeadScorer._COMMERCIAL_TYPES
COMMERCIAL_LEAD_TYPES: frozenset[str] = frozenset(
    {
        "BOT_PURCHASE",
        "BOT_REPAIR",
        "BOT_CUSTOMIZATION",
        "STRATEGY_IMPLEMENTATION",
        "TRADING_SYSTEM_CONTRACT",
        "QUANT_ENGINEERING_CONTRACT",
        "ML_AI_ENGINEERING_CONTRACT",
        "ARBITRAGE_PROJECT",
        "COPY_TRADING_PROJECT",
        "MARKET_MAKING_PROJECT",
        "SOLANA_DEX_BOT_PROJECT",
    }
)

_OBJECTIVE_RE = re.compile(r"^#\s*\d+\)\s*(.+)$")


def parse_scoring_objectives(scoring_yaml: Path | str) -> list[str]:
    """Parse the six commercial objectives from scoring.yaml header comments."""
    path = Path(scoring_yaml)
    text = path.read_text(encoding="utf-8")
    objectives: list[str] = []
    in_block = False
    for line in text.splitlines():
        if "Commercial objective" in line:
            in_block = True
            continue
        if in_block:
            if line.startswith("#") and _OBJECTIVE_RE.match(line):
                objectives.append(_OBJECTIVE_RE.match(line).group(1).strip())
            elif line.startswith("#") and "Technical relevance" in line:
                break
            elif line.startswith("#") and not line.strip("# ").strip():
                continue
            elif not line.startswith("#"):
                break
    return objectives


def objectives_text(scoring_yaml: Path | str) -> str:
    objs = parse_scoring_objectives(scoring_yaml)
    return "\n".join(f"{i}) {o}" for i, o in enumerate(objs, 1))
