#!/usr/bin/env python3
"""Evidence 99: disc_v6 LaborX FO veto carve acceptance (Phase D).

Read-only. Identical capped prefilter as Evidence 97/98 + AI_CONFIRMED probes.
Does not flip commercial_discovery_version default / enable flag / ML / outreach.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION,
    DISCOVERY_VERSION_V2,
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V6,
    evaluate_discovery,
    population_bucket,
)
from shared.db import SessionLocal
from shared.settings import get_settings

assert DISCOVERY_VERSION_V6 == "disc_v6"
# Default flipped to disc_v6 after acceptance; evidence records pre/post settings.

EV = "99"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-laborx-veto-carve-v6.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-laborx-veto-carve-v6.json"

PREFILTER_WHERE = """
s.tier = 'LOW'
AND s.scored_at >= :since
AND m.id NOT IN (SELECT message_id FROM label_review_samples)
AND m.id NOT IN (SELECT message_id FROM leads WHERE status = 'AI_CONFIRMED')
AND (
  (s.commercial_score > 0 AND s.technical_score > 0)
  OR m.text ~* 'trading bot|торговый бот|bybit|binance api|pybit'
  OR m.text ~* '(hire|looking to (pay|hire)|need).{0,40}(bot|trading|quant)'
  OR m.text ~* 'freelance opportunity.{0,80}(bot|trading|quant|bybit)'
  OR m.text ~* 'починить|кастом.{0,20}бот|ищу.{0,30}(бот|разработ)'
  OR m.text ~* 'commission.{0,40}(bot|trading)|build.{0,40}trading bot'
  OR m.text ~* 'is hiring|looking for a .{0,40}engineer'
  OR m.text ~* '#резюме|#opentowork|#вакансия'
  OR m.text ~* 'looking for (a )?(developer|contractor|freelancer).{0,60}(bot|trading|bybit|strategy)'
  OR m.text ~* '(budget|бюджет|deadline|сроки).{0,80}(bot|trading|bybit|strategy|разработ)'
  OR m.text ~* '(my|our|мой|наш).{0,20}(bot|strategy|бот|стратеги).{0,60}(fix|repair|automate|implement|починить)'
)
"""

PREFILTER_SELECT = f"""
SELECT m.id, m.text, m.author_id, m.community_id, m.message_date,
       c.name AS community_name, c.username AS community_username,
       s.score, s.commercial_score, s.technical_score, s.tier, s.scored_at
FROM messages m
JOIN message_scores s ON s.message_id = m.id
LEFT JOIN communities c ON c.id = m.community_id
WHERE {PREFILTER_WHERE}
ORDER BY (s.commercial_score + s.technical_score) DESC, m.message_date DESC
LIMIT :cap
"""

ISO_SQL = """
SELECT
  (SELECT COUNT(*) FROM human_labels) AS human_labels,
  (SELECT COUNT(*) FROM leads WHERE status = 'AI_CONFIRMED') AS ai_confirmed
"""

PROBE_SQL = """
SELECT l.id AS lead_id, l.message_id, l.tier AS lead_tier, l.status,
       m.text, m.community_id, c.name AS community_name, c.username,
       s.tier AS score_tier
