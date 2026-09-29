#!/usr/bin/env python3
"""Evidence 132 — Disable + hard-delete community 139 (@NautilusTrader).

Owner E131 option B + delete (this chat): "disable 139 now … and delete it
from list. it's really dead". VERIFY-JOIN A closed as abandoned.

Uses SessionLocal (same pattern as E127); API /communities blocked by
REVIEW_UI_LOCKDOWN. No soft-delete column exists — hard DELETE after
enabled=false when messages=0. scan_runs.community_id is ON DELETE SET NULL.
Preserves other communities' last_message_id. No path_b/c, ML, human_labels,
scoring.yaml, JoinChannel, docker compose down -v.
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
from shared.models import Community

EV = "132"
TARGET_ID = 139
EXPECT_REF = "@NautilusTrader"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-disable-delete-139-nautilus.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-disable-delete-139-nautilus.json"
EXPECT_HL = 1524
EXPECT_AC = 4
PATH_B_AT = datetime.fromisoformat("2026-09-26T06:53:16+00:00")
PHASE_B_IDS = (137, 138, 139, 140, 141)


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


async def _related_counts(session, community_id: int) -> dict[str, int]:
    row = dict(
        (
            await session.execute(
                sql_text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM messages WHERE community_id=:c) AS messages,
                      (SELECT COUNT(*) FROM scan_runs WHERE community_id=:c) AS scan_runs,
                      (SELECT COUNT(*) FROM message_scores WHERE community_id=:c) AS message_scores,
                      (SELECT COUNT(*) FROM human_labels WHERE community_id=:c) AS human_labels
                    """
                ),
                {"c": community_id},
            )
        )
        .mappings()
        .one()
    )
    return {k: int(v) for k, v in row.items()}


