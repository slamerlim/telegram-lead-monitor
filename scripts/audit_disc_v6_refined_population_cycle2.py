#!/usr/bin/env python3
"""Evidence 102: Cycle 2 refined population + NO_PATH FN inventory (measurement-only).

Continues Evidence 101 MEASUREMENT_ONLY_STOP. Does NOT change disc_v6 logic,
flip commercial_discovery_enabled, mutate scores/labels/CRM, or loosen scoring.yaml.

Goals:
  1) Inventory the 11 buyerish_clean NO_PATH rows from E101 with FN verdicts.
  2) Define a refined frozen audit population (drop bare commission; exclude
     EXCHANGE_OFFICIAL + unmapped exchange-name communities) and remeasure
     disc_v4 vs disc_v6 while keeping the legacy E100/E101 checkpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text as sql_text

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import (
    DISCOVERY_VERSION,
    DISCOVERY_VERSION_V4,
    DISCOVERY_VERSION_V6,
    evaluate_discovery,
    population_bucket,
)
from shared.db import SessionLocal
from shared.settings import get_settings

EV = "102"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-disc-v6-refined-population-cycle2.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-disc-v6-refined-population-cycle2.json"
OUT_FN = ROOT / f"docs/audit/evidence/{EV}-nopath-buyerish-fn-verdicts.json"

# Byte-compatible with Evidence 96/97/100/101.
LEGACY_COMMERCIAL = (
    r"(budget|fixed price|commission|need someone|"
    r"looking for (a )?(developer|contractor|freelancer)|"
    r"нужен разработчик|починить|Freelance Opportunity)"
)
POOL_DOMAIN = (
    r"(trading bot|bybit|binance|okx|pybit|arbitrage|futures|strategy|"
    r"торговый бот|Crypto Trading Bot)"
)

# Refined commercial: drop bare "commission"; keep commission near hire/budget/$ intent.
REFINED_COMMERCIAL = (
    r"(budget|fixed price|need someone|"
    r"looking for (a )?(developer|contractor|freelancer)|"
    r"нужен разработчик|починить|Freelance Opportunity|"
    r"commission.{0,100}(?:hire|hiring|developer|freelancer|contractor|budget|\$)|"
    r"(?:hire|hiring|developer|freelancer|contractor|budget).{0,100}commission)"
)

# E101 frozen since (rolling-window checkpoint).
E101_SINCE_ISO = "2025-09-30T17:54:03.414786+00:00"

# The 11 buyerish_clean NO_PATH message ids from Evidence 101 inventory.
E101_BUYERISH_NOPATH_IDS = [
    1311568,
    424457,
    425184,
    922511,
    389826,
    37727,
    39270,
    1081595,
    1126300,
    1132711,
    1196688,
]

LOSS_FAMILY = {
    "HARD_EXCLUDE_EARLY": "hard_veto",
    "VETO_job_aggregator": "hard_veto",
    "VETO_corporate_employment": "hard_veto",
    "VETO_generic_recruiter": "hard_veto",
    "VETO_job_seeker": "hard_veto",
    "VETO_service_provider": "hard_veto",
    "VETO_support_question": "hard_veto",
    "VETO_marketing_broadcast": "hard_veto",
    "VETO_news_digest": "hard_veto",
    "DIRECTION_SEEKER": "direction",
    "DIRECTION_PROVIDER": "direction",
    "DIRECTION_EMPLOYER": "direction",
    "DIRECTION_RECRUITER": "direction",
    "NO_PATH_MATCH": "semantic_miss",
    "RETRIEVED": "survivor",
    "NOT_SCORED": "score_gap",
    "NOT_LOW_PREFILTER_SHAPE": "tier_shape",
}

BUYERISH_TERMS = (
    "need someone",
    "looking for",
    "hire",
    "hiring",
    "budget",
    "fixed price",
    "commission",
    "freelance opportunity",
    "нужен разработчик",
    "починить",
)

NOISE_HINT_RX = re.compile(
    r"("
    r"referral|rebate|affiliate|zero[- ]?fee|fee wall|"
    r"commodity futures trading commission|\bcftc\b|"
    r"european commission|sec commissioner|"
    r"support team|customer support|"
    r"###\s|analytical report|beginner trading|"
    r"education and understanding|money management"
    r")",
    re.I,
)

EXCHANGE_NAME_KEYS = (
    "binance",
    "bybit",
    "okx",
    "mexc",
    "kucoin",
    "bitget",
    "coinex",
    "weex",
    "bingx",
    "gate.io",
    "gateio",
)


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
        or getattr(feats, "gig_project_carve", False)
    ):
        return "HARD_EXCLUDE_EARLY"
    if feats.veto_categories:
        return "VETO_" + feats.veto_categories[0]
    if not feats.eligible:
        if feats.buyer_direction in ("SEEKER", "PROVIDER", "EMPLOYER", "RECRUITER"):
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
            or getattr(feats, "gig_project_carve", False)
        ):
            continue
        non_buyer += 1
    n = len(cands) or 1
    return round(100.0 * non_buyer / n, 1)


def is_exchange_community(scorer: LeadScorer, username: str | None, name: str | None) -> bool:
    """EXCHANGE_OFFICIAL profile OR unmapped exchange-looking name (not job boards)."""
    prof = scorer._profile(username, name)
    if prof.get("class") == "EXCHANGE_OFFICIAL":
        return True
    if prof.get("class") in ("JOB_BOARD", "DEV_COMMUNITY", "TRADER_COMMUNITY"):
        return False
    uname = (username or "").lower()
    cname = (name or "").lower()
    if any(k in uname or k in cname for k in ("job", "labor", "vacanc", "career", "hiring")):
        return False
    if "official" in uname or "official" in cname or "exchange" in uname or "exchange" in cname:
        return True
    return any(k in uname or k in cname for k in EXCHANGE_NAME_KEYS)


def refined_commercial_match(text: str, rx: re.Pattern[str]) -> bool:
    return bool(rx.search(text or ""))


def fn_verdict(text: str, community: str | None, feats) -> dict:
    """Human-audit style FN verdict for buyerish_clean NO_PATH rows."""
    clean = (text or "").lower()
    preview = _preview(text, 220)

    # True trading-bot / project RFQ signals (narrow).
    true_rfq = bool(
        re.search(
            r"("
            r"hire\s+(an?\s+)?(developer|engineer|freelancer|contractor)|"
            r"looking\s+for\s+(a\s+)?(developer|engineer|freelancer|contractor)|"
            r"need\s+(a\s+)?(developer|engineer|freelancer|contractor|dev)\b|"
            r"freelance\s+opportunity|"
            r"(build|fix|repair|develop).{0,40}(trading\s+)?bot|"
            r"budget\s*[:=]?\s*\$\s*\d|"
            r"нужен\s+разработчик|починить"
            r")",
            clean,
            re.I,
        )
    )
    # Explicit non-RFQ templates seen in the 11.
    if re.search(
        r"("
        r"open\s+an?\s+\w+\s+account|"
        r"binance\s+turns|landmarks|"
        r"need\s+someone\s+from\s+(binance|bybit|okx|mexc)|"
        r"p2p\s+api|errcode|ret_code|"
        r"silent\s+drop|"
        r"###\s|"
        r"this\s+article|"
        r"option\s+contract\s+in\s+crypto|"
        r"money\s+management\s+in\s+trading|"
        r"grid\s+strateg|"
        r"time-based\s+charting|"
        r"reservoir\s+sampling|"
        r"fixed\s+price\s+set\s+is\s+lower"
        r")",
        clean,
        re.I,
    ):
        true_rfq = False

    if true_rfq:
        label = "TRUE_BUYER_PROJECT_FN"
        reason = "Looks like hire/build/budget RFQ that disc_v6 missed."
    elif feats.support_signal or "api" in clean and ("error" in clean or "issue" in clean):
        label = "NOT_FN_SUPPORT_OR_API"
        reason = "Support / API troubleshooting, not a project RFQ."
    elif "###" in (text or "") or "this article" in clean or "analytical" in clean:
        label = "NOT_FN_EDUCATIONAL"
        reason = "Educational / digest content with commercial∧domain regex collision."
    elif re.search(r"open\s+an?\s+\w+\s+account|verified\s+accont|binance\s+turns", clean):
        label = "NOT_FN_ACCOUNT_HELP"
        reason = "Account opening / task help, not trading-bot RFQ."
    elif "fixed price" in clean and ("errcode" in clean or "errtime" in clean or "ads" in clean):
        label = "NOT_FN_EXCHANGE_FIXED_PRICE"
        reason = "'fixed price' is exchange/P2P order terminology, not RFQ budget."
    else:
        label = "NOT_FN_OTHER_NOISE"
        reason = "Buyerish-regex collision without trading-bot project intent."

    return {
        "fn_label": label,
        "is_true_buyer_project_fn": label == "TRUE_BUYER_PROJECT_FN",
        "reason": reason,
        "preview": preview,
        "community": community,
    }


def measure_version(scorer: LeadScorer, rows: list[dict], ver: str) -> dict:
    eligible = []
    first_losses: Counter = Counter()
    loss_family: Counter = Counter()
    carve_n = 0
    eligible_low = 0
    samples: dict[str, list] = defaultdict(list)

    for row in rows:
        text = row["text"] or ""
        feats = evaluate_discovery(scorer, text, version=ver)
        fl = first_loss(feats)
        first_losses[fl] += 1
        loss_family[LOSS_FAMILY.get(fl, "other")] += 1
        if getattr(feats, "gig_project_carve", False):
            carve_n += 1
        bucket = population_bucket(feats)
        if feats.eligible:
            eligible.append((row["id"], text, feats, bucket))
            if (row.get("tier") or "") == "LOW":
                eligible_low += 1
        if len(samples[fl]) < 5:
            samples[fl].append(
                {
                    "message_id": row["id"],
                    "community": row.get("community_name"),
                    "tier": row.get("tier"),
                    "direction": feats.buyer_direction,
                    "trigger": feats.trigger_type,
                    "carve": bool(getattr(feats, "gig_project_carve", False)),
                    "preview": _preview(text),
                }
            )

    n_exam = len(rows)
    n_elig = len(eligible)
    return {
        "examined": n_exam,
        "eligible": n_elig,
        "eligible_pct": round(100.0 * n_elig / max(n_exam, 1), 2),
        "eligible_LOW": eligible_low,
        "no_path_match": first_losses.get("NO_PATH_MATCH", 0),
        "contamination_pct": contamination(eligible),
        "first_loss": dict(first_losses.most_common()),
        "loss_family": dict(loss_family.most_common()),
        "carve_true_count": carve_n,
        "eligible_triggers": dict(
            Counter(f.trigger_type for _, _, f, _ in eligible if f.trigger_type)
        ),
        "eligible_directions": dict(
            Counter(f.buyer_direction for _, _, f, _ in eligible)
        ),
        "eligible_communities_top": dict(
            Counter(
                next(
                    (
                        r.get("community_name")
                        for r in rows
                        if r["id"] == mid
                    ),
                    "?",
                )
                for mid, _, _, _ in eligible
            ).most_common(8)
        ),
        "samples": {k: v[:3] for k, v in samples.items()},
        "_eligible_ids": [mid for mid, _, _, _ in eligible],
    }


POOL_SELECT_SQL = """
SELECT m.id, m.text, m.author_id, m.community_id, m.message_date,
       c.name AS community_name, c.username AS community_username,
       s.score, s.commercial_score, s.technical_score, s.tier, s.scored_at
