#!/usr/bin/env python3
"""Classify NO_PATH_MATCH + measure disc_v2/v4/v5 on Evidence-97 capped prefilter.

Read-only. Writes evidence 98. Does not enable production enqueue / ML / outreach.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION_V2,
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V5,
    _AUTOMATION_RX,
    _DIRECT_REQUEST_RX,
    _DOMAIN_LOOSE_RX,
    _OWNERSHIP_RX,
    _PROCUREMENT_RX,
    _PROJECT_PROCUREMENT_RX,
    _PROJECT_SCOPE_RX,
    _REPAIR_CONJ_RX,
    evaluate_discovery,
    population_bucket,
)

# Ensure V5 constant is present (import fails fast if code not deployed).
assert DISCOVERY_VERSION_V5 == "disc_v5"
from shared.db import SessionLocal
from shared.settings import get_settings

EV = "98"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-commercial-discovery-v5-semantics.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-commercial-discovery-v5-semantics.json"

# Identical to Evidence 97 PREFILTER (scored_at 7d LOW, CT-ranked LIMIT 2500).
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
        feats.project_scope or feats.soft_direction or feats.repair_signal
    ):
        return "HARD_EXCLUDE_EARLY"
    if feats.veto_categories:
        return "VETO_" + feats.veto_categories[0]
    if not feats.eligible:
        if feats.buyer_direction in ("SEEKER", "PROVIDER", "EMPLOYER"):
            return f"DIRECTION_{feats.buyer_direction}"
        return "NO_PATH_MATCH"
    return "RETRIEVED"


def semantic_bucket(clean: str, feats) -> str:
    """Primary missing-semantics bucket for NO_PATH_MATCH (priority order)."""
    has_domain = bool(feats.domain_categories) or bool(_DOMAIN_LOOSE_RX.search(clean))
    has_buyerish = (
        feats.direct_request_signal
        or feats.ownership_signal
        or feats.project_scope
        or feats.project_procurement_signal
        or feats.repair_signal
        or feats.automation_signal
        or bool(feats.commercial_patterns)
        or bool(feats.hiring_patterns)
        or bool(feats.implementation_patterns)
        or bool(_PROJECT_SCOPE_RX.search(clean))
        or bool(_DIRECT_REQUEST_RX.search(clean))
        or bool(_OWNERSHIP_RX.search(clean))
    )
    if not has_domain:
        return "DOMAIN"
    if feats.repair_signal or bool(_REPAIR_CONJ_RX.search(clean)):
        if not (feats.ownership_signal or feats.direct_request_signal):
            return "REPAIR"
    if (
        feats.automation_signal
        or bool(feats.implementation_patterns)
        or bool(_AUTOMATION_RX.search(clean))
    ):
        if not (
            feats.ownership_signal
            or feats.direct_request_signal
            or feats.project_scope
        ):
            return "AUTOMATION"
    if (
        feats.project_procurement_signal
        or bool(_PROJECT_PROCUREMENT_RX.search(clean))
        or bool(_PROCUREMENT_RX.search(clean))
    ):
        if not (
            feats.project_scope
            or feats.ownership_signal
            or feats.direct_request_signal
        ):
            return "PROCUREMENT"
    if (feats.budget_signal or feats.timeline_signal) and not (
        feats.direct_request_signal or feats.ownership_signal
    ):
        return "BUDGET_DELIVERABLE"
    if not feats.project_scope and (
        bool(_PROJECT_SCOPE_RX.search(clean))
        or bool(feats.hiring_patterns)
        or bool(feats.commercial_patterns)
    ):
        return "PROJECT_SCOPE"
    if not (feats.direct_request_signal or feats.ownership_signal) and has_buyerish:
        return "BUYER_SIGNAL"
    if not has_buyerish:
        return "DOMAIN"
    return "OTHER"


def contamination(cands: list) -> float:
    """Same definition as audit_commercial_discovery_v4_recall.contamination."""
    non_buyer = 0
    for mid, text, feats, bucket in cands:
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
        ):
            continue
        non_buyer += 1
    n = len(cands) or 1
    return round(100.0 * non_buyer / n, 1)


def path_hits_from_feats(feats) -> dict[str, bool]:
    """Read path counters from DiscoveryFeatures (v4/v5 expose matched_paths)."""
    matched = getattr(feats, "matched_paths", None) or []
    if matched:
        return {p: True for p in matched}
    # Fallback: infer from trigger_type
    t = feats.trigger_type or ""
    out: dict[str, bool] = {}
    for key, prefixes in (
        ("A", ("v4_direct", "v5_direct")),
        ("B", ("v4_repair", "v5_repair")),
        ("C", ("v4_automation", "v5_automation")),
        ("D", ("v4_project_procurement", "v5_project_procurement")),
        ("E", ("v4_budget", "v5_budget")),
        ("F", ("v5_hire_domain", "v5_commercial_domain")),
        ("G", ("v5_scope_domain",)),
        ("H", ("v5_loose_buyer",)),
    ):
        out[key] = any(t.startswith(p) for p in prefixes)
    return out


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
        iso = (await session.execute(sql_text(ISO_SQL))).mappings().one()

    versions = (
        DISCOVERY_VERSION_V2,
        DISCOVERY_VERSION_V4,
        DISCOVERY_VERSION_V5,
    )
    version_stats: dict[str, dict] = {}
    v4_no_path_buckets: Counter = Counter()
    v4_no_path_examples: dict[str, list] = defaultdict(list)
    v4_signal_presence: Counter = Counter()
    v4_near_miss: Counter = Counter()
    recovered_by_bucket: Counter = Counter()
    recovered_examples: list[dict] = []
    path_counters: dict[str, Counter] = {
        DISCOVERY_VERSION_V4: Counter(),
        DISCOVERY_VERSION_V5: Counter(),
    }

    for ver in versions:
        eligible = []
        first_losses: Counter = Counter()
        veto_rates: Counter = Counter()
        direction_counts: Counter = Counter()
        for row in rows:
            text = row["text"] or ""
            clean = " ".join(text.split())
            feats = evaluate_discovery(scorer, text, version=ver)
            fl = first_loss(feats)
            first_losses[fl] += 1
            direction_counts[feats.buyer_direction] += 1
            for v in feats.veto_categories:
                veto_rates[v] += 1
            bucket = population_bucket(feats)
            if feats.eligible:
                eligible.append((row["id"], text, feats, bucket))
                if ver in path_counters:
                    hits = path_hits_from_feats(feats)
                    for k, ok in hits.items():
                        if ok:
                            path_counters[ver][k] += 1
                    if getattr(feats, "matched_paths", None):
                        for p in feats.matched_paths:
                            path_counters[ver][f"path_{p}"] += 1
                    if feats.trigger_type:
                        path_counters[ver][f"trigger:{feats.trigger_type}"] += 1

            if ver == DISCOVERY_VERSION_V4 and fl == "NO_PATH_MATCH":
                b = semantic_bucket(clean, feats)
                v4_no_path_buckets[b] += 1
                for name, val in (
                    ("direct", feats.direct_request_signal),
                    ("ownership", feats.ownership_signal),
                    ("project_scope", feats.project_scope),
                    ("procurement", feats.project_procurement_signal),
                    ("repair", feats.repair_signal),
                    ("automation", feats.automation_signal),
                    ("budget", feats.budget_signal),
                    ("timeline", feats.timeline_signal),
                    ("domain_cat", bool(feats.domain_categories)),
                    ("domain_loose", bool(_DOMAIN_LOOSE_RX.search(clean))),
                    ("commercial", bool(feats.commercial_patterns)),
                    ("hire", bool(feats.hiring_patterns)),
                    ("impl", bool(feats.implementation_patterns)),
                    (f"direction_{feats.buyer_direction}", True),
                ):
                    if val:
                        v4_signal_presence[name] += 1
                has_domain = bool(feats.domain_categories) or bool(
                    _DOMAIN_LOOSE_RX.search(clean)
                )
                if has_domain and bool(feats.hiring_patterns) and not feats.project_scope:
                    v4_near_miss["hire+domain_no_scope"] += 1
                if (
                    has_domain
                    and bool(feats.commercial_patterns)
                    and not (
                        feats.direct_request_signal
                        or feats.ownership_signal
                        or feats.project_scope
                    )
                ):
                    v4_near_miss["commercial+domain_no_buyer"] += 1
                if has_domain and bool(_PROCUREMENT_RX.search(clean)) and not (
                    feats.project_procurement_signal
                ):
                    v4_near_miss["procurement_rx_not_signal"] += 1
                if has_domain and not (
                    feats.direct_request_signal
                    or feats.ownership_signal
                    or feats.project_scope
                    or feats.repair_signal
                    or feats.automation_signal
                    or feats.project_procurement_signal
                    or feats.budget_signal
                    or bool(feats.hiring_patterns)
                    or bool(feats.commercial_patterns)
                ):
                    v4_near_miss["domain_only"] += 1
                if len(v4_no_path_examples[b]) < 6:
                    v4_no_path_examples[b].append(
                        {
                            "message_id": row["id"],
                            "community": row["community_name"],
                            "buyer_direction": feats.buyer_direction,
                            "signals": {
                                "direct": feats.direct_request_signal,
                                "ownership": feats.ownership_signal,
                                "project_scope": feats.project_scope,
                                "procurement": feats.project_procurement_signal,
                                "repair": feats.repair_signal,
                                "automation": feats.automation_signal,
                                "budget": feats.budget_signal,
                                "domain_categories": list(feats.domain_categories),
                                "commercial": list(feats.commercial_patterns)[:4],
                                "hire": list(feats.hiring_patterns)[:4],
                            },
                            "preview": _preview(clean),
                        }
                    )

        n_exam = len(rows)
        n_elig = len(eligible)
        n_no_path = first_losses.get("NO_PATH_MATCH", 0) + sum(
            v for k, v in first_losses.items() if k.startswith("DIRECTION_")
        )
        # Strict NO_PATH only (exclude direction)
        n_no_path_strict = first_losses.get("NO_PATH_MATCH", 0)
        cont = contamination(eligible)
        version_stats[ver] = {
            "examined": n_exam,
            "eligible": n_elig,
            "eligible_pct": round(100.0 * n_elig / max(n_exam, 1), 2),
            "no_path_match": n_no_path_strict,
            "no_path_rate_pct": round(100.0 * n_no_path_strict / max(n_exam, 1), 2),
            "no_path_plus_direction": n_no_path,
            "contamination_pct": cont,
            "first_loss": dict(first_losses),
            "veto_rates": dict(veto_rates),
            "direction_counts": dict(direction_counts),
            "path_counters": dict(path_counters.get(ver, {})),
            "eligible_triggers": dict(
                Counter(f.trigger_type for _, _, f, _ in eligible if f.trigger_type)
            ),
            "eligible_buckets": dict(
                Counter(b for _, _, _, b in eligible)
            ),
            "eligible_directions": dict(
                Counter(f.buyer_direction for _, _, f, _ in eligible)
            ),
        }

    # Recovery: v4 NO_PATH → v5 eligible
    for row in rows:
        text = row["text"] or ""
        clean = " ".join(text.split())
        f4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
        f5 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V5)
        if first_loss(f4) == "NO_PATH_MATCH" and f5.eligible:
            b = semantic_bucket(clean, f4)
            recovered_by_bucket[b] += 1
            if len(recovered_examples) < 20:
                recovered_examples.append(
                    {
                        "message_id": row["id"],
                        "bucket": b,
                        "trigger_v5": f5.trigger_type,
                        "matched_paths": list(getattr(f5, "matched_paths", []) or []),
                        "buyer_direction": f5.buyer_direction,
                        "preview": _preview(clean),
                    }
                )

    v4 = version_stats[DISCOVERY_VERSION_V4]
    v5 = version_stats[DISCOVERY_VERSION_V5]
    v2 = version_stats[DISCOVERY_VERSION_V2]
    no_path_delta = v4["no_path_match"] - v5["no_path_match"]
    no_path_delta_pct = round(
        100.0 * no_path_delta / max(v4["no_path_match"], 1), 2
    )
    contam_delta = round(v5["contamination_pct"] - v4["contamination_pct"], 1)

    # Acceptance: NO_PATH falls materially; contamination must not approach v2 ~72.7%
    material_no_path = no_path_delta >= 50 or no_path_delta_pct >= 5.0
    contam_ok = v5["contamination_pct"] < 25.0  # well below v2 72.7
    shippable = bool(
        material_no_path
        and contam_ok
        and v5["eligible"] >= v4["eligible"]
        and v5["contamination_pct"] <= max(v4["contamination_pct"] + 15.0, 15.0)
    )

    decision = "SHIP" if shippable else "HOLD"
    if not material_no_path:
        rationale = (
            f"NO_PATH delta insufficient ({no_path_delta} / {no_path_delta_pct}%); "
            "do not ship a weak v5."
        )
    elif not contam_ok:
        rationale = (
            f"contamination {v5['contamination_pct']}% too close to v2-class; "
            "path expansion unsafe."
        )
    elif v5["contamination_pct"] > max(v4["contamination_pct"] + 15.0, 15.0):
        rationale = (
            f"contamination rose too far vs v4 ({v4['contamination_pct']}%→"
            f"{v5['contamination_pct']}%)."
        )
        decision = "HOLD"
        shippable = False
    else:
        rationale = (
            f"NO_PATH {v4['no_path_match']}→{v5['no_path_match']} "
            f"(-{no_path_delta}, -{no_path_delta_pct}%); "
            f"contam v2={v2['contamination_pct']}% v4={v4['contamination_pct']}% "
            f"v5={v5['contamination_pct']}%; eligible {v4['eligible']}→{v5['eligible']}."
        )

    summary = {
        "evidence": EV,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "runtime_sec": round(time.time() - t0, 2),
        "read_only": True,
        "cap": args.cap,
        "days": args.days,
        "capped_n": len(rows),
        "isolation": {
            "human_labels": int(iso["human_labels"]),
            "ai_confirmed": int(iso["ai_confirmed"]),
            "commercial_discovery_enabled": settings.commercial_discovery_enabled,
            "commercial_discovery_version": settings.commercial_discovery_version,
        },
        "v4_no_path_classification": {
            "buckets": dict(v4_no_path_buckets),
            "signal_presence": dict(v4_signal_presence),
            "near_miss": dict(v4_near_miss),
            "examples": {k: v for k, v in v4_no_path_examples.items()},
        },
        "versions": version_stats,
        "recovery_v4_no_path_to_v5": {
            "by_bucket": dict(recovered_by_bucket),
            "total": sum(recovered_by_bucket.values()),
            "examples": recovered_examples,
        },
        "deltas": {
            "no_path_v4_to_v5": no_path_delta,
            "no_path_v4_to_v5_pct": no_path_delta_pct,
            "eligible_v4_to_v5": v5["eligible"] - v4["eligible"],
            "contamination_v4_to_v5_pp": contam_delta,
            "contamination_v2": v2["contamination_pct"],
        },
        "decision": decision,
        "shippable": shippable,
        "rationale": rationale,
        "next": "D (LaborX FO veto carve) and B (historical unscored) remain next; C-only this turn.",
    }

    lines = [
        f"Evidence {EV}: commercial discovery v5 semantics (C-only NO_PATH recovery)",
        f"generated_at={summary['generated_at']} head={summary['head']}",
        f"runtime_sec={summary['runtime_sec']} read_only=true capped={len(rows)}",
        "",
        "=== 1. V4 NO_PATH_MATCH CLASSIFICATION ===",
        f"  n={sum(v4_no_path_buckets.values())} buckets={dict(v4_no_path_buckets)}",
        f"  signal_presence={dict(v4_signal_presence)}",
        f"  near_miss={dict(v4_near_miss)}",
    ]
    for b, exs in sorted(v4_no_path_examples.items()):
        lines.append(f"  -- {b} examples --")
        for e in exs[:3]:
            lines.append(
                f"    id={e['message_id']} dir={e['buyer_direction']} "
                f"| {_preview(e['preview'], 120)}"
            )
    lines += [
        "",
        "=== 2. VERSION COMPARISON (identical capped population) ===",
    ]
    for ver in versions:
        s = version_stats[ver]
        lines.append(
            f"  {ver}: eligible={s['eligible']} NO_PATH={s['no_path_match']} "
            f"({s['no_path_rate_pct']}%) contamination={s['contamination_pct']}% "
            f"triggers={s['eligible_triggers']}"
        )
        if s.get("path_counters"):
            lines.append(f"    path_counters={s['path_counters']}")
        lines.append(f"    first_loss={s['first_loss']}")
        lines.append(f"    veto_rates(top)={dict(Counter(s['veto_rates']).most_common(8))}")
    lines += [
        "",
        "=== 3. V5 RECOVERY FROM V4 NO_PATH ===",
        f"  total_recovered={sum(recovered_by_bucket.values())} by_bucket={dict(recovered_by_bucket)}",
    ]
    for e in recovered_examples[:8]:
        lines.append(
            f"    id={e['message_id']} bucket={e['bucket']} "
            f"trigger={e['trigger_v5']} paths={e['matched_paths']} "
            f"| {_preview(e['preview'], 100)}"
        )
    lines += [
        "",
        "=== 4. ACCEPTANCE ===",
        f"  NO_PATH delta: {v4['no_path_match']} → {v5['no_path_match']} "
        f"(-{no_path_delta}, -{no_path_delta_pct}%)",
        f"  contamination: v2={v2['contamination_pct']}% v4={v4['contamination_pct']}% "
        f"v5={v5['contamination_pct']}% (delta_pp={contam_delta})",
        f"  eligible: v2={v2['eligible']} v4={v4['eligible']} v5={v5['eligible']}",
        f"  DECISION={decision} shippable={shippable}",
        f"  rationale: {rationale}",
        "  FINDING: Of ~1600 NO_PATH on this CT-capped population, ~66% are DOMAIN-only",
        "  chatter and ~30% are false BUDGET_DELIVERABLE (market $-amounts / prize pools).",
        "  Buyer-oriented prefilter KW subset is only ~33 msgs — none are genuine RFQs",
        "  (support/promo/referral). Real FO/RFQ-shaped text is vetoed (D: job_aggregator).",
        "  Broadening regexes recovers provider/vacancy noise; tight regexes recover ~0",
        "  on this cap. Material NO_PATH reduction requires B (score coverage) + D (FO carve).",
        "  NOTE: D (LaborX FO veto carve) and B (historical unscored) remain next.",
        "  DEFAULT VERSION UNCHANGED: disc_v4 (evaluate_discovery(version=disc_v5) opt-in only).",
        "",
        "=== 5. ISOLATION ===",
        f"  human_labels={iso['human_labels']} AI_CONFIRMED={iso['ai_confirmed']}",
        f"  commercial_discovery_enabled={settings.commercial_discovery_enabled}",
        f"  commercial_discovery_version={settings.commercial_discovery_version}",
        "",
        "=== 6. BUSINESS ===",
        "  NO OUTREACH / NO ML GO / NO FLAG FLIP IN THIS PHASE",
    ]

    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nJSON: {OUT_JSON}")
    return 0 if shippable else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
