#!/usr/bin/env python3
"""Bounded reprocess smoke + DB invariant checks.

Runs against whatever DATABASE the process settings resolve to
(local Postgres or compose network). Safe by default: requires
--seed-synthetic to create disposable data, and never truncates
unless --reset-synthetic is also passed (and only touches rows
belonging to the synthetic smoke community).

Examples:
  # Against an already-seeded DB (production host after analyzer rebuild):
  python scripts/smoke_reprocess_verify.py --max-messages 1000
  python scripts/smoke_reprocess_verify.py --max-messages 10000

  # Disposable local verification:
  python scripts/smoke_reprocess_verify.py --seed-synthetic --reset-synthetic \\
      --max-messages 1000 --second-pass
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select, text

from shared.db import SessionLocal
from shared.models import Community, Lead, Message

SMOKE_REF = "@SmokeTestCommunity"
SAMPLES = [
    "Looking for a developer to build a Bybit and OKX funding arbitrage bot. Budget $3k.",
    "Looking to buy a custom trading bot for Binance futures. Budget $3,000.",
    "I have a trading strategy and want a developer to implement and automate it.",
    "I need a developer to fix my Python Bybit trading bot execution bug.",
    "Python trading bot using pybit. ErrCode 170140. Why is Bybit rejecting the market order?",
    "New API keys return error 10003. Can someone from the API team check my account?",
    "Hello guys. I am a developer. I can help you build trading bots. DM me.",
    "#ищу_работу Python developer. Looking for work, remote. CV available.",
    "We are hiring a Senior Backend Engineer for a crypto trading platform.",
    "Random chat about weather and coffee in the trading lounge.",
]


async def _duplicate_lead_count() -> int:
    async with SessionLocal() as db:
        rows = await db.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "  SELECT message_id FROM leads "
                "  GROUP BY message_id HAVING COUNT(*) > 1"
                ") d"
            )
        )
        return int(rows.scalar_one())


async def _counts() -> dict[str, int]:
    async with SessionLocal() as db:
        leads = int(await db.scalar(select(func.count()).select_from(Lead)) or 0)
        msgs = int(await db.scalar(select(func.count()).select_from(Message)) or 0)
        contacted = int(
            await db.scalar(
                select(func.count()).select_from(Lead).where(Lead.status == "CONTACTED")
            )
            or 0
        )
    return {
        "leads": leads,
        "messages": msgs,
        "contacted": contacted,
        "duplicate_message_ids": await _duplicate_lead_count(),
    }


async def reset_synthetic() -> None:
    async with SessionLocal() as db:
        community = await db.scalar(
            select(Community).where(Community.telegram_ref == SMOKE_REF)
        )
        if community is None:
            return
        message_ids = list(
            await db.scalars(
                select(Message.id).where(Message.community_id == community.id)
            )
        )
        if message_ids:
            leads = (
                await db.scalars(
                    select(Lead).where(Lead.message_id.in_(message_ids))
                )
            ).all()
            for lead in leads:
                await db.delete(lead)
            messages = (
                await db.scalars(
                    select(Message).where(Message.community_id == community.id)
                )
            ).all()
            for message in messages:
                await db.delete(message)
        await db.delete(community)
        await db.commit()
        print(f"reset_synthetic removed community {SMOKE_REF}", flush=True)


async def seed_synthetic(n_messages: int) -> None:
    async with SessionLocal() as db:
        community = Community(
            telegram_ref=SMOKE_REF,
            name="Smoke Test",
            username="SmokeTestCommunity",
            enabled=True,
            resolve_status="resolved",
            last_message_id=0,
        )
        db.add(community)
        await db.flush()
        now = datetime.now(timezone.utc)
        for i in range(1, n_messages + 1):
            msg = Message(
                community_id=community.id,
                telegram_chat_id=111,
                telegram_message_id=i,
                message_date=now,
                text=f"{SAMPLES[i % len(SAMPLES)]} #{i}",
                is_reply=False,
            )
            db.add(msg)
            await db.flush()
            if i % 50 == 0 or i % 50 == 5:
                db.add(
                    Lead(
                        message_id=msg.id,
                        score=1.0,
                        tier="MEDIUM",
                        lead_type="NOISE",
                        buyer_type="UNKNOWN",
                        status="CONTACTED",
                        matched_keywords="[]",
                        matched_categories="[]",
                        reasons='["seed"]',
                        contact_usernames="[]",
                        contact_urls="[]",
                    )
                )
            if i % 3000 == 0:
                await db.commit()
                print(f"seeded {i}/{n_messages}", flush=True)
        await db.commit()
        print(f"seeded {n_messages} messages under {SMOKE_REF}", flush=True)


def run_reprocess(max_messages: int, batch_size: int) -> tuple[str, float]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    t0 = time.perf_counter()
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "reprocess_messages.py"),
            "--max-messages",
            str(max_messages),
            "--batch-size",
            str(batch_size),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    wall = time.perf_counter() - t0
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(
            f"reprocess failed rc={proc.returncode}\n{output[-2000:]}"
        )
    done = [ln for ln in output.splitlines() if ln.startswith("DONE ")]
    if not done:
        raise RuntimeError(f"reprocess produced no DONE line\n{output[-2000:]}")
    return done[-1], wall


def parse_done(line: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in line.replace("DONE ", "").split():
        key, _, value = part.partition("=")
        out[key] = int(value)
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-messages", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--seed-synthetic", action="store_true")
    parser.add_argument("--reset-synthetic", action="store_true")
    parser.add_argument("--seed-count", type=int, default=12000)
    parser.add_argument(
        "--second-pass",
        action="store_true",
        help="Re-run once more and assert created=updated=deleted=0",
    )
    args = parser.parse_args()

    if args.reset_synthetic:
        await reset_synthetic()
    if args.seed_synthetic:
        await seed_synthetic(args.seed_count)

    before = await _counts()
    print(f"BEFORE {before}", flush=True)
    if before["duplicate_message_ids"] != 0:
        raise SystemExit("FAIL: duplicate leads already present")

    done_line, wall = run_reprocess(args.max_messages, args.batch_size)
    metrics = parse_done(done_line)
    print(f"PASS1 WALL_SEC={wall:.3f} {done_line}", flush=True)
    if metrics.get("processed") != args.max_messages:
        raise SystemExit(
            f"FAIL: processed={metrics.get('processed')} != {args.max_messages}"
        )

    after = await _counts()
    print(f"AFTER {after}", flush=True)
    if after["duplicate_message_ids"] != 0:
        raise SystemExit("FAIL: duplicate leads after reprocess")

    expected_leads = before["leads"] + metrics["created"] - metrics["deleted"]
    if after["leads"] != expected_leads:
        raise SystemExit(
            f"FAIL: lead count {after['leads']} != "
            f"before+created-deleted ({expected_leads})"
        )

    if args.second_pass:
        done2, wall2 = run_reprocess(args.max_messages, args.batch_size)
        m2 = parse_done(done2)
        print(f"PASS2 WALL_SEC={wall2:.3f} {done2}", flush=True)
        if m2["created"] or m2["updated"] or m2["deleted"]:
            raise SystemExit(
                f"FAIL: second pass expected no writes, got {done2}"
            )
        if m2["unchanged"] <= 0 and m2["high"] + m2["medium"] > 0:
            raise SystemExit("FAIL: second pass expected unchanged > 0")

    print("SMOKE_OK", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
