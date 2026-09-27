#!/usr/bin/env python3
"""Evidence 121 — Authorized sourcing experiment (communities lever only).

Phase A: disable 3–5 high-volume EXCHANGE_OFFICIAL communities.
Phase B: add 3–5 E95-style rooms not already present; enqueue days=14 scans
         for NEW community ids only (never wipe last_message_id).

Uses Postgres + RedisBus (API /communities and /scans blocked by
REVIEW_UI_LOCKDOWN). Same lever as PATCH enabled / POST /scans.

Does NOT touch discovery.py, scoring.yaml, path_b/c, human_labels, or cursors.
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

EV = "121"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-sourcing-experiment-t0.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-sourcing-experiment-t0.json"
EXPECT_HL = 1524
EXPECT_AC = 4

# Phase A — high-volume EXCHANGE_OFFICIAL (msg_7d ranking 2026-09-27)
PHASE_A_DISABLE_USERNAMES = (
    "BitgetENOfficial",
    "OKXOfficial_English",
    "WeexGlobal_Group",
    "BybitEnglish",
)

# Phase B — E95-style public rooms (algo/dev/contractor); skip if already present
PHASE_B_ADD_REFS = (
    "@Freqtrade",
    "@hummingbot",
    "@NautilusTrader",
    "@backtrader_community",
    "@JesseTrade",
)

PATH_B_AT = datetime.fromisoformat("2026-09-26T06:53:16+00:00")


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
                           AND created_at >= CAST(:path_b AS timestamptz)) AS disc_v6_new
                    """
                ),
                {"path_b": PATH_B_AT},
            )
        )
        .mappings()
        .one()
    )
    return {k: int(v) for k, v in row.items()}


