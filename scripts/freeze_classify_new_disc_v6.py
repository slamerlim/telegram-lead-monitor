#!/usr/bin/env python3
"""Trigger A helper: freeze + quality-classify NEW disc_v6 candidates (read-only).

DORMANT until invoked after a watcher wake / Trigger A.
Does not alter discovery, scoring, labels, or AI_CONFIRMED.

Usage:
  python3 scripts/freeze_classify_new_disc_v6.py
  python3 scripts/freeze_classify_new_disc_v6.py --out /tmp/trigger_a_freeze.json

Exit:
  0 — no NEW candidates (TRIGGER_A_ABSENT) or freeze written successfully
  2 — query/runtime failure
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

PATH_B_AT = os.environ.get("PATH_B_AT", "2026-09-26T06:53:16Z")

QUALITY_CLASSES = [
    "genuine_buyer_project",
    "recruiter",
    "employment",
    "provider_vendor",
    "support",
    "aggregator",
    "marketplace_gig",
    "other_contamination",
    "uncertain",
]


def psql_json(sql: str) -> object:
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -At -t',
    ]
    out = subprocess.check_output(
        cmd,
        input=sql if sql.endswith(";") else sql + ";",
        text=True,
        cwd=os.environ.get("REPO_ROOT", "."),
    ).strip()
    if not out or out == "":
        return None
    return json.loads(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path-b-at", default=PATH_B_AT)
    ap.add_argument("--out", default="", help="Optional JSON freeze path")
    args = ap.parse_args()

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.chdir(root)
    os.environ["REPO_ROOT"] = root

    sql = f"""
SELECT COALESCE(json_agg(row_to_json(t) ORDER BY t.created_at, t.candidate_id), '[]'::json)
FROM (
  SELECT
    c.id AS candidate_id,
    c.seed_message_id,
    c.community_id,
    c.author_id,
    c.discovery_version,
    c.trigger_type,
    c.trigger_score,
    c.scorer_tier,
    c.status,
    c.skip_reason,
    c.episode_id,
    c.signals_json,
    c.created_at,
    LEFT(COALESCE(m.text, ''), 2000) AS message_text,
    s.tier AS score_tier,
    s.score,
    s.lead_type,
    com.name AS community_name,
    com.username AS community_username
  FROM commercial_discovery_candidates c
  JOIN messages m ON m.id = c.seed_message_id
  LEFT JOIN message_scores s ON s.message_id = m.id
  LEFT JOIN communities com ON com.id = c.community_id
  WHERE c.discovery_version = 'disc_v6'
    AND c.created_at >= '{args.path_b_at}'
) t
"""
    try:
        rows = psql_json(sql)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(json.dumps({"status": "QUERY_FAILURE", "error": str(e)}), file=sys.stderr)
        return 2

    if not rows:
        report = {
            "status": "TRIGGER_A_ABSENT",
            "at": datetime.now(timezone.utc).isoformat(),
            "path_b_at": args.path_b_at,
            "new_count": 0,
            "note": "No NEW disc_v6 since path_b — remain OBSERVE; do not reopen paths.",
        }
        print(json.dumps(report, indent=2))
        return 0

    frozen = []
    for r in rows:
        frozen.append(
            {
                **r,
                "quality_classification": {
                    "assigned": None,
                    "allowed_classes": QUALITY_CLASSES,
                    "buyer_project_evidence": None,
                    "commercial_intent": None,
                    "evidence_strength": None,
                    "notes": None,
                },
                "path_action": "DO_NOT_REOPEN_path_b_or_path_c — one candidate is one observation",
            }
        )

    report = {
        "status": "TRIGGER_A_FROZEN",
        "at": datetime.now(timezone.utc).isoformat(),
        "path_b_at": args.path_b_at,
        "new_count": len(frozen),
        "instruction": (
            "Quality-classify each frozen candidate. Do not modify discovery semantics. "
            "Paths path_b/path_c remain FROZEN unless systematic loss evidence appears."
        ),
        "candidates": frozen,
    }
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
