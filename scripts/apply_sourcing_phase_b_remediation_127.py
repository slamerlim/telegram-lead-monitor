#!/usr/bin/env python3
"""Evidence 127 — Apply E124 Steps A–F (Phase B remediation).

Authorized as remediation of the already-approved Option B sourcing experiment
(E121). Disables resolve-fail rooms 137/140; replaces wrong entities 138/141;
re-queues days=14 for 138/139/141. No cursor wipe on Phase A; no path_b/c;
no scoring.yaml; no human_labels; no ML/shadow; no invite autofix.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text as sql_text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.db import SessionLocal
from shared.models import Community, ScanRun
from shared.redis_bus import RedisBus, SCAN_STREAM
from shared.settings import get_settings

EV = "127"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-sourcing-phase-b-remediation-applied.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-sourcing-phase-b-remediation-applied.json"
EXPECT_HL = 1524
EXPECT_AC = 4
PATH_B_AT = datetime.fromisoformat("2026-09-26T06:53:16+00:00")
T0_AT = datetime.fromisoformat("2026-09-27T10:16:46.876011+00:00")

DISABLE_IDS = (137, 140)
REPLACE_SPECS = (
    {
        "id": 138,
        "from_ref": "@hummingbot",
        "to_ref": "@hummingbot_io",
        "to_username": "hummingbot_io",
    },
    {
        "id": 141,
        "from_ref": "@JesseTrade",
        "to_ref": "@jesse_trade",
        "to_username": "jesse_trade",
    },
)
SCAN_IDS = (138, 139, 141)  # replaced + NautilusTrader keep/verify-join


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


def _community_snapshot(row: Community | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "telegram_ref": row.telegram_ref,
        "username": row.username,
        "name": row.name,
        "kind": row.kind,
        "enabled": bool(row.enabled),
        "resolve_status": row.resolve_status,
        "telegram_chat_id": row.telegram_chat_id,
        "last_message_id": row.last_message_id,
        "last_error": (row.last_error or "")[:200] or None,
    }


async def _iso(session) -> dict[str, int]:
    row = dict(
        (
            await session.execute(
                sql_text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM human_labels) AS human_labels,
                      (SELECT COUNT(*) FROM leads WHERE status='AI_CONFIRMED') AS ai_confirmed,
                      (SELECT COUNT(*) FROM messages) AS messages,
                      (SELECT COUNT(*) FROM message_scores) AS message_scores,
                      (SELECT COUNT(*) FROM commercial_discovery_candidates
                         WHERE discovery_version='disc_v6'
                           AND created_at >= CAST(:path_b AS timestamptz)) AS disc_v6_new,
                      (SELECT COUNT(*) FROM messages
                         WHERE created_at >= CAST(:t0 AS timestamptz)) AS messages_since_t0,
                      (SELECT COUNT(*) FROM message_scores
                         WHERE scored_at >= CAST(:t0 AS timestamptz)) AS scores_since_t0,
                      (SELECT COUNT(*) FROM commercial_discovery_candidates
                         WHERE discovery_version='disc_v6'
                           AND created_at >= CAST(:t0 AS timestamptz)) AS disc_v6_since_t0
                    """
                ),
                {"path_b": PATH_B_AT, "t0": T0_AT},
            )
        )
        .mappings()
        .one()
    )
    return {k: int(v) for k, v in row.items()}


async def _enqueue_scan(
    session, bus: RedisBus, community_id: int, days: int = 14
) -> dict[str, Any]:
    existing = await session.scalar(
        select(ScanRun.id)
        .where(
            ScanRun.community_id == community_id,
            ScanRun.status.in_(["queued", "running"]),
        )
        .limit(1)
    )
    if existing:
        return {
            "community_id": community_id,
            "skipped": True,
            "reason": "already_queued",
            "existing_run_id": int(existing),
        }
    run = ScanRun(community_id=community_id, days=days, status="queued")
    session.add(run)
    await session.commit()
    await session.refresh(run)
    await bus.publish(
        SCAN_STREAM,
        {"run_id": run.id, "community_id": community_id, "days": days},
    )
    return {
        "community_id": community_id,
        "run_id": run.id,
        "days": days,
        "status": run.status,
    }