async def main() -> int:
    dry = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc)
    head = _git_head()

    auth = {
        "authorized_by": "owner",
        "auth_record": (
            "Owner decision this chat: E131 option B + delete — "
            "'disable 139 now … and delete it from list. it's really dead'. "
            "VERIFY-JOIN path A closed as abandoned. NOT path_b/c reopen; "
            "NOT ML/shadow; NOT human_labels mutate; NOT JoinChannel invent."
        ),
        "parent_evidence": [
            "docs/audit/evidence/131-verify-join-139-nautilus-blocked.*",
            "docs/audit/evidence/127-sourcing-phase-b-remediation-applied.*",
            "docs/audit/evidence/124-sourcing-phase-b-remediation-proposal.*",
        ],
        "authorized_at": now.isoformat(),
        "lever": (
            "communities.enabled=false for id=139 then hard DELETE row "
            "(no soft-delete column; API has PATCH enabled only; "
            "REVIEW_UI_LOCKDOWN blocks /communities — SessionLocal like E127)"
        ),
        "forbidden": [
            "path_b/path_c reopen",
            "scoring.yaml threshold loosen",
            "human_labels mutation",
            "ML/shadow enable",
            "last_message_id wipe on non-target rows",
            "docker compose down -v",
            "JoinChannel invent",
            "Optuna / freeze expand",
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
        for cid in PHASE_B_IDS:
            before_rows[cid] = _community_snapshot(await session.get(Community, cid))

        target = await session.get(Community, TARGET_ID)
        if target is None:
            print(f"TARGET_MISSING id={TARGET_ID}", file=sys.stderr)
            return 3
        if target.telegram_ref != EXPECT_REF:
            print(
                f"REF_MISMATCH id={TARGET_ID} got={target.telegram_ref!r} "
                f"expected={EXPECT_REF!r}",
                file=sys.stderr,
            )
            return 4

        related_before = await _related_counts(session, TARGET_ID)
        cursor_before_others = {
            cid: (before_rows[cid] or {}).get("last_message_id")
            for cid in (137, 138, 140, 141)
        }

        steps: list[dict[str, Any]] = []
        delete_mode = "hard_delete"
        delete_error: str | None = None

        if not dry:
            # Step 1 — DISABLE (preserve last_message_id on target)
            cursor_before = target.last_message_id
            target.enabled = False
            assert target.last_message_id == cursor_before
            await session.commit()
            await session.refresh(target)
            steps.append(
                {
                    "step": "disable",
                    "id": TARGET_ID,
                    "telegram_ref": target.telegram_ref,
                    "enabled": bool(target.enabled),
                    "last_message_id": target.last_message_id,
                    "last_message_id_unchanged": target.last_message_id == cursor_before,
                }
            )

            # Step 2 — HARD DELETE if messages==0 (supported product path:
            # ORM delete; FKs CASCADE/SET NULL). Soft-delete does not exist.
            if related_before["messages"] != 0:
                delete_mode = "soft_only_blocked_by_messages"
                delete_error = (
                    f"messages={related_before['messages']} — refused hard DELETE; "
                    "left enabled=false only"
                )
                steps.append(
                    {
                        "step": "delete",
                        "skipped": True,
                        "reason": delete_error,
                        "mode": delete_mode,
                    }
                )
            else:
                try:
                    await session.delete(target)
                    await session.commit()
                    steps.append(
                        {
                            "step": "delete",
                            "id": TARGET_ID,
                            "mode": delete_mode,
                            "messages_at_delete": related_before["messages"],
                            "scan_runs_at_delete": related_before["scan_runs"],
                            "note": "scan_runs.community_id ON DELETE SET NULL",
                        }
                    )
                except Exception as exc:  # noqa: BLE001 — evidence path
                    await session.rollback()
                    delete_mode = "disable_only_fk_blocked"
                    delete_error = f"{type(exc).__name__}: {exc}"
                    # Re-ensure disabled after rollback
                    target2 = await session.get(Community, TARGET_ID)
                    if target2 is not None and target2.enabled:
                        target2.enabled = False
                        await session.commit()
                    steps.append(
                        {
                            "step": "delete",
                            "failed": True,
                            "mode": delete_mode,
                            "error": delete_error[:500],
                            "fallback": "enabled=false retained",
                        }
                    )

        after_iso = await _iso(session)
        after_rows: dict[int, dict[str, Any] | None] = {}
        for cid in PHASE_B_IDS:
            after_rows[cid] = _community_snapshot(await session.get(Community, cid))

        cursor_after_others = {
            cid: (after_rows[cid] or {}).get("last_message_id")
            for cid in (137, 138, 140, 141)
        }
        cursors_preserved = cursor_before_others == cursor_after_others

        # Accidental disable check for 138/141 (must remain enabled if present)
        accidental: list[str] = []
        for cid in (138, 141):
            b = before_rows.get(cid)
            a = after_rows.get(cid)
            if b and a and bool(b.get("enabled")) and not bool(a.get("enabled")):
                accidental.append(f"id={cid} was enabled→disabled")
            if b and a is None:
                accidental.append(f"id={cid} disappeared")

        gone = after_rows.get(TARGET_ID) is None
        disabled_only = (
            after_rows.get(TARGET_ID) is not None
            and after_rows[TARGET_ID] is not None
            and not after_rows[TARGET_ID]["enabled"]  # type: ignore[index]
        )

        # Phase B enabled summary
        phase_b_enabled = {
            str(cid): (after_rows[cid] or {}).get("enabled") if after_rows[cid] else "DELETED"
            for cid in PHASE_B_IDS
        }

        payload: dict[str, Any] = {
            "evidence_id": EV,
            "experiment_id": "DISABLE_DELETE_139_NAUTILUS_132",
            "decision": "APPLIED" if not dry else "DRY_RUN",
            "owner_choice": "E131_B_plus_delete",
            "verify_join_a": "CLOSED_ABANDONED",
            "measure_at": now.isoformat(),
            "head": head,
            "dry_run": dry,
            "auth": auth,
            "delete_mode": delete_mode,
            "delete_error": delete_error,
            "target": {
                "id": TARGET_ID,
                "expected_ref": EXPECT_REF,
                "related_before": related_before,
            },
            "before": {
                "isolation": before_iso,
                "phase_b_communities": before_rows,
            },
            "steps": steps,
            "after": {
                "isolation": after_iso,
                "phase_b_communities": after_rows,
                "phase_b_enabled": phase_b_enabled,
                "target_gone": gone,
                "target_disabled_only": disabled_only,
            },
            "cursors_preserved_137_138_140_141": cursors_preserved,
            "cursor_before_others": cursor_before_others,
            "cursor_after_others": cursor_after_others,
            "accidental_disable_138_141": accidental,
            "isolation_held": (
                after_iso["human_labels"] == EXPECT_HL
                and after_iso["ai_confirmed"] == EXPECT_AC
            ),
            "api_note": (
                "No DELETE /communities route; PATCH /enabled exists but "
                "REVIEW_UI_LOCKDOWN blocks list/mutate via HTTP — used SessionLocal"
            ),
        }

        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(payload, indent=2, default=str) + "\n")

        lines = [
            f"Evidence {EV}: Disable + delete community {TARGET_ID} (@NautilusTrader)",
            f"evidence_id={EV}",
            f"experiment_id={payload['experiment_id']}",
            f"decision={payload['decision']}",
            f"owner_choice=E131 option B + delete (this chat)",
            f"verify_join_A=CLOSED_ABANDONED",
            f"measure_at={now.isoformat()} head={head}",
            f"dry_run={dry} delete_mode={delete_mode}",
            "",
            "=== 0. OWNER AUTH ===",
            f"  {auth['auth_record']}",
            f"  authorized_at={auth['authorized_at']}",
            f"  lever={auth['lever']}",
            "",
            "=== 1. BEFORE ===",
            f"  isolation={before_iso}",
            f"  target={before_rows.get(TARGET_ID)}",
            f"  related={related_before}",
            "  phase_b:",
        ]
        for cid in PHASE_B_IDS:
            lines.append(f"    {before_rows.get(cid)}")
        lines += [
            "",
            "=== 2. STEPS ===",
        ]
        for st in steps:
            lines.append(f"  {st}")
        if delete_error:
            lines.append(f"  delete_error={delete_error}")
        lines += [
            "",
            "=== 3. AFTER ===",
            f"  isolation={after_iso}",
            f"  target_gone={gone} target_disabled_only={disabled_only}",
            f"  phase_b_enabled={phase_b_enabled}",
            f"  cursors_preserved_137_138_140_141={cursors_preserved}",
            f"  accidental_disable_138_141={accidental or 'none'}",
            "  phase_b:",
        ]
        for cid in PHASE_B_IDS:
            lines.append(f"    {after_rows.get(cid)}")
        lines += [
            "",
            "=== 4. NOT DONE ===",
            "  path_b/c reopen · ML/shadow · human_labels · scoring.yaml",
            "  JoinChannel · Optuna · freeze expand · docker compose down -v",
            "  last_message_id wipe on 137/138/140/141",
            "",
            "=== 5. VERDICT ===",
            f"  isolation_held={payload['isolation_held']} (expect {EXPECT_HL}/{EXPECT_AC})",
            f"  139_removed_from_list={gone}",
            f"  VERIFY-JOIN A abandoned; E131 B+delete applied",
        ]
        OUT_TXT.write_text("\n".join(lines) + "\n")
        print(json.dumps({"ok": True, "gone": gone, "isolation": after_iso, "txt": str(OUT_TXT)}, default=str))

        if accidental:
            return 5
        if not payload["isolation_held"]:
            return 6
        if not dry and not gone and delete_mode == "hard_delete":
            return 7
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