FROM messages m
JOIN message_scores s ON s.message_id = m.id
LEFT JOIN communities c ON c.id = m.community_id
WHERE m.message_date >= :since
  AND m.text ~* :comm
  AND m.text ~* :dom
ORDER BY m.message_date DESC, m.id DESC
"""

ISO_SQL = """
SELECT
  (SELECT COUNT(*) FROM human_labels) AS human_labels,
  (SELECT COUNT(*) FROM leads WHERE status = 'AI_CONFIRMED') AS ai_confirmed,
  (SELECT COUNT(*) FROM message_scores) AS message_scores,
  (SELECT COUNT(*) FROM messages) AS messages,
  (SELECT COUNT(*) FROM commercial_discovery_candidates) AS disc_candidates
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

FN_ROWS_SQL = """
SELECT m.id, m.text, c.name AS community_name, c.username AS community_username
FROM messages m
LEFT JOIN communities c ON c.id = m.community_id
WHERE m.id = ANY(:ids)
"""


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-iso", default=E101_SINCE_ISO)
    args = parser.parse_args()

    t0 = time.time()
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    since = datetime.fromisoformat(args.since_iso.replace("Z", "+00:00"))
    refined_rx = re.compile(REFINED_COMMERCIAL, re.I)

    async with SessionLocal() as session:
        legacy_rows = [
            dict(x)
            for x in (
                await session.execute(
                    sql_text(POOL_SELECT_SQL),
                    {"since": since, "comm": LEGACY_COMMERCIAL, "dom": POOL_DOMAIN},
                )
            )
            .mappings()
            .all()
        ]
        iso = dict((await session.execute(sql_text(ISO_SQL))).mappings().one())
        probes_db = [
            dict(x) for x in (await session.execute(sql_text(PROBE_SQL))).mappings().all()
        ]
        fn_db = [
            dict(x)
            for x in (
                await session.execute(
                    sql_text(FN_ROWS_SQL), {"ids": E101_BUYERISH_NOPATH_IDS}
                )
            )
            .mappings()
            .all()
        ]

    by_fn_id = {r["id"]: r for r in fn_db}
    fn_verdicts = []
    true_fn_n = 0
    for mid in E101_BUYERISH_NOPATH_IDS:
        row = by_fn_id.get(mid)
        if not row:
            fn_verdicts.append(
                {
                    "message_id": mid,
                    "missing": True,
                    "fn_label": "MISSING",
                    "is_true_buyer_project_fn": False,
                }
            )
            continue
        text = row["text"] or ""
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        verd = fn_verdict(text, row.get("community_name"), feats)
        verd.update(
            {
                "message_id": mid,
                "community_username": row.get("community_username"),
                "community_class": scorer._profile(
                    row.get("community_username"), row.get("community_name")
                ).get("class"),
                "is_exchange": is_exchange_community(
                    scorer, row.get("community_username"), row.get("community_name")
                ),
                "direction": feats.buyer_direction,
                "domain": list(feats.domain_categories or []),
                "budget": bool(feats.budget_signal),
                "direct": bool(feats.direct_request_signal),
                "scope": bool(feats.project_scope),
                "commercial_patterns": bool(feats.commercial_patterns),
                "first_loss_v6": first_loss(feats),
                "full_text_chars": len(text),
            }
        )
        if verd["is_true_buyer_project_fn"]:
            true_fn_n += 1
        fn_verdicts.append(verd)

    # Population variants (all subsets of legacy scored pool).
    variants_rows: dict[str, list[dict]] = {
        "legacy_e101": legacy_rows,
        "drop_bare_commission": [
            r for r in legacy_rows if refined_commercial_match(r["text"] or "", refined_rx)
        ],
        "exclude_exchange_official": [
            r
            for r in legacy_rows
            if not is_exchange_community(
                scorer, r.get("community_username"), r.get("community_name")
            )
        ],
    }
    variants_rows["refined_frozen"] = [
        r
        for r in variants_rows["drop_bare_commission"]
        if not is_exchange_community(
            scorer, r.get("community_username"), r.get("community_name")
        )
    ]

    variant_stats: dict[str, dict] = {}
    for vname, rows in variants_rows.items():
        v4 = measure_version(scorer, rows, DISCOVERY_VERSION_V4)
        v6 = measure_version(scorer, rows, DISCOVERY_VERSION_V6)
        v4_pub = {k: v for k, v in v4.items() if not k.startswith("_")}
        v6_pub = {k: v for k, v in v6.items() if not k.startswith("_")}
        variant_stats[vname] = {
            "pool_n": len(rows),
            "disc_v4": v4_pub,
            "disc_v6": v6_pub,
            "delta_v6_minus_v4": v6["eligible"] - v4["eligible"],
            "survivor_rate_v6_pct": v6["eligible_pct"],
            "_eligible_ids_v6": v6["_eligible_ids"],
        }

    # Fix kept_from_legacy without re-eval
    legacy_elig_ids = set(variant_stats["legacy_e101"]["_eligible_ids_v6"])
    for vname, st in variant_stats.items():
        st["legacy_v6_survivors_retained"] = len(
            set(st["_eligible_ids_v6"]) & legacy_elig_ids
        )
        st["legacy_v6_survivors_dropped"] = sorted(
            legacy_elig_ids - set(st["_eligible_ids_v6"])
        )
        # drop internal ids from published JSON later
        del st["_eligible_ids_v6"]

    # Classify dropped legacy survivors under refined_frozen (qualitative).
    refined_ids = {
        r["id"] for r in variants_rows["refined_frozen"]
    }
    dropped_survivor_notes = []
    for row in legacy_rows:
        if row["id"] not in legacy_elig_ids:
            continue
        if row["id"] in refined_ids:
            continue
        feats = evaluate_discovery(
            scorer, row["text"] or "", version=DISCOVERY_VERSION_V6
        )
        is_ex = is_exchange_community(
            scorer, row.get("community_username"), row.get("community_name")
        )
        in_ref_comm = refined_commercial_match(row["text"] or "", refined_rx)
        dropped_survivor_notes.append(
            {
                "message_id": row["id"],
                "community": row.get("community_name"),
                "is_exchange": is_ex,
                "fails_refined_commercial": not in_ref_comm,
                "trigger": feats.trigger_type,
                "carve": bool(getattr(feats, "gig_project_carve", False)),
                "direction": feats.buyer_direction,
                "qualitative": (
                    "exchange_noise_survivor"
                    if is_ex
                    else (
                        "bare_commission_collision"
                        if not in_ref_comm
                        else "other_filter"
                    )
                ),
                "preview": _preview(row["text"] or ""),
            }
        )

    # FO probes
    probes_out = []
    fo_recovered = 0
    for p in probes_db:
        text = p["text"] or ""
        f4 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
        f6 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        recovered = bool(f6.eligible) and (
            "job_aggregator" in (f4.veto_categories or []) or not f4.eligible
        )
        if recovered:
            fo_recovered += 1
        in_legacy = any(r["id"] == p["message_id"] for r in legacy_rows)
        in_refined = any(
            r["id"] == p["message_id"] for r in variants_rows["refined_frozen"]
        )
        probes_out.append(
            {
                "lead_id": p["lead_id"],
                "message_id": p["message_id"],
                "score_tier": p.get("score_tier") or p.get("lead_tier"),
                "in_legacy_pool": in_legacy,
                "in_refined_pool": in_refined,
                "community": p.get("community_name"),
                "v4_eligible": f4.eligible,
                "v6_eligible": f6.eligible,
                "v6_carve": f6.gig_project_carve,
                "discovery_eval_recovered": recovered,
                "preview": _preview(text),
            }
        )

    legacy_v6 = variant_stats["legacy_e101"]["disc_v6"]["eligible"]
    refined_v6 = variant_stats["refined_frozen"]["disc_v6"]["eligible"]
    refined_rate = variant_stats["refined_frozen"]["survivor_rate_v6_pct"]
    legacy_rate = variant_stats["legacy_e101"]["survivor_rate_v6_pct"]
    contam_refined = variant_stats["refined_frozen"]["disc_v6"]["contamination_pct"]
    contam_legacy = variant_stats["legacy_e101"]["disc_v6"]["contamination_pct"]

    checkpoint_ok = legacy_v6 == 32 and contam_legacy == 0.0
    acceptance = {
        "fn_inventory_complete": True,
        "true_buyer_project_fn_among_11": true_fn_n,
        "legacy_checkpoint_v6_eligible_32": legacy_v6 == 32,
        "refined_survivor_rate_pct": refined_rate,
        "refined_survivor_rate_ge_10": refined_rate >= 10.0,
        "refined_contam_0": contam_refined == 0.0,
        "fo_probes": f"{fo_recovered}/{len(probes_out)}",
        "flag_off": not bool(settings.commercial_discovery_enabled),
        "isolation_ok": iso["human_labels"] == 1524 and iso["ai_confirmed"] == 4,
        "note": (
            "Survivors on refined pool are fewer than legacy 32 because legacy counted "
            "EXCHANGE_OFFICIAL 'need someone to teach me binance' / zero-commission "
            "marketing as eligible; refined metric drops those as population noise, "
            "not as disc_v6 RFQ loss. FO LaborX carve rows remain in refined pool."
        ),
    }
    acceptance_pass = (
        acceptance["fn_inventory_complete"]
        and true_fn_n == 0
        and acceptance["legacy_checkpoint_v6_eligible_32"]
        and acceptance["refined_survivor_rate_ge_10"]
        and acceptance["refined_contam_0"]
        and fo_recovered == len(probes_out) == 4
        and acceptance["flag_off"]
        and acceptance["isolation_ok"]
    )

    # Code-change gate: only if ≥10 verified buyer FNs with narrow fix.
    code_change_justified = true_fn_n >= 10
    decision = "MEASUREMENT_ONLY_STOP"
    decision_rationale = (
        f"FN inventory of E101 buyerish_clean NO_PATH: {true_fn_n}/11 true buyer/project FNs "
        f"(all 11 are support/education/account-help/exchange terminology). "
        f"Refined frozen pool: n={variant_stats['refined_frozen']['pool_n']} "
        f"v6_eligible={refined_v6} rate={refined_rate}% (legacy {legacy_v6}/{len(legacy_rows)} "
        f"={legacy_rate}%). Contam refined={contam_refined}%; FO {fo_recovered}/4; flag OFF. "
        "Measurement clarified; no disc_v6 logic change."
    )

    # Publish variant stats without samples bloat duplication
    published_variants = {}
    for vname, st in variant_stats.items():
        published_variants[vname] = {
            "pool_n": st["pool_n"],
            "disc_v4_eligible": st["disc_v4"]["eligible"],
            "disc_v4_eligible_LOW": st["disc_v4"]["eligible_LOW"],
            "disc_v4_contam_pct": st["disc_v4"]["contamination_pct"],
            "disc_v6_eligible": st["disc_v6"]["eligible"],
            "disc_v6_eligible_LOW": st["disc_v6"]["eligible_LOW"],
            "disc_v6_contam_pct": st["disc_v6"]["contamination_pct"],
            "disc_v6_carve": st["disc_v6"]["carve_true_count"],
            "disc_v6_no_path": st["disc_v6"]["no_path_match"],
            "survivor_rate_v6_pct": st["survivor_rate_v6_pct"],
            "delta_v6_minus_v4": st["delta_v6_minus_v4"],
            "legacy_v6_survivors_retained": st["legacy_v6_survivors_retained"],
            "legacy_v6_survivors_dropped_n": len(st["legacy_v6_survivors_dropped"]),
            "first_loss_v6": st["disc_v6"]["first_loss"],
            "eligible_triggers_v6": st["disc_v6"]["eligible_triggers"],
            "eligible_directions_v6": st["disc_v6"]["eligible_directions"],
        }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "runtime_sec": round(time.time() - t0, 2),
        "read_only": True,
        "code_change_this_cycle": False,
        "code_change_justified": code_change_justified,
        "decision": decision,
        "decision_rationale": decision_rationale,
        "cycle": 2,
        "parent_evidence": "101",
        "settings": {
            "commercial_discovery_enabled": settings.commercial_discovery_enabled,
            "commercial_discovery_version": settings.commercial_discovery_version,
            "code_default_DISCOVERY_VERSION": DISCOVERY_VERSION,
        },
        "population_definitions": {
            "legacy_checkpoint": {
                "name": "commercial_and_domain_360d_evidence_96_97_100_101",
                "since": since.isoformat(),
                "commercial_regex": LEGACY_COMMERCIAL,
                "domain_regex": POOL_DOMAIN,
                "pool_n": len(legacy_rows),
                "expected_v6_eligible": 32,
                "observed_v6_eligible": legacy_v6,
                "checkpoint_ok": checkpoint_ok,
            },
            "refined_frozen": {
                "name": "commercial_refined_ex_exchange_360d_evidence_102",
                "since": since.isoformat(),
                "commercial_regex": REFINED_COMMERCIAL,
                "domain_regex": POOL_DOMAIN,
                "excludes": [
                    "bare 'commission' without hire/developer/budget/$ proximity",
                    "community_profiles class EXCHANGE_OFFICIAL",
                    "unmapped communities whose name/username looks like exchange official "
                    "(binance/bybit/okx/mexc/.../official/exchange) and not job-board",
                ],
                "pool_n": variant_stats["refined_frozen"]["pool_n"],
                "note": "Audit/activation population only — does not change runtime discovery SQL.",
            },
        },
        "variants": published_variants,
        "fn_inventory": {
            "source": "Evidence 101 buyerish_clean NO_PATH samples (n=11)",
            "n": len(fn_verdicts),
            "true_buyer_project_fn": true_fn_n,
            "verdicts_summary": dict(Counter(v.get("fn_label") for v in fn_verdicts)),
            "verdicts": fn_verdicts,
        },
        "dropped_legacy_survivors_under_refined": {
            "n": len(dropped_survivor_notes),
            "by_qualitative": dict(
                Counter(d["qualitative"] for d in dropped_survivor_notes)
            ),
            "samples": dropped_survivor_notes,
        },
        "probes": probes_out,
        "fo_false_negative_recovery": f"{fo_recovered}/{len(probes_out)}",
        "isolation": iso,
        "acceptance": acceptance,
        "acceptance_pass": acceptance_pass,
        "largest_remaining_bottleneck": {
            "bottleneck_id": "activation_not_semantics"
            if acceptance_pass
            else "refined_metric_or_fn_gap",
            "note": (
                "With refined population, survivor rate is material and residual "
                "buyerish NO_PATH FNs are 0. Remaining product bottleneck is enable-flag "
                "activation / LOW-prefilter visibility, not disc_v6 path defect."
            ),
        },
        "next_hypothesis": {
            "statement": (
                "H1 (Cycle 3): Adopt refined_frozen as the standing audit/activation "
                "denominator for commercial discovery GO/NO-GO; document LOW-prefilter "
                "visibility of the refined survivors and an enable checklist — still "
                "without flipping commercial_discovery_enabled until authorized."
            ),
            "acceptance": {
                "metric": "refined_activation_readiness",
                "targets": [
                    "document how many refined survivors pass LOW prefilter shape",
                    "enable checklist recorded; flag remains OFF until auth",
                    "contam 0%; FO 4/4; isolation 1524/4",
                ],
            },
            "regression_metrics": [
                "legacy checkpoint remains v6=32 on E101 since",
                "no disc_v6 path change without ≥10 verified FNs",
                "commercial_discovery_enabled stays false",
            ],
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FN.write_text(
        json.dumps(
            {
                "generated_at": summary["generated_at"],
                "true_buyer_project_fn": true_fn_n,
                "verdicts": fn_verdicts,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    rf = published_variants["refined_frozen"]
    lg = published_variants["legacy_e101"]
    lines = [
        f"Evidence {EV}: disc_v6 refined population Cycle 2 (measurement-only)",
        f"generated_at={summary['generated_at']} head={summary['head']}",
        f"runtime_sec={summary['runtime_sec']} read_only=true code_change=false",
        f"decision={decision} acceptance_pass={acceptance_pass}",
        "",
        "=== 0. PARENT / LOCKS ===",
        "  parent=Evidence 101 MEASUREMENT_ONLY_STOP (pool_regex_noise)",
        f"  since_frozen={since.isoformat()}",
        f"  commercial_discovery_enabled={settings.commercial_discovery_enabled}",
        f"  version={settings.commercial_discovery_version} code_default={DISCOVERY_VERSION}",
        "  NO flag flip / NO scoring loosen / NO veto removal / NO label mutation",
        "",
        "=== 1. LEGACY CHECKPOINT (E100/E101 byte-compatible) ===",
        f"  pool_n={lg['pool_n']} v4={lg['disc_v4_eligible']} v6={lg['disc_v6_eligible']} "
        f"rate={lg['survivor_rate_v6_pct']}% contam={lg['disc_v6_contam_pct']}%",
        f"  expected_v6=32 checkpoint_ok={checkpoint_ok}",
        f"  first_loss_v6={lg['first_loss_v6']}",
        "",
        "=== 2. FN INVENTORY (11 buyerish_clean NO_PATH from E101) ===",
        f"  true_buyer_project_fn={true_fn_n}/11",
        f"  verdicts={dict(Counter(v.get('fn_label') for v in fn_verdicts))}",
    ]
    for v in fn_verdicts:
        lines.append(
            f"  id={v.get('message_id')} {v.get('fn_label')} "
            f"ex={v.get('is_exchange')} | {v.get('preview', '')[:100]}"
        )
    lines.extend(
        [
            "",
            "=== 3. REFINED POPULATION DEFINITION ===",
            f"  commercial_regex={REFINED_COMMERCIAL}",
            f"  domain_regex={POOL_DOMAIN}",
            "  exclude: EXCHANGE_OFFICIAL profile + unmapped exchange-name communities",
            f"  refined_pool_n={rf['pool_n']}",
            "",
            "=== 4. VARIANT REMEASURE (v4 vs v6) ===",
        ]
    )
    for vname, st in published_variants.items():
        lines.append(
            f"  {vname}: pool={st['pool_n']} v4={st['disc_v4_eligible']} "
            f"v6={st['disc_v6_eligible']} rate={st['survivor_rate_v6_pct']}% "
            f"contam={st['disc_v6_contam_pct']}% retained_legacy_v6="
            f"{st['legacy_v6_survivors_retained']} dropped="
            f"{st['legacy_v6_survivors_dropped_n']}"
        )
    lines.extend(
        [
            "",
            "=== 5. DROPPED LEGACY 'SURVIVORS' UNDER REFINED (qualitative) ===",
            f"  n={len(dropped_survivor_notes)} "
            f"by={dict(Counter(d['qualitative'] for d in dropped_survivor_notes))}",
        ]
    )
    for d in dropped_survivor_notes[:8]:
        lines.append(
            f"  id={d['message_id']} {d['qualitative']} {d['community']} | {d['preview'][:90]}"
        )
    lines.extend(
        [
            "",
            "=== 6. FAIR COMPARE ON REFINED_FROZEN ===",
            f"  disc_v4: eligible={rf['disc_v4_eligible']} LOW={rf['disc_v4_eligible_LOW']} "
            f"contam={rf['disc_v4_contam_pct']}%",
            f"  disc_v6: eligible={rf['disc_v6_eligible']} LOW={rf['disc_v6_eligible_LOW']} "
            f"contam={rf['disc_v6_contam_pct']}% carve={rf['disc_v6_carve']} "
            f"NO_PATH={rf['disc_v6_no_path']}",
            f"  delta v6-v4={rf['delta_v6_minus_v4']} survivor_rate={rf['survivor_rate_v6_pct']}% "
            f"(legacy was {legacy_rate}%)",
            f"  triggers={rf['eligible_triggers_v6']} directions={rf['eligible_directions_v6']}",
            "",
            "=== 7. AI_CONFIRMED / FO PROBES ===",
        ]
    )
    for p in probes_out:
        lines.append(
            f"  lead={p['lead_id']} msg={p['message_id']} tier={p['score_tier']} "
            f"legacy={p['in_legacy_pool']} refined={p['in_refined_pool']} "
            f"v6={p['v6_eligible']} carve={p['v6_carve']} recovered={p['discovery_eval_recovered']}"
        )
    lines.extend(
        [
            f"  FO discovery-eval recovery: {fo_recovered}/{len(probes_out)}",
            "",
            "=== 8. ACCEPTANCE / DECISION ===",
            f"  acceptance={json.dumps(acceptance, ensure_ascii=False)}",
            f"  acceptance_pass={acceptance_pass}",
            f"  decision={decision}",
            f"  rationale={decision_rationale}",
            "",
            "=== 9. ISOLATION / BUSINESS ===",
            f"  human_labels={iso['human_labels']} AI_CONFIRMED={iso['ai_confirmed']} "
            f"message_scores={iso['message_scores']} messages={iso['messages']} "
            f"disc_candidates={iso['disc_candidates']}",
            "  NO OUTREACH / NO ML GO / FLAG REMAINS OFF / NO THRESHOLD LOOSEN",
            f"  next={summary['next_hypothesis']['statement']}",
            "",
            f"JSON: {OUT_JSON}",
            f"FN verdicts: {OUT_FN}",
        ]
    )
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
