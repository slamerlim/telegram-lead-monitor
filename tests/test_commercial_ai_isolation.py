"""Commercial AI isolation: must not import validation gate tables for writes."""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN = {
    "LabelReview",
    "HumanLabel",
    "AIValidationAttempt",
    "ValidationConsensus",
}


def test_commercial_ai_package_does_not_import_gate_models():
    root = Path("shared/commercial_ai")
    worker = Path("services/commercial_ai")
    for base in (root, worker):
        for path in base.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "shared.models":
                    names = {a.name for a in node.names}
                    bad = names & FORBIDDEN
                    assert not bad, f"{path} imports forbidden models {bad}"