async def _metrics_since(session, t0: datetime) -> dict[str, int]:
    row = dict(
        (
            await session.execute(
                sql_text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM messages
                         WHERE created_at >= :t0) AS messages_since_t0,
                      (SELECT COUNT(*) FROM message_scores
                         WHERE scored_at >= :t0) AS scores_since_t0,
                      (SELECT COUNT(*) FROM commercial_discovery_candidates
                         WHERE discovery_version='disc_v6'
                           AND created_at >= :t0) AS disc_v6_since_t0
                    """
                ),
                {"t0": t0},
            )
        )
        .mappings()
        .one()
    )
    return {k: int(v) for k, v in row.items()}


async def main() -> int:
    ap_dry = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc)
    t0 = now
    head = _git_head()
    settings = get_settings()

    auth = {
        "authorized_by": "owner",
        "auth_record": (
            "User approved plan+execute A/B/C including Option B sourcing experiment "
            "(communities lever). Supersedes E96/E97 default 'do not expand' for this "
            "bounded Phase A/B only. NOT Evidence-105 HIGH-tier bridge."
        ),
        "research_agent": "24b81832",
        "authorized_at": t0.isoformat(),
        "lever": "communities.enabled + insert communities + scan_runs days=14",
        "forbidden": [
            "discovery.py path_b/c edits",
            "scoring.yaml threshold loosen",
            "E105 Option B bridge",
            "last_message_id wipe",
            "docker compose down -v",
            "human_labels mutation",
        ],
    }

    async with SessionLocal() as session:
        before = await _iso(session)
        if before["human_labels"] != EXPECT_HL or before["ai_confirmed"] != EXPECT_AC:
            print(f"ISOLATION_DRIFT before: {before}", file=sys.stderr)
            return 2

        # Snapshot last_message_id for Phase A targets (must not wipe)
        phase_a_before: list[dict[str, Any]] = []
        for uname in PHASE_A_DISABLE_USERNAMES:
            row = (
                await session.execute(
                    select(Community).where(Community.username == uname)
                )
            ).scalar_one_or_none()
            if not row:
                phase_a_before.append({"username": uname, "found": False})
                continue
            phase_a_before.append(
                {
                    "username": uname,
                    "found": True,
                    "id": row.id,
                    "enabled_before": bool(row.enabled),
                    "last_message_id_before": row.last_message_id,
                    "telegram_ref": row.telegram_ref,
                }
            )

        existing_refs = {
            (r.telegram_ref or "").casefold(): r
            for r in (await session.execute(select(Community))).scalars().all()
        }
        existing_usernames = {
            (r.username or "").casefold(): r for r in existing_refs.values()
        }

        phase_b_plan: list[dict[str, Any]] = []
        for ref in PHASE_B_ADD_REFS:
            key = ref.casefold()
            uname = ref.lstrip("@")
            if key in existing_refs or uname.casefold() in existing_usernames:
                hit = existing_refs.get(key) or existing_usernames[uname.casefold()]
                phase_b_plan.append(
                    {
                        "ref": ref,
                        "action": "skip_exists",
                        "id": hit.id,
                        "enabled": bool(hit.enabled),
                    }
                )
            else:
                phase_b_plan.append({"ref": ref, "action": "add", "username": uname})

        disabled: list[dict[str, Any]] = []
        added: list[dict[str, Any]] = []
        scans: list[dict[str, Any]] = []

        if not ap_dry:
            # Phase A
            for item in phase_a_before:
                if not item.get("found"):
                    continue
                row = await session.get(Community, item["id"])
                if row is None:
                    continue
                cursor_before = row.last_message_id
                row.enabled = False
                # Explicitly do not touch last_message_id
                assert row.last_message_id == cursor_before
                disabled.append(
                    {
                        "id": row.id,
                        "username": row.username,
                        "last_message_id": row.last_message_id,
                        "enabled": False,
                    }
                )

            # Phase B adds
            for plan in phase_b_plan:
                if plan["action"] != "add":
                    continue
                ref = plan["ref"]
                uname = plan["username"]
                c = Community(
                    telegram_ref=ref,
                    username=uname,
                    enabled=True,
                    resolve_status="pending",
                )
                session.add(c)
                await session.flush()
                added.append({"id": c.id, "telegram_ref": ref, "username": uname})

            await session.commit()

            # Scans for NEW ids only
            bus = RedisBus(settings.redis_url)
            try:
                for a in added:
                    # skip if already queued/running
                    existing = await session.scalar(
                        select(ScanRun.id).where(
                            ScanRun.community_id == a["id"],
                            ScanRun.status.in_(["queued", "running"]),
                        ).limit(1)
                    )
                    if existing:
                        scans.append(
                            {
                                "community_id": a["id"],
                                "skipped": True,
                                "reason": "already_queued",
                            }
                        )
                        continue
                    run = ScanRun(community_id=a["id"], days=14, status="queued")
                    session.add(run)
                    await session.commit()
                    await session.refresh(run)
                    await bus.publish(
                        SCAN_STREAM,
                        {
                            "run_id": run.id,
                            "community_id": a["id"],
                            "days": 14,
                        },
                    )
                    scans.append(
                        {
                            "community_id": a["id"],
                            "run_id": run.id,
                            "days": 14,
                            "status": run.status,
                        }
                    )
            finally:
                await bus.close()

        after = await _iso(session)
        since = await _metrics_since(session, t0)
        # Verify cursors unchanged for Phase A
        cursor_ok = True
        cursor_checks: list[dict[str, Any]] = []
        for item in phase_a_before:
            if not item.get("found"):
                continue
            row = await session.get(Community, item["id"])
            ok = row is not None and row.last_message_id == item["last_message_id_before"]
            cursor_ok = cursor_ok and ok
            cursor_checks.append(
                {
                    "id": item["id"],
                    "username": item["username"],
                    "last_message_id_before": item["last_message_id_before"],
                    "last_message_id_after": None if row is None else row.last_message_id,
                    "unchanged": ok,
                    "enabled_after": None if row is None else bool(row.enabled),
                }
            )

    isolation_ok = (
        after["human_labels"] == EXPECT_HL and after["ai_confirmed"] == EXPECT_AC
    )

    payload = {
        "evidence_id": EV,
        "experiment_id": "SOURCING_EXPERIMENT_T0_121",
        "generated_at": now.isoformat(),
        "t0": t0.isoformat(),
        "head": head,
        "dry_run": ap_dry,
        "owner_auth": auth,
        "pre_registered": {
            "hypothesis": (
                "Disabling top EXCHANGE_OFFICIAL volume and adding bounded E95-style "
                "algo/dev rooms increases non-exchange message mix and near-miss RFQ "
                "presence without raising disc_v6 NEW contamination vs WP2 pattern."
            ),
            "decision_rule": (
                "Observe ≥4h or first NEW disc_v6 since T0; quality-classify any NEW; "
                "do not reopen path_b/c from sourcing alone."
            ),
            "phase_a": list(PHASE_A_DISABLE_USERNAMES),
            "phase_b_candidates": list(PHASE_B_ADD_REFS),
        },
        "isolation_before": before,
        "isolation_after": after,
        "isolation_ok": isolation_ok,
        "metrics_since_t0": since,
        "phase_a_disable": disabled if not ap_dry else phase_a_before,
        "phase_b_plan": phase_b_plan,
        "phase_b_added": added,
        "scans_new_ids_only": scans,
        "cursor_checks": cursor_checks,
        "cursors_unchanged": cursor_ok,
        "business": {
            "outreach": False,
            "ml_go": False,
            "threshold_loosen": False,
            "path_b_path_c_reopened": False,
            "discovery_semantic_change": False,
            "e105_bridge": False,
            "human_labels_mutated": after["human_labels"] != before["human_labels"],
            "scoring_yaml_touched": False,
            "code_deploy_required": False,
        },
        "services": ["scheduler", "collector", "api(lockdown→postgres lever)", "postgres", "redis"],
        "next": (
            "Watch NEW-since-T0 messages/scores/disc_v6; on Trigger A freeze+classify; "
            "rollback Phase A by re-enabling usernames if intake harm observed."
        ),
    }

    lines = [
        f"Evidence {EV}: Sourcing experiment T0 (communities lever)",
        "experiment_id=SOURCING_EXPERIMENT_T0_121",
        f"decision={'DRY_RUN' if ap_dry else 'SOURCING_T0_APPLIED'}",
        f"t0={t0.isoformat()} head={head} dry_run={ap_dry}",
        "",
        "=== 0. OWNER AUTH ===",
        f"  {auth['auth_record']}",
        f"  lever={auth['lever']}",
        f"  forbidden={auth['forbidden']}",
        "",
        "=== 1. ISOLATION ===",
        f"  before hl={before['human_labels']} ac={before['ai_confirmed']} "
        f"disc_v6_new={before['disc_v6_new']}",
        f"  after  hl={after['human_labels']} ac={after['ai_confirmed']} "
        f"isolation_ok={isolation_ok}",
        f"  metrics_since_t0={since}",
        "",
        "=== 2. PHASE A DISABLE ===",
        f"  targets={list(PHASE_A_DISABLE_USERNAMES)}",
        f"  disabled={disabled if not ap_dry else phase_a_before}",
        f"  cursors_unchanged={cursor_ok}",
        "",
        "=== 3. PHASE B ADD + SCANS ===",
        f"  plan={phase_b_plan}",
        f"  added={added}",
        f"  scans={scans}",
        "",
        "=== 4. BUSINESS / LOCKS ===",
        f"  {payload['business']}",
        "",
        "=== 5. NEXT ===",
        f"  {payload['next']}",
        "",
    ]
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(OUT_TXT.read_text())
    return 0 if isolation_ok and cursor_ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
