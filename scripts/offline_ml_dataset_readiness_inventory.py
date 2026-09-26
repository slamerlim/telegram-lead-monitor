#!/usr/bin/env python3
"""Read-only offline inventory: is production data ready for Optuna/ML scaffolding?

DORMANT — does not enable ml_training, alter discovery, or write labels.
Safe to run against live Postgres via docker compose exec.

Exit 0 always when queries succeed; prints JSON readiness report to stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

PATH_B_AT = os.environ.get("PATH_B_AT", "2026-09-26T06:53:16Z")
EXPECT_HL = int(os.environ.get("EXPECT_HL", "1524"))
EXPECT_AC = int(os.environ.get("EXPECT_AC", "4"))


def psql(sql: str) -> str:
    # Pass SQL on stdin to avoid shell-quoting pitfalls.
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -At -F"\t"',
    ]
    return subprocess.check_output(
        cmd,
        input=sql if sql.endswith(";") else sql + ";",
        text=True,
        cwd=os.environ.get("REPO_ROOT", "."),
    )


def scalar(sql: str) -> int:
    line = psql(sql).strip().splitlines()[0]
    return int(line.split("\t")[0])


def rows(sql: str) -> list[list[str]]:
    out = psql(sql).strip()
    if not out:
        return []
    return [line.split("\t") for line in out.splitlines() if line.strip()]


def main() -> int:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.chdir(root)
    os.environ["REPO_ROOT"] = root

    hl = scalar("SELECT COUNT(*) FROM human_labels")
    ac = scalar("SELECT COUNT(*) FROM leads WHERE status='AI_CONFIRMED'")
    reviews = scalar("SELECT COUNT(*) FROM label_reviews")
    disc_all = scalar(
        "SELECT COUNT(*) FROM commercial_discovery_candidates WHERE discovery_version='disc_v6'"
    )
    disc_new = scalar(
        "SELECT COUNT(*) FROM commercial_discovery_candidates "
        f"WHERE discovery_version='disc_v6' AND created_at >= '{PATH_B_AT}'"
    )
    scores = scalar("SELECT COUNT(*) FROM message_scores")
    messages = scalar("SELECT COUNT(*) FROM messages")
    actionable_t = scalar(
        "SELECT COUNT(*) FROM human_labels WHERE commercially_actionable IS TRUE"
    )
    actionable_f = scalar(
        "SELECT COUNT(*) FROM human_labels WHERE commercially_actionable IS FALSE"
    )
    actionable_null = scalar(
        "SELECT COUNT(*) FROM human_labels WHERE commercially_actionable IS NULL"
    )

    by_label = {
        r[0]: int(r[1])
        for r in rows("SELECT label, COUNT(*)::int FROM human_labels GROUP BY 1")
        if len(r) >= 2
    }
    by_fp = {
        (r[0] if r[0] else "(null)"): int(r[1])
        for r in rows(
            "SELECT COALESCE(fp_class, '(null)'), COUNT(*)::int "
            "FROM human_labels GROUP BY 1 ORDER BY 2 DESC"
        )
        if len(r) >= 2
    }

    # Optuna/ML readiness heuristic (offline only — does not flip gates).
    gaps: list[str] = []
    if disc_new == 0:
        gaps.append("no_NEW_disc_v6_since_path_b — no live positive outcomes for discovery optimization")
    if actionable_t < 50:
        gaps.append(
            f"commercially_actionable=true only {actionable_t} — weak commercial target for supervised ML"
        )
    if actionable_null > hl // 2:
        gaps.append("majority of human_labels lack commercially_actionable flag")
    if reviews < 200:
        gaps.append("label_reviews sparse relative to independent-validation needs")
    gaps.append("ml_training_enabled must remain false until holdout + leakage controls exist")
    gaps.append("Optuna/SHAP/LLM scaffolding must stay dormant from production discovery")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "OFFLINE_PREP_READONLY",
        "isolation": {
            "human_labels": hl,
            "AI_CONFIRMED": ac,
            "expect": {"human_labels": EXPECT_HL, "AI_CONFIRMED": EXPECT_AC},
            "ok": hl == EXPECT_HL and ac == EXPECT_AC,
        },
        "inventory": {
            "messages": messages,
            "message_scores": scores,
            "label_reviews": reviews,
            "disc_v6_all_historical": disc_all,
            "disc_v6_new_since_path_b": disc_new,
            "human_labels_by_label": by_label,
            "human_labels_by_fp_class": by_fp,
            "commercially_actionable": {
                "true": actionable_t,
                "false": actionable_f,
                "null": actionable_null,
            },
        },
        "optuna_ml_readiness": {
            "ready_for_production_optuna": False,
            "ready_for_production_ml": False,
            "ready_for_shap": False,
            "ready_for_llm_augmentation": False,
            "gaps": gaps,
            "rationale": (
                "Labels exist (provisional + independent reviews) but live disc_v6 NEW outcomes "
                "are absent post-path_b; commercial actionable positives are rare; "
                "deterministic RESIDUAL_SCARCITY observation must continue before optimization."
            ),
        },
    }
    json.dump(report, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
