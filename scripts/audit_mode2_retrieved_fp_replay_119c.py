#!/usr/bin/env python3
"""Evidence 119c — Mode-1/2 replay of RETRIEVED FPs mid=60620, mid=4780480.

DB-backed evaluate_discovery v4 vs v6. Does NOT mutate human_labels.
Does NOT re-adjudicate e118b batch. Writes Evidence 119c artifacts only.

Owner auth for Mode-2 semantic hardening is recorded in the evidence payload
(user approved Option C / Mode-2 investigation + minimal hardening).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V6,
    evaluate_discovery,
)
from shared.db import SessionLocal
from shared.models import Message
from shared.settings import get_settings
from scripts.audit_disc_v6_refined_population_cycle2 import first_loss

EV = "119c"
MIDS = (60620, 4780480)
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-retrieved-fp-mode2-root-cause.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-retrieved-fp-mode2-root-cause.json"
OUT_MD = ROOT / f"docs/audit/evidence/{EV}-retrieved-fp-mode2-root-cause.md"
EXPECT_HL = 1524
EXPECT_AC = 4


def _git_head() -> str:
    env = (os.environ.get("GIT_HEAD") or "").strip()
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _feat_snapshot(feats: Any) -> dict[str, Any]:
    d = feats.to_signals_dict() if hasattr(feats, "to_signals_dict") else {}
    return {
        "eligible": bool(feats.eligible),
        "first_loss": first_loss(feats),
        "trigger_type": getattr(feats, "trigger_type", None),
        "matched_paths": list(getattr(feats, "matched_paths", None) or []),
        "veto_categories": list(getattr(feats, "veto_categories", None) or []),
        "hard_exclude": bool(getattr(feats, "hard_exclude", False)),
        "exclude_reasons": list(getattr(feats, "exclude_reasons", None) or []),
        "aggregator_signal": bool(getattr(feats, "aggregator_signal", False)),
        "gig_project_carve": bool(getattr(feats, "gig_project_carve", False)),
        "ownership_signal": bool(getattr(feats, "ownership_signal", False)),
        "project_scope": bool(getattr(feats, "project_scope", False)),
        "budget_signal": bool(getattr(feats, "budget_signal", False)),
        "true_budget_signal": bool(getattr(feats, "true_budget_signal", False)),
        "direct_request_signal": bool(getattr(feats, "direct_request_signal", False)),
        "repair_signal": bool(getattr(feats, "repair_signal", False)),
        "buyer_direction": getattr(feats, "buyer_direction", None),
        "signals": d,
    }


async def main() -> int:
    now = datetime.now(timezone.utc)
    head = _git_head()
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)

    async with SessionLocal() as session:
        iso = dict(
            (
                await session.execute(
                    sql_text(
                        """
                        SELECT
                          (SELECT COUNT(*) FROM human_labels) AS human_labels,
                          (SELECT COUNT(*) FROM leads WHERE status='AI_CONFIRMED') AS ai_confirmed
                        """
                    )
                )
            )
            .mappings()
            .one()
        )
        hl, ac = int(iso["human_labels"]), int(iso["ai_confirmed"])
        if hl != EXPECT_HL or ac != EXPECT_AC:
            print(f"ISOLATION_DRIFT: {hl}/{ac}", file=sys.stderr)
            return 2

        rows = {
            int(r.id): r
            for r in (
                await session.execute(select(Message).where(Message.id.in_(list(MIDS))))
            ).scalars().all()
        }

    replays: list[dict[str, Any]] = []
    for mid in MIDS:
        msg = rows.get(mid)
        text = (msg.text if msg else "") or ""
        community_id = getattr(msg, "community_id", None) if msg else None
        entry: dict[str, Any] = {
            "message_id": mid,
            "community_id": community_id,
            "text_preview": " ".join(text.split())[:400],
            "text_len": len(text),
            "versions": {},
        }
        for ver in (DISCOVERY_VERSION_V4, DISCOVERY_VERSION_V6):
            feats = evaluate_discovery(scorer, text, version=ver)
            entry["versions"][ver] = _feat_snapshot(feats)
        replays.append(entry)

    # Hypotheses from Option C research (24f1aa6a)
    hyp = {
        60620: (
            "LaborX NEW PROJECT listing reaches path E (v6_budget_deliverable) "
            "despite marketplace shape; JOB_VACANCY FP if eligible."
        ),
        4780480: (
            "MEXC FOMO commentary reaches path E via ownership_signal on bare "
            "'my strategy' + domain; OFF_DOMAIN FP if eligible."
        ),
    }

    findings: list[dict[str, Any]] = []
    for r in replays:
        mid = r["message_id"]
        v6 = r["versions"][DISCOVERY_VERSION_V6]
        v4 = r["versions"][DISCOVERY_VERSION_V4]
        findings.append(
            {
                "message_id": mid,
                "hypothesis": hyp[mid],
                "v4_eligible": v4["eligible"],
                "v6_eligible": v6["eligible"],
                "v6_trigger": v6["trigger_type"],
                "v6_paths": v6["matched_paths"],
                "v6_first_loss": v6["first_loss"],
                "v6_ownership": v6["ownership_signal"],
                "v6_aggregator": v6["aggregator_signal"],
                "v6_carve": v6["gig_project_carve"],
                "v6_vetoes": v6["veto_categories"],
                "root_cause_notes": [],
            }
        )

    # Annotate root causes from signal traces
    for f, r in zip(findings, replays):
        v6 = r["versions"][DISCOVERY_VERSION_V6]
        mid = f["message_id"]
        if mid == 60620:
            if v6["eligible"] and v6["aggregator_signal"] is False:
                f["root_cause_notes"].append(
                    "aggregator_signal false despite LaborX template — GIG_RX/scorer gap"
                )
            if v6["eligible"] and "E" in (v6["matched_paths"] or []):
                f["root_cause_notes"].append(
                    "path E fired; listing should be vetoed when not gig_project_carve"
                )
            if not v6["eligible"] and "job_aggregator" in (v6["veto_categories"] or []):
                f["root_cause_notes"].append(
                    "CURRENT CODE already vetoes via job_aggregator — freeze RETRIEVED may be stale or race; still harden listing veto for defense-in-depth"
                )
            if v6["eligible"] and v6["gig_project_carve"]:
                f["root_cause_notes"].append(
                    "unexpected FO carve on NEW PROJECT template (carve should require FO listing RX)"
                )
        if mid == 4780480:
            if v6["ownership_signal"] and v6["eligible"]:
                f["root_cause_notes"].append(
                    "ownership_signal true on bare 'my strategy' trading advice → path E"
                )
            if not v6["eligible"]:
                f["root_cause_notes"].append(
                    "CURRENT CODE already ineligible — still narrow path-E ownership away from bare strategy advice"
                )

    hardening_auth = {
        "authorized_by": "owner",
        "auth_record": (
            "User approved plan+execute A/B/C including Mode-2 investigation of "
            "RETRIEVED FPs 60620/4780480 with minimal defensive hardening if falsified"
        ),
        "authorized_at": now.isoformat(),
        "scope": [
            "replay evidence 119c",
            "LaborX listing veto when not carve (no path_b/c reopen)",
            "narrow path-E ownership away from bare 'my strategy' advice",
        ],
        "forbidden": [
            "path_b/path_c conjunct reopen",
            "scoring.yaml threshold loosen",
            "human_labels mutation",
            "ML enable / ML GO claim",
        ],
    }

    payload = {
        "evidence_id": EV,
        "experiment_id": "MODE2_RETRIEVED_FP_REPLAY_119C",
        "generated_at": now.isoformat(),
        "head": head,
        "mode2_auth": hardening_auth,
        "isolation": {
            "human_labels": hl,
            "AI_CONFIRMED": ac,
            "isolation_ok": True,
        },
        "message_ids": list(MIDS),
        "replays": replays,
        "findings": findings,
        "business": {
            "outreach": False,
            "ml_go": False,
            "threshold_loosen": False,
            "path_b_path_c_reopened": False,
            "human_labels_mutated": False,
            "discovery_semantic_change_pending": True,
        },
        "next": (
            "Apply minimal discovery.py hardening under mode2_auth; run "
            "pytest tests/test_discovery_laborx_veto_carve_v6.py; rebuild analyzer."
        ),
    }

    lines = [
        f"Evidence {EV}: Mode-2 RETRIEVED FP root-cause (60620 LaborX, 4780480 MEXC FOMO)",
        "experiment_id=MODE2_RETRIEVED_FP_REPLAY_119C",
        "decision=MODE2_AUTH_REPLAY_THEN_HARDEN",
        f"generated_at={now.isoformat()} head={head}",
        "",
        "=== 0. AUTH ===",
        f"  {hardening_auth['auth_record']}",
        f"  scope={hardening_auth['scope']}",
        "",
        "=== 1. ISOLATION ===",
        f"  human_labels={hl} AI_CONFIRMED={ac} isolation_ok=True",
        "",
        "=== 2. REPLAY SUMMARY ===",
    ]
    for f in findings:
        lines.append(
            f"  mid={f['message_id']} v4_elig={f['v4_eligible']} v6_elig={f['v6_eligible']} "
            f"trigger={f['v6_trigger']} paths={f['v6_paths']} loss={f['v6_first_loss']} "
            f"own={f['v6_ownership']} agg={f['v6_aggregator']} carve={f['v6_carve']} "
            f"vetoes={f['v6_vetoes']}"
        )
        for note in f["root_cause_notes"]:
            lines.append(f"    note: {note}")
    lines += ["", "=== 3. NEXT ===", f"  {payload['next']}", ""]

    md = [
        f"# Evidence {EV}: Mode-2 RETRIEVED FP root-cause",
        "",
        f"- Generated: `{now.isoformat()}` HEAD `{head}`",
        "- Isolation: human_labels=1524 AI_CONFIRMED=4",
        "- Auth: owner approved Mode-2 investigation + minimal hardening",
        "- Locks: no path_b/c reopen; no scoring.yaml loosen; no human_labels mutation",
        "",
        "## Findings",
        "",
    ]
    for f in findings:
        md.append(f"### mid={f['message_id']}")
        md.append(f"- Hypothesis: {f['hypothesis']}")
        md.append(
            f"- v4 eligible={f['v4_eligible']}; v6 eligible={f['v6_eligible']} "
            f"trigger={f['v6_trigger']} paths={f['v6_paths']}"
        )
        md.append(f"- Signals: ownership={f['v6_ownership']} aggregator={f['v6_aggregator']} carve={f['v6_carve']}")
        md.append(f"- Vetoes: {f['v6_vetoes']}")
        for note in f["root_cause_notes"]:
            md.append(f"- {note}")
        md.append("")

    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    OUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(OUT_TXT.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