FROM leads l
JOIN messages m ON m.id = l.message_id
LEFT JOIN communities c ON c.id = m.community_id
LEFT JOIN message_scores s ON s.message_id = m.id
WHERE l.status = 'AI_CONFIRMED'
ORDER BY l.id
"""


def _preview(t: str, n: int = 140) -> str:
    return " ".join((t or "").split())[:n]


def _git_head() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
            ).strip()
        )
    except Exception:
        return "unknown"


def first_loss(feats) -> str:
    if feats.hard_exclude and not (
        feats.project_scope
        or feats.soft_direction
        or feats.repair_signal
        or feats.gig_project_carve
    ):
        return "HARD_EXCLUDE_EARLY"
    if feats.veto_categories:
        return "VETO_" + feats.veto_categories[0]
    if not feats.eligible:
        if feats.buyer_direction in ("SEEKER", "PROVIDER", "EMPLOYER"):
            return f"DIRECTION_{feats.buyer_direction}"
        return "NO_PATH_MATCH"
    return "RETRIEVED"


def contamination(cands: list) -> float:
    non_buyer = 0
    for _mid, _text, feats, bucket in cands:
        if bucket != "Direct buyer/RFQ candidate":
            non_buyer += 1
            continue
        if feats.buyer_direction == "BUYER":
            continue
        if feats.buyer_direction == "RECRUITER" and (
            feats.project_scope
            or feats.project_procurement_signal
            or feats.soft_direction
            or feats.ownership_signal
            or feats.gig_project_carve
        ):
            continue
        non_buyer += 1
    n = len(cands) or 1
    return round(100.0 * non_buyer / n, 1)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cap", type=int, default=2500)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    t0 = time.time()
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)

    async with SessionLocal() as session:
        r = await session.execute(
            sql_text(PREFILTER_SELECT), {"since": since, "cap": args.cap}
        )
        rows = [dict(x) for x in r.mappings().all()]
        iso = dict((await session.execute(sql_text(ISO_SQL))).mappings().one())
        probes_db = [
            dict(x) for x in (await session.execute(sql_text(PROBE_SQL))).mappings().all()
        ]

    versions = (DISCOVERY_VERSION_V2, DISCOVERY_VERSION_V4, DISCOVERY_VERSION_V6)
    version_stats: dict[str, dict] = {}

    for ver in versions:
        eligible = []
        first_losses: Counter = Counter()
        veto_rates: Counter = Counter()
        carve_n = 0
        for row in rows:
            text = row["text"] or ""
            feats = evaluate_discovery(scorer, text, version=ver)
            fl = first_loss(feats)
            first_losses[fl] += 1
            for v in feats.veto_categories:
                veto_rates[v] += 1
            if getattr(feats, "gig_project_carve", False):
                carve_n += 1
            bucket = population_bucket(feats)
            if feats.eligible:
                eligible.append((row["id"], text, feats, bucket))

        n_exam = len(rows)
        n_elig = len(eligible)
        version_stats[ver] = {
            "examined": n_exam,
            "eligible": n_elig,
            "eligible_pct": round(100.0 * n_elig / max(n_exam, 1), 2),
            "no_path_match": first_losses.get("NO_PATH_MATCH", 0),
            "veto_job_aggregator_first_loss": first_losses.get("VETO_job_aggregator", 0),
            "contamination_pct": contamination(eligible),
            "first_loss": dict(first_losses.most_common()),
            "veto_rates": dict(veto_rates.most_common()),
            "carve_true_count": carve_n,
            "eligible_triggers": dict(
                Counter(f.trigger_type for _, _, f, _ in eligible if f.trigger_type)
            ),
            "eligible_buckets": dict(Counter(b for _, _, _, b in eligible)),
            "eligible_directions": dict(
                Counter(f.buyer_direction for _, _, f, _ in eligible)
            ),
        }

    v2 = version_stats[DISCOVERY_VERSION_V2]
    v4 = version_stats[DISCOVERY_VERSION_V4]
    v6 = version_stats[DISCOVERY_VERSION_V6]
    newly_eligible = v6["eligible"] - v4["eligible"]

    # Newly eligible message ids (v6 yes, v4 no) — sample
    newly: list[dict] = []
    for row in rows:
        text = row["text"] or ""
        f4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
        f6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        if f6.eligible and not f4.eligible:
            newly.append(
                {
                    "message_id": row["id"],
                    "community": row.get("community_name"),
                    "carve": f6.gig_project_carve,
                    "trigger": f6.trigger_type,
                    "paths": list(f6.matched_paths or []),
                    "direction": f6.buyer_direction,
                    "preview": _preview(text),
                }
            )

    probes_out = []
    fo_recovered = 0
    for p in probes_db:
        text = p["text"] or ""
        f4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
        f6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        score_tier = p.get("score_tier") or p.get("lead_tier")
        lost_prefilter = score_tier in ("HIGH", "MEDIUM")
        fl4 = first_loss(f4)
        fl6 = first_loss(f6)
        recovered = bool(f6.eligible) and (
            "job_aggregator" in (f4.veto_categories or []) or not f4.eligible
        )
        if recovered:
            fo_recovered += 1
        probes_out.append(
            {
                "lead_id": p["lead_id"],
                "message_id": p["message_id"],
                "score_tier": score_tier,
                "community": p.get("community_name"),
                "username": p.get("username"),
                "v4": {
                    "eligible": f4.eligible,
                    "first_loss": fl4,
                    "veto": list(f4.veto_categories),
                    "carve": f4.gig_project_carve,
                },
                "v6": {
                    "eligible": f6.eligible,
                    "first_loss": fl6,
                    "veto": list(f6.veto_categories),
                    "carve": f6.gig_project_carve,
                    "paths": list(f6.matched_paths or []),
                    "trigger": f6.trigger_type,
                    "direction": f6.buyer_direction,
                },
                "discovery_eval_recovered": recovered,
                "note_high_tier_prefilter_separate": lost_prefilter,
                "preview": _preview(text),
            }
        )

    contam_ok = (
        v6["contamination_pct"] < 25.0
        and v6["contamination_pct"] <= max(v4["contamination_pct"] + 5.0, 5.0)
    )
    probe_ok = fo_recovered == len(probes_out) and all(
        p["v6"]["eligible"] and "job_aggregator" not in p["v6"]["veto"] for p in probes_out
    )
    # Ship default only if probes recover AND contamination stays near v4 AND
    # newly eligible is not a vacancy flood (carve-dominated or small).
    carve_share = (
        sum(1 for n in newly if n["carve"]) / max(len(newly), 1) if newly else 1.0
    )
    shippable_default = bool(
        probe_ok
        and contam_ok
        and v6["eligible"] >= v4["eligible"]
        and (len(newly) == 0 or carve_share >= 0.5 or len(newly) <= 30)
    )
    # Conservative: keep default disc_v4 unless contamination ~0 and probes 4/4.
    # User gate: if contamination badly → HOLD carve default flip.
    decision = "SHIP_DEFAULT_V6" if shippable_default and v6["contamination_pct"] <= 5.0 else "HOLD_DEFAULT_V4"
    if not probe_ok:
        rationale = f"probe recovery {fo_recovered}/{len(probes_out)} incomplete"
        decision = "HOLD_DEFAULT_V4"
    elif not contam_ok:
        rationale = (
            f"contamination {v6['contamination_pct']}% unsafe vs v4 "
            f"{v4['contamination_pct']}% / v2 {v2['contamination_pct']}%"
        )
        decision = "HOLD_DEFAULT_V4"
    elif shippable_default and v6["contamination_pct"] <= 5.0:
        rationale = (
            f"probes {fo_recovered}/{len(probes_out)} recovered; contamination "
            f"{v6['contamination_pct']}% near v4; newly_eligible={len(newly)} "
            f"(carve_share={round(carve_share, 2)}). Safe to flip default to disc_v6."
        )
    else:
        rationale = (
            f"opt-in disc_v6 OK for audits; keep default disc_v4 "
            f"(contam={v6['contamination_pct']}% newly={len(newly)} carve_share={round(carve_share, 2)})"
        )
        decision = "HOLD_DEFAULT_V4"

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "runtime_sec": round(time.time() - t0, 2),
        "read_only": True,
        "cap": args.cap,
        "days": args.days,
        "examined": len(rows),
        "isolation": iso,
        "settings": {
            "commercial_discovery_enabled": settings.commercial_discovery_enabled,
            "commercial_discovery_version": settings.commercial_discovery_version,
            "code_default_DISCOVERY_VERSION": DISCOVERY_VERSION,
        },
        "version_stats": version_stats,
        "newly_eligible_vs_v4": {
            "count": len(newly),
            "delta_eligible": newly_eligible,
            "carve_true": sum(1 for n in newly if n["carve"]),
            "samples": newly[:40],
        },
        "probes": probes_out,
        "fo_false_negative_recovery": f"{fo_recovered}/{len(probes_out)}",
        "acceptance": {
            "decision": decision,
            "shippable_default": decision == "SHIP_DEFAULT_V6",
            "contamination_v2": v2["contamination_pct"],
            "contamination_v4": v4["contamination_pct"],
            "contamination_v6": v6["contamination_pct"],
            "eligible_v4": v4["eligible"],
            "eligible_v6": v6["eligible"],
            "veto_job_aggregator_first_loss_v4": v4["veto_job_aggregator_first_loss"],
            "veto_job_aggregator_first_loss_v6": v6["veto_job_aggregator_first_loss"],
            "rationale": rationale,
            "note": (
                "AI_CONFIRMED probes are HIGH-tier → discovery SQL prefilter (LOW-only) "
                "still excludes them; recovery is discovery-eval / first_loss taxonomy."
            ),
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"Evidence {EV}: LaborX FO veto carve (disc_v6)",
        f"generated_at={summary['generated_at']} head={summary['head']}",
        f"runtime_sec={summary['runtime_sec']} read_only=true capped={args.cap}",
        "",
        "=== 1. VERSION COMPARISON (identical capped prefilter) ===",
        f"  disc_v2: eligible={v2['eligible']} contamination={v2['contamination_pct']}%",
        f"  disc_v4: eligible={v4['eligible']} contamination={v4['contamination_pct']}% "
        f"VETO_job_aggregator_first={v4['veto_job_aggregator_first_loss']}",
        f"  disc_v6: eligible={v6['eligible']} contamination={v6['contamination_pct']}% "
        f"VETO_job_aggregator_first={v6['veto_job_aggregator_first_loss']} "
        f"carve_true_on_cap={v6['carve_true_count']}",
        f"  newly eligible vs v4: {len(newly)} (delta={newly_eligible}) "
        f"carve_among_new={sum(1 for n in newly if n['carve'])}",
        f"  first_loss v6: {v6['first_loss']}",
        "",
        "=== 2. AI_CONFIRMED PROBES (discovery eval) ===",
    ]
    for p in probes_out:
        lines.append(
            f"  lead={p['lead_id']} msg={p['message_id']} tier={p['score_tier']} "
            f"v4_fl={p['v4']['first_loss']} v6_elig={p['v6']['eligible']} "
            f"v6_fl={p['v6']['first_loss']} carve={p['v6']['carve']} "
            f"recovered={p['discovery_eval_recovered']} "
            f"HIGH_prefilter_note={p['note_high_tier_prefilter_separate']}"
        )
        lines.append(f"    | {_preview(p['preview'], 120)}")
    lines.extend(
        [
            f"  FO false-negative recovery (discovery-eval): {fo_recovered}/{len(probes_out)}",
            "",
            "=== 3. ACCEPTANCE ===",
            f"  DECISION={decision} shippable_default={decision == 'SHIP_DEFAULT_V6'}",
            f"  rationale: {rationale}",
            f"  contamination: v2={v2['contamination_pct']}% v4={v4['contamination_pct']}% "
            f"v6={v6['contamination_pct']}% (target ≪ v2 ~72.7%, near v4 0%)",
            "",
            "=== 4. ISOLATION ===",
            f"  human_labels={iso['human_labels']} AI_CONFIRMED={iso['ai_confirmed']}",
            f"  commercial_discovery_enabled={settings.commercial_discovery_enabled}",
            f"  commercial_discovery_version={settings.commercial_discovery_version}",
            f"  code DISCOVERY_VERSION={DISCOVERY_VERSION}",
            "",
            "=== 5. BUSINESS ===",
            "  NO OUTREACH / NO ML GO / NO FLAG FLIP UNLESS DECISION=SHIP_DEFAULT_V6",
            "",
            f"JSON: {OUT_JSON}",
        ]
    )
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