async def main() -> int:
    dry = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc)
    head = _git_head()
    settings = get_settings()

    auth = {
        "authorized_by": "owner",
        "auth_record": (
            "User previously approved Option B sourcing (A/B/C parallel execute; E121). "
            "This turn applies E124 Steps A–F as remediation of that already-authorized "
            "sourcing experiment (bad usernames / wrong entities / empty Phase B rooms). "
            "NOT a new expansion auth; NOT Evidence-105 bridge; NOT UpdateGoal complete."
        ),
        "parent_proposal": "docs/audit/evidence/124-sourcing-phase-b-remediation-proposal.*",
        "parent_t0": "docs/audit/evidence/121-sourcing-experiment-t0.*",
        "authorized_at": now.isoformat(),
        "lever": (
            "communities.enabled=false for 137/140; telegram_ref/username replace "
            "138→@hummingbot_io 141→@jesse_trade; scan_runs days=14 for 138/139/141"
        ),
        "forbidden": [
            "path_b/path_c reopen",
            "scoring.yaml threshold loosen",
            "human_labels mutation",
            "ML/shadow enable",
            "last_message_id wipe on Phase A / non-target rows",
            "docker compose down -v",
            "invite autofix / JoinChannel autofix",
            "optional @commaschatru insert (Step D skipped — leave 137/140 disabled)",
            "UpdateGoal complete",
        ],
    }

    async with SessionLocal() as session:
        before_iso = await _iso(session)
        if (
            before_iso["human_labels"] != EXPECT_HL
            or before_iso["ai_confirmed"] != EXPECT_AC
        ):
            print(f"ISOLATION_DRIFT before: {before_iso}", file=sys.stderr)
            return 2

        before_rows: dict[int, dict[str, Any] | None] = {}
        for cid in range(137, 142):
            before_rows[cid] = _community_snapshot(await session.get(Community, cid))

        # UNIQUE pre-check for replacement refs
        unique_conflicts: list[dict[str, Any]] = []
        for spec in REPLACE_SPECS:
            hit = await session.scalar(
                select(Community).where(
                    Community.telegram_ref == spec["to_ref"],
                    Community.id != spec["id"],
                )
            )
            if hit is not None:
                unique_conflicts.append(
                    {
                        "wanted_ref": spec["to_ref"],
                        "existing_id": hit.id,
                        "existing_ref": hit.telegram_ref,
                    }
                )

        step_a: list[dict[str, Any]] = []
        step_b: list[dict[str, Any]] = []
        step_e: list[dict[str, Any]] = []
        cursor_checks: list[dict[str, Any]] = []
        disable_on_fail: list[dict[str, Any]] = []

        if not dry:
            # Step A — DISABLE 137 / 140 (preserve last_message_id)
            for cid in DISABLE_IDS:
                row = await session.get(Community, cid)
                if row is None:
                    step_a.append({"id": cid, "error": "not_found"})
                    continue
                cursor_before = row.last_message_id
                row.enabled = False
                assert row.last_message_id == cursor_before
                step_a.append(
                    {
                        "id": cid,
                        "telegram_ref": row.telegram_ref,
                        "enabled": False,
                        "last_message_id": row.last_message_id,
                        "resolve_status_kept": row.resolve_status,
                        "last_error_kept": (row.last_error or "")[:120] or None,
                    }
                )
                cursor_checks.append(
                    {
                        "id": cid,
                        "step": "A_disable",
                        "last_message_id_before": cursor_before,
                        "last_message_id_after": row.last_message_id,
                        "unchanged": row.last_message_id == cursor_before,
                    }
                )

            # Step B — REPLACE refs (clear resolve state; fresh cursor NULL for new entity)
            for spec in REPLACE_SPECS:
                row = await session.get(Community, spec["id"])
                if row is None:
                    step_b.append({"id": spec["id"], "error": "not_found"})
                    continue
                if unique_conflicts:
                    # Do not apply conflicting replace; disable instead
                    row.enabled = False
                    disable_on_fail.append(
                        {
                            "id": spec["id"],
                            "reason": "unique_telegram_ref_conflict",
                            "conflicts": unique_conflicts,
                        }
                    )
                    step_b.append(
                        {
                            "id": spec["id"],
                            "action": "disable_on_conflict",
                            "from": row.telegram_ref,
                            "wanted": spec["to_ref"],
                        }
                    )
                    continue
                if (row.telegram_ref or "").casefold() != spec["from_ref"].casefold():
                    # Unexpected current ref — document and disable rather than thrash
                    row.enabled = False
                    disable_on_fail.append(
                        {
                            "id": spec["id"],
                            "reason": "unexpected_from_ref",
                            "actual": row.telegram_ref,
                            "expected_from": spec["from_ref"],
                        }
                    )
                    step_b.append(
                        {
                            "id": spec["id"],
                            "action": "disable_unexpected_ref",
                            "actual": row.telegram_ref,
                        }
                    )
                    continue

                cursor_before = row.last_message_id
                from_ref = row.telegram_ref
                row.telegram_ref = spec["to_ref"]
                row.username = spec["to_username"]
                row.resolve_status = "pending"
                row.last_error = None
                row.telegram_chat_id = None
                row.name = None
                row.kind = None
                # New entity: ensure fresh cursor (NULL/0 only for this replaced row)
                row.last_message_id = None
                step_b.append(
                    {
                        "id": spec["id"],
                        "action": "replaced",
                        "from": from_ref,
                        "to": spec["to_ref"],
                        "username": spec["to_username"],
                        "resolve_status": "pending",
                        "last_message_id_before": cursor_before,
                        "last_message_id_after": None,
                        "enabled": bool(row.enabled),
                    }
                )
                cursor_checks.append(
                    {
                        "id": spec["id"],
                        "step": "B_replace_fresh_cursor",
                        "last_message_id_before": cursor_before,
                        "last_message_id_after": None,
                        "note": "fresh cursor for new telegram_ref entity only",
                    }
                )

            await session.commit()

            # Step E — enqueue days=14 for 138, 139, 141 (skip if disabled)
            bus = RedisBus(settings.redis_url)
            try:
                for cid in SCAN_IDS:
                    row = await session.get(Community, cid)
                    if row is None or not row.enabled:
                        step_e.append(
                            {
                                "community_id": cid,
                                "skipped": True,
                                "reason": "disabled_or_missing",
                            }
                        )
                        continue
                    step_e.append(await _enqueue_scan(session, bus, cid, days=14))
            finally:
                await bus.close()

        after_iso = await _iso(session)
        after_rows: dict[int, dict[str, Any] | None] = {}
        for cid in range(137, 142):
            after_rows[cid] = _community_snapshot(await session.get(Community, cid))

    isolation_ok = (
        after_iso["human_labels"] == EXPECT_HL
        and after_iso["ai_confirmed"] == EXPECT_AC
    )
    cursors_ok = all(
        c.get("unchanged", True) for c in cursor_checks if c.get("step") == "A_disable"
    )

    step_c_note = {
        "id": 139,
        "action": "keep_enabled_queue_days14",
        "verify_join": (
            "If scan days=14 still messages_seen=0, VERIFY-JOIN collector session "
            "membership for @NautilusTrader under owner auth (no invite autofix). "
            "If still 0 after join+rescan → DISABLE rather than invent alternate refs."
        ),
        "join_applied_this_turn": False,
    }
    step_d_note = {
        "action": "SKIPPED",
        "reason": "Owner apply instructions leave 137/140 disabled; no @commaschatru insert",
    }

    payload = {
        "evidence_id": EV,
        "experiment_id": "SOURCING_PHASE_B_REMEDIATION_APPLIED_127",
        "generated_at": now.isoformat(),
        "head": head,
        "dry_run": dry,
        "t0": T0_AT.isoformat(),
        "parent": [
            "Evidence 121 SOURCING_T0_APPLIED",
            "Evidence 123 PHASE_B_INTAKE_GAP_DOCUMENTED",
            "Evidence 124 PROPOSAL → this apply",
        ],
        "mode": "CONTINUOUS_OBSERVE",
        "decision": "DRY_RUN" if dry else "SOURCING_PHASE_B_REMEDIATION_APPLIED",
        "OWNER_AUTH": auth,
        "baseline_observe_note": (
            "Host ran ./scripts/observe_abc_watch.sh --once before apply; "
            "expect OBSERVE_OK hl=1524 ac=4 recorded in apply turn report."
        ),
        "isolation_before": before_iso,
        "isolation_after": after_iso,
        "isolation_ok": isolation_ok,
        "communities_before": before_rows,
        "communities_after": after_rows,
        "step_a_disable": step_a if not dry else {"planned": list(DISABLE_IDS)},
        "step_b_replace": step_b if not dry else {"planned": list(REPLACE_SPECS)},
        "step_c_nautilus": step_c_note,
        "step_d_optional_insert": step_d_note,
        "step_e_scans": step_e if not dry else {"planned": list(SCAN_IDS), "days": 14},
        "step_f_isolation": {
            "expect_hl": EXPECT_HL,
            "expect_ac": EXPECT_AC,
            "ok": isolation_ok,
        },
        "unique_conflicts": unique_conflicts,
        "disable_on_fail": disable_on_fail,
        "cursor_checks": cursor_checks,
        "phase_a_cursors_preserved_on_disable": cursors_ok,
        "business": {
            "outreach": False,
            "ml_go": False,
            "threshold_loosen": False,
            "path_b_path_c_reopened": False,
            "discovery_semantic_change": False,
            "e105_bridge": False,
            "human_labels_mutated": after_iso["human_labels"]
            != before_iso["human_labels"],
            "scoring_yaml_touched": False,
            "autofix_invites": False,
            "cursor_wipe_phase_a_or_other": False,
            "freeze_expand": False,
            "timed_remeasure_invented": False,
            "update_goal_complete": False,
            "code_deploy_required": False,
        },
        "next": (
            "Stay CONTINUOUS OBSERVE. Watch scan_runs for 138/139/141; classify any "
            "NEW disc_v6 (Trigger A) without path reopen. If 139 still 0 after days=14 "
            "→ note VERIFY-JOIN; if replace resolves fail → disable that id. "
            "Do not UpdateGoal complete."
        ),
    }

    lines = [
        f"Evidence {EV}: Phase B remediation APPLY (E124 Steps A–F)",
        "experiment_id=SOURCING_PHASE_B_REMEDIATION_APPLIED_127",
        f"decision={payload['decision']}",
        f"measure_at={now.isoformat()} head={head} dry_run={dry}",
        f"t0={T0_AT.isoformat()} (E121)",
        "parent=Evidence 121 + 123 + 124 proposal",
        "",
        "=== 0. OWNER AUTH (remediation of authorized E121 Option B) ===",
        f"  {auth['auth_record']}",
        f"  lever={auth['lever']}",
        f"  forbidden={auth['forbidden']}",
        "  Step D optional insert SKIPPED (leave 137/140 disabled)",
        "",
        "=== 1. ISOLATION (Step F) ===",
        f"  before hl={before_iso['human_labels']} ac={before_iso['ai_confirmed']} "
        f"disc_v6_new={before_iso['disc_v6_new']} msgs_t0={before_iso['messages_since_t0']} "
        f"scores_t0={before_iso['scores_since_t0']} disc_t0={before_iso['disc_v6_since_t0']}",
        f"  after  hl={after_iso['human_labels']} ac={after_iso['ai_confirmed']} "
        f"isolation_ok={isolation_ok}",
        "",
        "=== 2. COMMUNITIES BEFORE (137–141) ===",
        f"  {json.dumps(before_rows, default=str)}",
        "",
        "=== 3. STEP A — DISABLE 137 / 140 ===",
        f"  {step_a if not dry else list(DISABLE_IDS)}",
        f"  phase_a_style_cursors_preserved={cursors_ok}",
        "",
        "=== 4. STEP B — REPLACE 138 / 141 ===",
        f"  {step_b if not dry else list(REPLACE_SPECS)}",
        f"  unique_conflicts={unique_conflicts}",
        f"  disable_on_fail={disable_on_fail}",
        "",
        "=== 5. STEP C — 139 @NautilusTrader KEEP + NOTE VERIFY-JOIN ===",
        f"  {step_c_note}",
        "",
        "=== 6. STEP D — OPTIONAL INSERT ===",
        f"  {step_d_note}",
        "",
        "=== 7. STEP E — SCAN days=14 (138,139,141) ===",
        f"  {step_e if not dry else list(SCAN_IDS)}",
        "",
        "=== 8. COMMUNITIES AFTER (137–141) ===",
        f"  {json.dumps(after_rows, default=str)}",
        "",
        "=== 9. BUSINESS / LOCKS ===",
        f"  {payload['business']}",
        "",
        "=== 10. NEXT ===",
        f"  {payload['next']}",
        "",
    ]
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(OUT_TXT.read_text())
    return 0 if isolation_ok and (dry or cursors_ok) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
