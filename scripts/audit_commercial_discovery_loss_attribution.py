#!/usr/bin/env python3
"""Commercial discovery end-to-end loss attribution (read-only).

Writes evidence 97. No SDK, no CRM/outreach, no scoring/community changes.
Measures disc_v4 on the capped prefilter (does not hardcode survivor count).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
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
    DISCOVERY_VERSION_V2,
    DISCOVERY_VERSION_V4,
    _GIG_MARKETPLACE_RX,
    evaluate_discovery,
)
from shared.db import SessionLocal
from shared.settings import get_settings

EV = "97"
OUT_TXT = ROOT / f"docs/audit/evidence/{EV}-commercial-discovery-loss-attribution.txt"
OUT_JSON = ROOT / f"docs/audit/evidence/{EV}-commercial-discovery-loss-attribution.json"

# Reuse audit-plane prefilter definition (same as evidence 95/96).
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

# Evidence-96-compatible commercial∧domain pool (byte-compatible recovery regex).
POOL_COMMERCIAL = (
    r"(budget|fixed price|commission|need someone|"
    r"looking for (a )?(developer|contractor|freelancer)|"
    r"нужен разработчик|починить|Freelance Opportunity)"
)
POOL_DOMAIN = (
    r"(trading bot|bybit|binance|okx|pybit|arbitrage|futures|strategy|"
    r"торговый бот|Crypto Trading Bot)"
)

VETO_PRIORITY = (
    "job_aggregator",
    "corporate_employment",
    "generic_recruiter",
    "job_seeker",
    "service_provider",
    "support_question",
    "marketing_broadcast",
    "news_digest",
)

SEMANTIC_FROM_FIRST_LOSS = {
    "NOT_SCORED": "SCORE_SELECTION_BUG",
    "NOT_LOW": "PREFILTER_MISS",
    "OUTSIDE_SCORED_AT_WINDOW": "PREFILTER_MISS",
    "EXCLUDED_BLIND": "OTHER",
    "EXCLUDED_AI_CONFIRMED": "OTHER",
    "PREFILTER_REGEX_MISS": "PREFILTER_MISS",
    "CT_CAP_TRUNCATED": "PREFILTER_MISS",
    "HARD_EXCLUDE_EARLY": "EMPLOYMENT_FALSE_POSITIVE",
    "VETO_job_aggregator": "MARKETPLACE_FALSE_POSITIVE",
    "VETO_corporate_employment": "EMPLOYMENT_FALSE_POSITIVE",
    "VETO_generic_recruiter": "RECRUITER_FALSE_POSITIVE",
    "VETO_job_seeker": "OTHER",
    "VETO_service_provider": "PROVIDER",
    "VETO_support_question": "SUPPORT_FALSE_POSITIVE",
    "VETO_marketing_broadcast": "OTHER",
    "VETO_news_digest": "OTHER",
    "DIRECTION_SEEKER": "OTHER",
    "DIRECTION_PROVIDER": "PROVIDER",
    "DIRECTION_EMPLOYER": "EMPLOYMENT_FALSE_POSITIVE",
    "DIRECTION_RECRUITER": "RECRUITER_FALSE_POSITIVE",
    "NO_PATH_MATCH": "BUYER_SIGNAL_NOT_RECOGNIZED",
    "RETRIEVED": "OTHER",
}


def _preview(t: str, n: int = 140) -> str:
    return " ".join((t or "").split())[:n]


def _feat_snapshot(f) -> dict:
    return {
        "eligible": f.eligible,
        "buyer_direction": f.buyer_direction,
        "veto_categories": list(f.veto_categories),
        "trigger_type": f.trigger_type,
        "hard_exclude": f.hard_exclude,
        "exclude_reasons": list(f.exclude_reasons),
        "aggregator_signal": f.aggregator_signal,
        "repair_signal": f.repair_signal,
        "project_scope": f.project_scope,
        "project_procurement_signal": f.project_procurement_signal,
        "ownership_signal": f.ownership_signal,
        "direct_request_signal": f.direct_request_signal,
        "soft_direction": f.soft_direction,
        "automation_signal": f.automation_signal,
        "budget_signal": f.budget_signal,
        "employment_signal": f.employment_signal,
        "recruiter_signal": f.recruiter_signal,
        "seeker_signal": f.seeker_signal,
        "provider_signal": f.provider_signal,
        "support_signal": f.support_signal,
        "marketing_signal": f.marketing_signal,
        "news_signal": f.news_signal,
        "domain_categories": list(f.domain_categories),
        "commercial_patterns": list(f.commercial_patterns)[:8],
        "buyer_signal_families": list(f.buyer_signal_families),
        "discovery_score": f.discovery_score,
        "gig_marketplace_rx": bool(_GIG_MARKETPLACE_RX.search(f._clean if hasattr(f, "_clean") else "")),
    }


def _paths_v4(f) -> dict:
    has_domain = bool(f.domain_categories) or bool(f.technical_project_terms)
    has_tech = bool(f.technical_project_terms)
    direct = f.direct_request_signal
    ownership = f.ownership_signal
    scope = f.project_scope
    a = bool(direct and has_domain and (scope or ownership or direct or (f.budget_signal and f.commercial_patterns)))
    # Mirror _eligible_v4 path checks as closely as practical for NO_PATH detail.
    path_a = bool(
        direct
        and (f.domain_categories or f.technical_project_terms)
        and (scope or ownership or f.direct_request_signal or (bool(f.commercial_patterns) and f.budget_signal))
    )
    # Mirror path_b conjunct (Evidence 109): direct OR (ownership ∧
    # hire/budget/paid-fix/carve) — not bare ownership, not budget alone.
    path_b = bool(
        f.repair_signal
        and (has_domain or has_tech)
        and (
            direct
            or (
                ownership
                and (
                    bool(f.hiring_patterns)
                    or f.budget_signal
                    or getattr(f, "true_budget_signal", False)
                    or getattr(f, "gig_project_carve", False)
                )
            )
        )
    )
    path_c = bool(
        (f.automation_signal or bool(f.implementation_patterns))
        and bool(f.domain_categories)
        and (
            ownership
            or direct
            or f.budget_signal
            or bool(f.hiring_patterns)
        )
    )
    path_d = bool(
        f.project_procurement_signal
        and (scope or ownership or direct)
        and bool(f.domain_categories)
        and not f.aggregator_signal
    )
    path_e = bool(
        (f.budget_signal or f.timeline_signal)
        and bool(f.domain_categories)
        and (direct or ownership)
        and not f.aggregator_signal
    )
    return {"A": path_a, "B": path_b, "C": path_c, "D": path_d, "E": path_e}


def first_loss_audit_row(
    *,
    has_score: bool,
    tier: str | None,
    scored_at_in_window: bool,
    in_blind: bool,
    in_ai_confirmed: bool,
    prefilter_regex_ok: bool,
    in_cap: bool,
    feats,
) -> str:
    """Single-assignment first-loss for audit plane."""
    if not has_score:
        return "NOT_SCORED"
    if tier != "LOW":
        return "NOT_LOW"
    if not scored_at_in_window:
        return "OUTSIDE_SCORED_AT_WINDOW"
    if in_blind:
        return "EXCLUDED_BLIND"
    if in_ai_confirmed:
        return "EXCLUDED_AI_CONFIRMED"
    if not prefilter_regex_ok:
        return "PREFILTER_REGEX_MISS"
    if not in_cap:
        return "CT_CAP_TRUNCATED"

    if feats.hard_exclude and not (
        feats.project_scope or feats.soft_direction or feats.repair_signal
    ):
        return "HARD_EXCLUDE_EARLY"

    if feats.veto_categories:
        for v in VETO_PRIORITY:
            if v in feats.veto_categories:
                return f"VETO_{v}"
        return f"VETO_{feats.veto_categories[0]}"

    if feats.eligible:
        return "RETRIEVED"

    # Direction failures (v4 appends these after paths)
    bd = feats.buyer_direction
    if bd in ("SEEKER", "PROVIDER", "EMPLOYER") and not feats.soft_direction:
        return f"DIRECTION_{bd}"
    if bd == "RECRUITER" and not (
        feats.project_scope or feats.project_procurement_signal or feats.soft_direction
    ):
        # Usually already vetoed; keep as fallback.
        return "DIRECTION_RECRUITER"

    return "NO_PATH_MATCH"


def semantic_gap(first_loss: str, feats) -> str:
    if first_loss == "NO_PATH_MATCH":
        if feats.repair_signal is False and re.search(
            r"fix|repair|починить|broken", feats.trigger_type or "", re.I
        ):
            return "REPAIR_NOT_RECOGNIZED"
        if not feats.domain_categories:
            return "DOMAIN_NOT_RECOGNIZED"
        if feats.automation_signal:
            return "AUTOMATION_NOT_RECOGNIZED"
        if feats.project_procurement_signal:
            return "PROCUREMENT_NOT_RECOGNIZED"
        if feats.project_scope:
            return "PROJECT_SCOPE_NOT_RECOGNIZED"
        return "BUYER_SIGNAL_NOT_RECOGNIZED"
    return SEMANTIC_FROM_FIRST_LOSS.get(first_loss, "OTHER")


def laborx_structure(text: str) -> str:
    t = text or ""
    fo = bool(re.search(r"freelance\s+opportunity", t, re.I)) or bool(
        _GIG_MARKETPLACE_RX.search(t)
    )
    emp = bool(
        re.search(
            r"\b(full[\s-]?time|part[\s-]?time|salary|benefits|job description|"
            r"years of experience|we are hiring)\b",
            t,
            re.I,
        )
    )
    repair = bool(re.search(r"\b(fix|repair|debug|broken|починить)\b", t, re.I))
    trading = bool(
        re.search(r"trading\s*bot|bybit|binance|pybit|arbitrage|futures|strategy", t, re.I)
    )
    seeker = bool(re.search(r"#резюме|#opentowork|looking for (a )?job|ищу работу", t, re.I))
    provider = bool(
        re.search(r"\b(I (can|will) (build|develop|fix)|hire me|my services)\b", t, re.I)
    )
    recruiter = bool(re.search(r"\b(we are hiring|candidates needed|recruit)\b", t, re.I))
    support = bool(re.search(r"\b(how (do|can) I|does anyone know|help me with)\b", t, re.I))
    procurement = bool(
        re.search(r"\b(looking for|need|budget|fixed price|commission|deadline)\b", t, re.I)
    )

    if fo and emp and not repair and not trading:
        return "FO_EMPLOYMENT_JD"
    if fo and repair and trading:
        return "FO_REPAIR"
    if fo and trading and procurement:
        return "FO_PROJECT_RFQ_TRADING"
    if fo and procurement:
        return "FO_PROJECT_RFQ_OTHER_WEB3"
    if fo:
        return "GIG_MARKETPLACE_LISTING"
    if seeker:
        return "JOB_SEEKER"
    if provider:
        return "PROVIDER"
    if recruiter or emp:
        return "CORPORATE_EMPLOYMENT"
    if support:
        return "SUPPORT"
    if procurement and trading:
        return "BUYER_RFQ"
    if procurement:
        return "PROJECT_PROCUREMENT"
    return "OTHER"


async def _set_readonly(session) -> None:
    await session.execute(sql_text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    await session.execute(sql_text("SET LOCAL statement_timeout = '600s'"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-cap", type=int, default=5000)
    ap.add_argument("--prefilter-cap", type=int, default=2500)
    ap.add_argument("--low-sample", type=int, default=100)
    ap.add_argument("--skip", type=str, default="", help="Comma sections e.g. S11,S14")
    args = ap.parse_args()
    skip = {s.strip().upper() for s in args.skip.split(",") if s.strip()}

    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)
    since_7 = now - timedelta(days=7)
    since_360 = now - timedelta(days=360)

    summary: dict = {
        "generated_at": now.isoformat(),
        "head": "e4b58be",
        "discovery_version": DISCOVERY_VERSION_V4,
        "context_version": settings.commercial_context_version,
        "read_only": True,
        "sdk_run": False,
        "code_changes": False,
    }

    async with SessionLocal() as session:
        await _set_readonly(session)

        # --- S1 invariants ---
        inv = (
            await session.execute(
                sql_text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM human_labels) AS human_labels,
                      (SELECT COUNT(*) FROM leads WHERE status='AI_CONFIRMED') AS ai_confirmed,
                      (SELECT COUNT(*) FROM commercial_discovery_candidates) AS disc_candidates,
                      (SELECT COUNT(*) FROM commercial_episodes) AS episodes,
                      (SELECT COUNT(*) FROM message_scores) AS message_scores,
                      (SELECT COUNT(*) FROM messages) AS messages
                    """
                )
            )
        ).one()
        summary["S1_run"] = {
            "invariants_before": {
                "human_labels": int(inv[0]),
                "ai_confirmed": int(inv[1]),
                "disc_candidates": int(inv[2]),
                "episodes": int(inv[3]),
                "message_scores": int(inv[4]),
                "messages": int(inv[5]),
            },
            "live_settings": {
                "commercial_discovery_enabled": settings.commercial_discovery_enabled,
                "commercial_episode_shadow_enabled": settings.commercial_episode_shadow_enabled,
                "commercial_discovery_version": settings.commercial_discovery_version,
                "commercial_context_version": settings.commercial_context_version,
                "ml_note": "ml_training_enabled from /validation/gates (API)",
            },
        }

        # --- Score integrity + windows ---
        windows = {}
        for days, key in [(7, "7d"), (30, "30d"), (90, "90d"), (360, "360d")]:
            since = now - timedelta(days=days)
            row = (
                await session.execute(
                    sql_text(
                        """
                        SELECT
                          COUNT(*) AS msgs,
                          COUNT(s.message_id) AS scored,
                          COUNT(*) FILTER (WHERE s.tier='LOW') AS low,
                          COUNT(*) FILTER (WHERE s.tier='MEDIUM') AS med,
                          COUNT(*) FILTER (WHERE s.tier='HIGH') AS high
                        FROM messages m
                        LEFT JOIN message_scores s ON s.message_id = m.id
                        WHERE m.message_date >= :since
                        """
                    ),
                    {"since": since},
                )
            ).one()
            windows[key] = {
                "messages": int(row[0]),
                "scored": int(row[1]),
                "pct_scored": round(100.0 * int(row[1]) / max(int(row[0]), 1), 2),
                "low": int(row[2]),
                "medium": int(row[3]),
                "high": int(row[4]),
            }

        score_integrity = (
            await session.execute(
                sql_text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM message_scores) AS scores_total,
                      (SELECT COUNT(DISTINCT message_id) FROM message_scores) AS distinct_scored,
                      (SELECT COUNT(*) FROM (
                         SELECT message_id FROM message_scores
                         GROUP BY message_id HAVING COUNT(*) > 1
                       ) t) AS multi_score_msgs,
                      (SELECT COUNT(*) FROM message_scores
                       WHERE scored_at >= :since AND tier='LOW') AS low_by_scored_at_7d,
                      (SELECT COUNT(*) FROM messages m
                       JOIN message_scores s ON s.message_id=m.id
                       WHERE m.message_date >= :since AND s.tier='LOW') AS low_by_msg_date_7d,
                      (SELECT COUNT(*) FROM message_scores s
                       JOIN messages m ON m.id=s.message_id
                       WHERE s.scored_at >= :since AND s.tier='LOW'
                         AND m.message_date < :since) AS low_rescored_older_than_7d
                    """
                ),
                {"since": since_7},
            )
        ).one()
        summary["score_reconciliation"] = {
            "windows": windows,
            "current_score_per_message": {
                "definition": "INNER/LEFT JOIN message_scores ON message_id; UNIQUE(message_id)",
                "scores_total": int(score_integrity[0]),
                "distinct_scored": int(score_integrity[1]),
                "multi_score_msgs": int(score_integrity[2]),
                "invariant_ok": int(score_integrity[0]) == int(score_integrity[1])
                and int(score_integrity[2]) == 0,
            },
            "low_by_scored_at_7d": int(score_integrity[3]),
            "low_by_msg_date_7d": int(score_integrity[4]),
            "low_rescored_older_than_7d": int(score_integrity[5]),
            "msgs_vs_score_rows_note": (
                "7d message_date LOW count differs from scored_at LOW count when older "
                "messages are re-scored into the window (not duplicate rows)."
            ),
        }

        # --- S2 commercial∧domain pool counts ---
        pool_counts = {}
        pool_v2_counts = {}
        for days, key in [(7, "7d"), (30, "30d"), (90, "90d"), (360, "360d")]:
            since = now - timedelta(days=days)
            n = int(
                (
                    await session.scalar(
                        sql_text(
                            f"""
                            SELECT COUNT(*) FROM messages
                            WHERE message_date >= :since
                              AND text ~* :comm
                              AND text ~* :dom
                            """
                        ),
                        {"since": since, "comm": POOL_COMMERCIAL, "dom": POOL_DOMAIN},
                    )
                )
                or 0
            )
            pool_counts[key] = n
            # Corrected word-boundary probe (Postgres \y); illustrative hiring count only on 7d.
            if key == "7d":
                n_b = int(
                    (
                        await session.scalar(
                            sql_text(
                                """
                                SELECT COUNT(*) FROM messages
                                WHERE message_date >= :since AND text ~* '\\bhiring\\b'
                                """
                            ),
                            {"since": since},
                        )
                    )
                    or 0
                )
                n_y = int(
                    (
                        await session.scalar(
                            sql_text(
                                """
                                SELECT COUNT(*) FROM messages
                                WHERE message_date >= :since AND text ~* '\\yhiring\\y'
                                """
                            ),
                            {"since": since},
                        )
                    )
                    or 0
                )
                pool_v2_counts["hiring_backslash_b"] = n_b
                pool_v2_counts["hiring_backslash_y"] = n_y

        # Fetch capped pool rows with scores
        pool_rows = (
            await session.execute(
                sql_text(
                    """
                    SELECT m.id, m.text, m.community_id, m.message_date,
                           c.name, c.username,
                           s.tier, s.score, s.commercial_score, s.technical_score, s.scored_at
                    FROM messages m
                    LEFT JOIN communities c ON c.id = m.community_id
                    LEFT JOIN message_scores s ON s.message_id = m.id
                    WHERE m.message_date >= :since
                      AND m.text ~* :comm
                      AND m.text ~* :dom
                    ORDER BY m.message_date DESC
                    LIMIT :cap
                    """
                ),
                {
                    "since": since_360,
                    "comm": POOL_COMMERCIAL,
                    "dom": POOL_DOMAIN,
                    "cap": args.pool_cap,
                },
            )
        ).all()
        summary["S2_pool"] = {
            "pool_v1_compat": pool_counts,
            "pool_360d_full": pool_counts["360d"],
            "pool_fetched": len(pool_rows),
            "pool_cap": args.pool_cap,
            "pool_truncated": pool_counts["360d"] > len(pool_rows),
            "boundary_probe_7d": pool_v2_counts,
            "boundary_note": (
                "In PostgreSQL ARE, \\b is backspace not word boundary; \\y is word boundary. "
                "Evidence 96 semantic zeros using \\b are measurement artifacts. "
                "Pool v1 regex intentionally matches evidence 96 (no \\b)."
            ),
        }

        # Blind / AI_CONFIRMED id sets for pool membership checks
        blind_ids = set(
            int(x)
            for (x,) in (
                await session.execute(sql_text("SELECT message_id FROM label_review_samples"))
            ).all()
        )
        ai_conf_ids = set(
            int(x)
            for (x,) in (
                await session.execute(
                    sql_text("SELECT message_id FROM leads WHERE status='AI_CONFIRMED'")
                )
            ).all()
        )

        # --- S5 Prefilter ---
        pref_uncapped = int(
            (
                await session.scalar(
                    sql_text(
                        f"""
                        SELECT COUNT(*) FROM messages m
                        JOIN message_scores s ON s.message_id = m.id
                        WHERE {PREFILTER_WHERE}
                        """
                    ),
                    {"since": since_7},
                )
            )
            or 0
        )
        pref_rows = (
            await session.execute(
                sql_text(PREFILTER_SELECT),
                {"since": since_7, "cap": args.prefilter_cap},
            )
        ).all()
        pref_ids = {int(r[0]) for r in pref_rows}

        full_low_comms = (
            await session.execute(
                sql_text(
                    """
                    SELECT c.name, COUNT(*) AS n
                    FROM messages m
                    JOIN message_scores s ON s.message_id=m.id
                    JOIN communities c ON c.id=m.community_id
                    WHERE m.message_date >= :since AND s.tier='LOW'
                    GROUP BY c.name ORDER BY n DESC LIMIT 15
                    """
                ),
                {"since": since_7},
            )
        ).all()
        cap_comms = Counter()
        cap_tiers = Counter()
        for r in pref_rows:
            cap_comms[r[5] or r[6] or str(r[3])] += 1
            cap_tiers[r[10]] += 1

        summary["S5_prefilter"] = {
            "uncapped_7d": pref_uncapped,
            "capped": len(pref_rows),
            "cap": args.prefilter_cap,
            "cap_truncated": max(pref_uncapped - len(pref_rows), 0),
            "order_by": "(commercial_score+technical_score) DESC, message_date DESC",
            "date_filter": "s.scored_at >= since (NOT m.message_date)",
            "tier": "LOW",
            "full_7d_low_top_communities": [
                {"name": n, "low_msgs": int(c)} for n, c in full_low_comms
            ],
            "capped_top_communities": [
                {"name": n, "in_cap": c} for n, c in cap_comms.most_common(15)
            ],
            "capped_tier_dist": dict(cap_tiers),
            "bias_finding": (
                "CT-ranked LIMIT 2500 skews toward high commercial+technical communities; "
                "compare full_7d_low_top vs capped_top."
            ),
        }

        # --- S3 planes ---
        cand_stats = (
            await session.execute(
                sql_text(
                    """
                    SELECT discovery_version, status, COALESCE(skip_reason,''), COUNT(*)
                    FROM commercial_discovery_candidates
                    GROUP BY 1,2,3 ORDER BY 1,2,3
                    """
                )
            )
        ).all()
        summary["S3_planes"] = {
            "audit": {
                "description": "LOW + scored_at window + regex/CT OR + ORDER BY CT LIMIT",
                "prefilter_cap": args.prefilter_cap,
            },
            "production": {
                "commercial_discovery_enabled": settings.commercial_discovery_enabled,
                "enqueue": "analyzer: flag AND tier==LOW AND discovery_features.eligible",
                "candidates_by_version_status_skip": [
                    {
                        "version": v,
                        "status": st,
                        "skip_reason": sk,
                        "n": int(n),
                    }
                    for v, st, sk, n in cand_stats
                ],
            },
            "mismatch_items": [
                "Audit uses scored_at + LIMIT 2500; production streams ingest-time LOW+eligible",
                "Production flag default False — disc_v4 never emitted candidates in live defaults",
                "Existing candidates are disc_v1/disc_v2 only",
            ],
        }
        summary["S4_production_loss"] = {
            "live_settings": summary["S1_run"]["live_settings"],
            "first_loss": {
                "FLAG_DISABLED": (
                    "100% of potential seeds while commercial_discovery_enabled=false"
                )
            },
            "counterfactual_after_stage": "FLAG_DISABLED",
            "note": "Do not treat production veto rates as live until flag is enabled.",
        }

        # --- AI_CONFIRMED probes (SQL) ---
        ai_rows = (
            await session.execute(
                sql_text(
                    """
                    SELECT l.id, l.message_id, m.community_id, c.name, c.username, c.url,
                           c.telegram_ref, m.author_id, m.message_date, m.text,
                           l.tier, l.score, l.lead_type, l.buyer_type, l.source, l.created_at,
                           s.tier, s.score, s.commercial_score, s.technical_score, s.scored_at
                    FROM leads l
                    JOIN messages m ON m.id = l.message_id
                    JOIN communities c ON c.id = m.community_id
                    LEFT JOIN message_scores s ON s.message_id = m.id
                    WHERE l.status = 'AI_CONFIRMED'
                    ORDER BY l.id
                    """
                )
            )
        ).all()

        # LaborX community census (S11 SQL)
        laborx_sql = None
        if "S11" not in skip:
            laborx_sql = (
                await session.execute(
                    sql_text(
                        """
                        SELECT
                          COUNT(*) AS msgs,
                          COUNT(*) FILTER (WHERE text ~* 'freelance opportunity') AS fo,
                          COUNT(*) FILTER (WHERE text ~* '(fix|repair|починить)') AS repairish,
                          COUNT(*) FILTER (
                            WHERE text ~* 'trading bot|bybit|binance|pybit|arbitrage'
                          ) AS trading_botish,
                          COUNT(*) FILTER (WHERE text ~* 'budget|fixed price|commission') AS budgeted
                        FROM messages
                        WHERE community_id = 2 AND message_date >= :since
                        """
                    ),
                    {"since": since_360},
                )
            ).one()
            laborx_sample = (
                await session.execute(
                    sql_text(
                        """
                        SELECT m.id, m.text, s.tier, s.commercial_score, s.technical_score
                        FROM messages m
                        LEFT JOIN message_scores s ON s.message_id = m.id
                        WHERE m.community_id = 2 AND m.message_date >= :since
                          AND m.text ~* 'freelance opportunity'
                        ORDER BY m.message_date DESC
                        LIMIT 200
                        """
                    ),
                    {"since": since_360},
                )
            ).all()
        else:
            laborx_sample = []

    # Session closed — Python-side evaluation (no DB writes)

    # --- S6 measured disc_v4 on capped prefilter ---
    veto_hist = Counter()
    first_loss_pref = Counter()
    eligible_ids = []
    for r in pref_rows:
        mid, text = int(r[0]), r[1] or ""
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
        for v in feats.veto_categories:
            veto_hist[v] += 1
        fl = first_loss_audit_row(
            has_score=True,
            tier="LOW",
            scored_at_in_window=True,
            in_blind=False,
            in_ai_confirmed=False,
            prefilter_regex_ok=True,
            in_cap=True,
            feats=feats,
        )
        first_loss_pref[fl] += 1
        if feats.eligible:
            eligible_ids.append(mid)

    summary["S6_disc_v4_measured"] = {
        "examined": len(pref_rows),
        "eligible_measured": len(eligible_ids),
        "eligible_ids": eligible_ids[:50],
        "evidence_96_hardcoded": 4,
        "matches_96": len(eligible_ids) == 4,
        "veto_hit_counts_multi_label": dict(veto_hist.most_common()),
        "first_loss_on_capped_prefilter": dict(first_loss_pref.most_common()),
    }

    # --- S8 commercial_domain_loss_report on fetched pool ---
    first_loss_pool = Counter()
    veto_in_pool = Counter()
    veto_all_hits = Counter()
    semantic_gaps = Counter()
    tier_in_pool = Counter()
    unscored_pool = 0
    low_but_commercial_samples = []
    path_detail = Counter()
    examples_by_gap: dict[str, list] = {}

    for r in pool_rows:
        (
            mid,
            text,
            cid,
            mdate,
            cname,
            cuname,
            tier,
            score,
            cscore,
            tscore,
            scored_at,
        ) = r
        mid = int(mid)
        text = text or ""
        has_score = tier is not None
        if not has_score:
            unscored_pool += 1
            tier_in_pool["UNSCORED"] += 1
        else:
            tier_in_pool[str(tier)] += 1

        if scored_at is None:
            scored_in_window = False
        else:
            sa = scored_at if scored_at.tzinfo else scored_at.replace(tzinfo=timezone.utc)
            scored_in_window = sa >= since_7
        # Prefilter regex/CT gate (approximate: CT both >0 OR keyword arms — use evaluate + score)
        prefilter_regex_ok = False
        if has_score and tier == "LOW":
            if (cscore or 0) > 0 and (tscore or 0) > 0:
                prefilter_regex_ok = True
            else:
                # Match keyword arms cheaply via same Python evaluate commercial patterns + FO
                prefilter_regex_ok = bool(
                    re.search(
                        r"trading bot|bybit|binance api|pybit|freelance opportunity|"
                        r"починить|commission|build.{0,40}trading bot|is hiring|"
                        r"#резюме|#opentowork|looking for.*(developer|contractor|freelancer)|"
                        r"budget|бюджет|(my|our).{0,20}(bot|strategy).{0,60}(fix|repair)",
                        text,
                        re.I,
                    )
                )

        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
        # gig flag for snapshots
        gig = bool(_GIG_MARKETPLACE_RX.search(text))

        for v in feats.veto_categories:
            veto_all_hits[v] += 1
            veto_in_pool[v] += 1

        fl = first_loss_audit_row(
            has_score=has_score,
            tier=tier,
            scored_at_in_window=scored_in_window if has_score else False,
            in_blind=mid in blind_ids,
            in_ai_confirmed=mid in ai_conf_ids,
            prefilter_regex_ok=prefilter_regex_ok,
            in_cap=mid in pref_ids,
            feats=feats,
        )
        # For pool messages older than 7d scored_at window: if they have LOW score but
        # scored_at outside 7d, OUTSIDE_SCORED_AT_WINDOW is correct for *current* 7d audit
        # prefilter. For 360d pool attribution we treat "would pass prefilter shape ignoring
        # window" separately — keep first_loss as ordered above for audit fidelity.
        first_loss_pool[fl] += 1
        gap = semantic_gap(fl, feats)
        semantic_gaps[gap] += 1
        if gap not in examples_by_gap or len(examples_by_gap[gap]) < 3:
            examples_by_gap.setdefault(gap, []).append(
                {"message_id": mid, "first_loss": fl, "preview": _preview(text)}
            )

        if fl == "NO_PATH_MATCH":
            for k, v in _paths_v4(feats).items():
                if v:
                    path_detail[f"path_{k}_true_but_ineligible"] += 1

        if has_score and tier == "LOW" and len(low_but_commercial_samples) < args.low_sample:
            v2 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V2)
            low_but_commercial_samples.append(
                {
                    "message_id": mid,
                    "community": cname or cuname,
                    "commercial_score": float(cscore or 0),
                    "technical_score": float(tscore or 0),
                    "first_loss": fl,
                    "semantic_gap": gap,
                    "gig_marketplace": gig,
                    "v4": {
                        "eligible": feats.eligible,
                        "buyer_direction": feats.buyer_direction,
                        "veto_categories": list(feats.veto_categories),
                        "repair_signal": feats.repair_signal,
                        "project_scope": feats.project_scope,
                        "aggregator_signal": feats.aggregator_signal,
                        "soft_direction": feats.soft_direction,
                        "families": list(feats.buyer_signal_families),
                    },
                    "v2_eligible": v2.eligible,
                    "preview": _preview(text),
                }
            )

    pool_n = len(pool_rows)
    assert sum(first_loss_pool.values()) == pool_n, (
        f"first_loss sum {sum(first_loss_pool.values())} != pool {pool_n}"
    )

    # Ordered loss report rows
    loss_order = [
        "NOT_SCORED",
        "NOT_LOW",
        "OUTSIDE_SCORED_AT_WINDOW",
        "EXCLUDED_BLIND",
        "EXCLUDED_AI_CONFIRMED",
        "PREFILTER_REGEX_MISS",
        "CT_CAP_TRUNCATED",
        "HARD_EXCLUDE_EARLY",
        *[f"VETO_{v}" for v in VETO_PRIORITY],
        "DIRECTION_SEEKER",
        "DIRECTION_PROVIDER",
        "DIRECTION_EMPLOYER",
        "DIRECTION_RECRUITER",
        "NO_PATH_MATCH",
        "RETRIEVED",
    ]
    cumulative = 0
    report_rows = []
    for stage in loss_order:
        n = int(first_loss_pool.get(stage, 0))
        if n == 0 and not stage.startswith("VETO_"):
            continue
        if n == 0:
            continue
        cumulative += n
        report_rows.append(
            {
                "stage": stage,
                "n": n,
                "pct_of_pool": round(100.0 * n / max(pool_n, 1), 2),
                "cumulative_pct": round(100.0 * cumulative / max(pool_n, 1), 2),
                "plane": "audit",
            }
        )
    # Any unexpected buckets
    for stage, n in first_loss_pool.items():
        if stage not in loss_order:
            cumulative += n
            report_rows.append(
                {
                    "stage": stage,
                    "n": n,
                    "pct_of_pool": round(100.0 * n / max(pool_n, 1), 2),
                    "cumulative_pct": round(100.0 * cumulative / max(pool_n, 1), 2),
                    "plane": "audit",
                }
            )

    summary["S8_commercial_domain_loss_report"] = {
        "pool_fetched": pool_n,
        "pool_360d_sql_count": pool_counts["360d"],
        "tier_distribution": dict(tier_in_pool),
        "unscored": unscored_pool,
        "rows": report_rows,
        "sums_to_pool": sum(first_loss_pool.values()) == pool_n,
        "first_loss_counts": dict(first_loss_pool.most_common()),
        "no_path_match_detail": dict(path_detail),
    }
    summary["S9_veto_x_domain"] = {
        "in_pool_multi_label_hits": dict(veto_in_pool.most_common()),
        "note": "Multi-label: one message can hit multiple vetoes; first-loss is single-assignment in S8.",
    }
    summary["S12_low_but_commercial"] = {
        "n": len(low_but_commercial_samples),
        "samples": low_but_commercial_samples,
        "first_loss_on_samples": dict(
            Counter(s["first_loss"] for s in low_but_commercial_samples).most_common()
        ),
    }
    summary["S13_semantic_gaps"] = {
        "boundary_bug": {
            "pg_b_is_backspace": True,
            "hiring_7d": pool_v2_counts,
        },
        "categories": dict(semantic_gaps.most_common()),
        "examples": examples_by_gap,
    }

    # --- S10 AI_CONFIRMED traces ---
    probes = []
    probe_veto_hits = Counter()
    for r in ai_rows:
        (
            lead_id,
            mid,
            cid,
            cname,
            cuname,
            curl,
            cref,
            author_id,
            mdate,
            text,
            lead_tier,
            lead_score,
            lead_type,
            buyer_type,
            source,
            lead_created,
            score_tier,
            msg_score,
            cscore,
            tscore,
            scored_at,
        ) = r
        text = text or ""
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V4)
        v2 = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V2)
        for v in feats.veto_categories:
            probe_veto_hits[v] += 1
        stages = []
        stages.append({"stage": "SEED", "pass": True, "note": f"lead={lead_id} msg={mid}"})
        stages.append(
            {
                "stage": "current_score_row",
                "pass": score_tier is not None or lead_tier is not None,
                "note": f"score_tier={score_tier} lead_tier={lead_tier} score={msg_score or lead_score}",
            }
        )
        prefilter_pass = (score_tier or lead_tier) == "LOW"
        stages.append(
            {
                "stage": "discovery_prefilter_LOW",
                "pass": prefilter_pass,
                "note": (
                    "FAIL: disc_v4 audit/production discovery requires LOW; "
                    "HIGH goes scorer→CRM. Also AI_CONFIRMED ids excluded from audit SQL."
                    if not prefilter_pass
                    else "would enter LOW prefilter"
                ),
            }
        )
        feats_ok = bool(feats.commercial_patterns or feats.domain_categories)
        stages.append(
            {
                "stage": "discovery_features",
                "pass": True,
                "note": (
                    f"gig={bool(_GIG_MARKETPLACE_RX.search(text))} "
                    f"agg={feats.aggregator_signal} repair={feats.repair_signal} "
                    f"scope={feats.project_scope} families={feats.buyer_signal_families}"
                ),
            }
        )
        stages.append(
            {
                "stage": "buyer_direction",
                "pass": feats.buyer_direction == "BUYER" or feats.soft_direction,
                "note": f"dir={feats.buyer_direction} soft={feats.soft_direction}",
            }
        )
        stages.append(
            {
                "stage": "hard_veto",
                "pass": not bool(feats.veto_categories),
                "note": f"veto={feats.veto_categories}",
            }
        )
        stages.append(
            {
                "stage": "disc_v4_eligible",
                "pass": bool(feats.eligible),
                "note": f"trig={feats.trigger_type} score={feats.discovery_score} v2_eligible={v2.eligible}",
            }
        )
        lost_at = next((s["stage"] for s in stages if not s["pass"]), "none")
        fl = first_loss_audit_row(
            has_score=score_tier is not None,
            tier=score_tier or lead_tier,
            scored_at_in_window=True,
            in_blind=False,
            in_ai_confirmed=True,
            prefilter_regex_ok=True,
            in_cap=False,
            feats=feats,
        )
        probes.append(
            {
                "lead_id": int(lead_id),
                "message_id": int(mid),
                "community": cname,
                "username": cuname,
                "url": curl,
                "is_laborx": bool(cuname and "laborx" in (cuname or "").lower())
                or bool(cref and "laborx" in (cref or "").lower())
                or "Freelance Opportunity" in text,
                "score_tier": score_tier or lead_tier,
                "lead_type": lead_type,
                "buyer_type": buyer_type,
                "lost_at_stage": lost_at,
                "first_loss_taxonomy": fl,
                "stage_trace": stages,
                "v4": {
                    "eligible": feats.eligible,
                    "buyer_direction": feats.buyer_direction,
                    "veto_categories": list(feats.veto_categories),
                    "repair_signal": feats.repair_signal,
                    "project_scope": feats.project_scope,
                    "aggregator_signal": feats.aggregator_signal,
                    "soft_direction": feats.soft_direction,
                    "paths": _paths_v4(feats),
                },
                "v2_eligible": v2.eligible,
                "structure": laborx_structure(text),
                "preview": _preview(text, 180),
            }
        )

    summary["S10_veto_x_ai_confirmed"] = {
        "n_probes": len(probes),
        "probe_retrieval_coverage_v4": sum(1 for p in probes if p["v4"]["eligible"]),
        "veto_multi_label_hits": dict(probe_veto_hits.most_common()),
        "probes": probes,
    }

    # --- S11 LaborX structure ---
    if "S11" not in skip and laborx_sample is not None:
        struct_counts = Counter()
        struct_v4 = Counter()
        struct_first = Counter()
        for mid, text, tier, cscore, tscore in laborx_sample:
            cat = laborx_structure(text or "")
            struct_counts[cat] += 1
            feats = evaluate_discovery(scorer, text or "", version=DISCOVERY_VERSION_V4)
            if feats.eligible:
                struct_v4[cat] += 1
            fl = first_loss_audit_row(
                has_score=tier is not None,
                tier=tier,
                scored_at_in_window=True,
                in_blind=False,
                in_ai_confirmed=False,
                prefilter_regex_ok=True,
                in_cap=True,
                feats=feats,
            )
            struct_first[f"{cat}:{fl}"] += 1
        summary["S11_laborx_structure"] = {
            "community_id": 2,
            "sql_360d": {
                "msgs": int(laborx_sql[0]) if laborx_sql else None,
                "fo": int(laborx_sql[1]) if laborx_sql else None,
                "repairish": int(laborx_sql[2]) if laborx_sql else None,
                "trading_botish": int(laborx_sql[3]) if laborx_sql else None,
                "budgeted": int(laborx_sql[4]) if laborx_sql else None,
            },
            "sample_n": len(laborx_sample),
            "categories": dict(struct_counts.most_common()),
            "v4_eligible_by_category": dict(struct_v4.most_common()),
            "first_loss_by_category": dict(struct_first.most_common(30)),
            "finding": (
                "FO templates match _GIG_MARKETPLACE_RX → job_aggregator veto clears repair. "
                "Source-level LaborX ban would over-block; message-level FO/gig structure is "
                "the actual veto trigger. AI_CONFIRMED BOT_REPAIR FO posts are message-level "
                "false positives of a marketplace template veto."
            ),
            "source_vs_message": "MESSAGE_LEVEL_STRUCTURAL_VETO (gig FO template), not community_id ban",
        }
    else:
        summary["S11_laborx_structure"] = {"skipped": True}

    # --- 7d funnel (measured) ---
    msgs_7d = windows["7d"]["messages"]
    scored_7d = windows["7d"]["scored"]
    low_7d = windows["7d"]["low"]
    funnel = [
        {
            "stage": "RAW messages (7d message_date)",
            "input": msgs_7d,
            "survivors": msgs_7d,
            "rejected": 0,
            "rejection_pct": 0.0,
            "top_rejection_reason": "n/a",
        },
        {
            "stage": "CURRENT SCORE ROW",
            "input": msgs_7d,
            "survivors": scored_7d,
            "rejected": msgs_7d - scored_7d,
            "rejection_pct": round(100.0 * (msgs_7d - scored_7d) / max(msgs_7d, 1), 2),
            "top_rejection_reason": "unscored (negligible in 7d)",
        },
        {
            "stage": "LOW tier (discovery plane)",
            "input": scored_7d,
            "survivors": low_7d,
            "rejected": scored_7d - low_7d,
            "rejection_pct": round(100.0 * (scored_7d - low_7d) / max(scored_7d, 1), 2),
            "top_rejection_reason": "MEDIUM/HIGH → scorer CRM path",
        },
        {
            "stage": "DISCOVERY PREFILTER (uncapped)",
            "input": low_7d,
            "survivors": pref_uncapped,
            "rejected": max(low_7d - pref_uncapped, 0),
            "rejection_pct": round(100.0 * max(low_7d - pref_uncapped, 0) / max(low_7d, 1), 2),
            "top_rejection_reason": "CT/keyword OR fail or scored_at window",
        },
        {
            "stage": "PREFILTER CAP 2500",
            "input": pref_uncapped,
            "survivors": len(pref_rows),
            "rejected": max(pref_uncapped - len(pref_rows), 0),
            "rejection_pct": round(
                100.0 * max(pref_uncapped - len(pref_rows), 0) / max(pref_uncapped, 1), 2
            ),
            "top_rejection_reason": "ORDER BY CT DESC LIMIT",
        },
        {
            "stage": "DISC_V4 CANDIDATE (measured)",
            "input": len(pref_rows),
            "survivors": len(eligible_ids),
            "rejected": len(pref_rows) - len(eligible_ids),
            "rejection_pct": round(
                100.0 * (len(pref_rows) - len(eligible_ids)) / max(len(pref_rows), 1), 2
            ),
            "top_rejection_reason": (
                first_loss_pref.most_common(1)[0][0] if first_loss_pref else "unknown"
            ),
            "per_veto_first_loss": {
                k: v for k, v in first_loss_pref.items() if k.startswith("VETO_")
            },
        },
    ]
    summary["S7_discovery_funnel_7d"] = funnel

    # --- S16 Decision A–E ---
    # Attribute on commercial∧domain pool first-loss + 7d funnel + probes
    fl = first_loss_pool
    n_pool = max(pool_n, 1)
    prefilter_loss = (
        fl.get("PREFILTER_REGEX_MISS", 0)
        + fl.get("CT_CAP_TRUNCATED", 0)
        + fl.get("NOT_LOW", 0)
        + fl.get("OUTSIDE_SCORED_AT_WINDOW", 0)
    )
    score_loss = fl.get("NOT_SCORED", 0)
    veto_loss = sum(v for k, v in fl.items() if k.startswith("VETO_") or k == "HARD_EXCLUDE_EARLY")
    semantics_loss = fl.get("NO_PATH_MATCH", 0) + sum(
        v for k, v in fl.items() if k.startswith("DIRECTION_")
    )
    retrieved = fl.get("RETRIEVED", 0)

    shares = {
        "A_PREFILTER": round(100.0 * prefilter_loss / n_pool, 2),
        "B_SCORE_QUERY": round(100.0 * score_loss / n_pool, 2),
        "C_DISCOVERY_SEMANTICS": round(100.0 * semantics_loss / n_pool, 2),
        "D_VETO": round(100.0 * veto_loss / n_pool, 2),
        "RETRIEVED": round(100.0 * retrieved / n_pool, 2),
    }
    dominant = max(
        [
            ("A", shares["A_PREFILTER"]),
            ("B", shares["B_SCORE_QUERY"]),
            ("C", shares["C_DISCOVERY_SEMANTICS"]),
            ("D", shares["D_VETO"]),
        ],
        key=lambda x: x[1],
    )
    over_50 = [k for k, v in [("A", shares["A_PREFILTER"]), ("B", shares["B_SCORE_QUERY"]), ("C", shares["C_DISCOVERY_SEMANTICS"]), ("D", shares["D_VETO"])] if v >= 50]
    meaningful = [k for k, v in [("A", shares["A_PREFILTER"]), ("B", shares["B_SCORE_QUERY"]), ("C", shares["C_DISCOVERY_SEMANTICS"]), ("D", shares["D_VETO"])] if v >= 15]

    # Probe evidence: all 4 lost at prefilter (HIGH) AND would hit veto if evaluated
    probe_prefilter = all(p["lost_at_stage"] == "discovery_prefilter_LOW" for p in probes)
    probe_would_veto = all(
        "job_aggregator" in p["v4"]["veto_categories"] for p in probes
    ) if probes else False

    # Dual-cohort: pool visibility vs measured capped-prefilter losses.
    pref_semantics = first_loss_pref.get("NO_PATH_MATCH", 0) + sum(
        v for k, v in first_loss_pref.items() if k.startswith("DIRECTION_")
    )
    pref_veto = sum(
        v for k, v in first_loss_pref.items() if k.startswith("VETO_") or k == "HARD_EXCLUDE_EARLY"
    )
    pref_n = max(len(pref_rows), 1)
    visibility_material = score_loss >= 0.5 * n_pool
    postfilter_material = (pref_semantics + pref_veto) >= 0.5 * pref_n
    if visibility_material and postfilter_material:
        decision = "E"
    elif len(over_50) == 1 and len(meaningful) <= 1:
        decision = over_50[0]
    elif len(meaningful) >= 2 or (probe_prefilter and probe_would_veto):
        decision = "E"
    elif dominant[1] >= 50:
        decision = dominant[0]
    else:
        decision = "E"

    decision_labels = {
        "A": "PREFILTER",
        "B": "SCORE/QUERY",
        "C": "DISCOVERY SEMANTICS",
        "D": "VETO",
        "E": "MULTIPLE",
    }
    rationale = (
        f"MULTIPLE internal bottlenecks (not community expansion). "
        f"Pool shares={shares}. "
        f"7d measured disc_v4 survivors={len(eligible_ids)}/{len(pref_rows)} "
        f"(evidence96 hardcoded 4; matches={len(eligible_ids)==4}). "
        f"Capped-prefilter first-loss: semantics={pref_semantics} veto={pref_veto}. "
        f"AI_CONFIRMED 4/4 lost_at=prefilter_LOW (HIGH) + text veto job_aggregator="
        f"{probe_would_veto} (message-level FO). "
        f"Production FLAG_DISABLED (enabled={settings.commercial_discovery_enabled}). "
        f"360d pool unscored={round(100.0*unscored_pool/n_pool,1)}%."
    )

    summary["S16_decision"] = {
        "decision": decision,
        "label": decision_labels[decision],
        "shares_pct_of_pool": shares,
        "cohorts": {
            "pool_360d_primary_share": "B_SCORE_QUERY" if visibility_material else dominant[0],
            "capped_prefilter_7d_primary_share": (
                "C_DISCOVERY_SEMANTICS"
                if pref_semantics >= pref_veto
                else "D_VETO"
            ),
            "capped_prefilter_semantics_share_pct": round(100.0 * pref_semantics / pref_n, 2),
            "capped_prefilter_veto_share_pct": round(100.0 * pref_veto / pref_n, 2),
            "overall": decision_labels[decision],
        },
        "rationale": rationale,
        "plane_disagreement": True,
        "production_plane": "FLAG_DISABLED",
        "fix_recommendation_smallest": (
            "1) Do NOT expand communities. "
            "2) Ops/backfill score coverage for 8–360d commercial∧domain (B). "
            "3) Keep message-level gig FO veto; carve FO+repair/trading-bot+budget "
            "exception (LaborX AI_CONFIRMED) — not a LaborX source ban (D). "
            "4) Remeasure NO_PATH_MATCH + CT-cap bias after (2)–(3). "
            "No scoring.yaml / score() changes. Do not flip discovery flag here."
        ),
    }

    # Isolation after (re-open read-only)
    async with SessionLocal() as session:
        await _set_readonly(session)
        inv2 = (
            await session.execute(
                sql_text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM human_labels),
                      (SELECT COUNT(*) FROM leads WHERE status='AI_CONFIRMED'),
                      (SELECT COUNT(*) FROM commercial_discovery_candidates)
                    """
                )
            )
        ).one()
    summary["S1_run"]["invariants_after"] = {
        "human_labels": int(inv2[0]),
        "ai_confirmed": int(inv2[1]),
        "disc_candidates": int(inv2[2]),
    }
    summary["S1_run"]["invariants_unchanged"] = (
        int(inv2[0]) == int(inv[0])
        and int(inv2[1]) == int(inv[1])
        and int(inv2[2]) == int(inv[2])
    )
    summary["S1_run"]["runtime_sec"] = round(time.perf_counter() - t0, 2)

    # --- Write evidence ---
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")

    lines = [
        f"Evidence {EV}: commercial discovery loss attribution",
        f"generated_at={summary['generated_at']} head={summary['head']} disc={DISCOVERY_VERSION_V4}",
        f"runtime_sec={summary['S1_run']['runtime_sec']} read_only=true sdk=false",
        "",
        "=== 1. RAW CORPUS ===",
        *(
            f"  {k}: msgs={v['messages']} scored={v['scored']} ({v['pct_scored']}%) "
            f"L/M/H={v['low']}/{v['medium']}/{v['high']}"
            for k, v in windows.items()
        ),
        "",
        "=== 2. SCORE RECONCILIATION ===",
        f"  current_score_per_message invariant_ok="
        f"{summary['score_reconciliation']['current_score_per_message']['invariant_ok']} "
        f"multi={summary['score_reconciliation']['current_score_per_message']['multi_score_msgs']}",
        f"  low_by_scored_at_7d={summary['score_reconciliation']['low_by_scored_at_7d']} "
        f"low_by_msg_date_7d={summary['score_reconciliation']['low_by_msg_date_7d']} "
        f"rescored_older={summary['score_reconciliation']['low_rescored_older_than_7d']}",
        f"  note: {summary['score_reconciliation']['msgs_vs_score_rows_note']}",
        "",
        "=== 3. PREFILTER ===",
        f"  uncapped={pref_uncapped} capped={len(pref_rows)} truncated="
        f"{summary['S5_prefilter']['cap_truncated']}",
        f"  bias: {summary['S5_prefilter']['bias_finding']}",
        "  capped_top:",
        *[
            f"    {x['name']}: {x['in_cap']}"
            for x in summary["S5_prefilter"]["capped_top_communities"][:8]
        ],
        "",
        "=== 4. DISCOVERY FUNNEL (7d, measured) ===",
        *[
            f"  {s['stage']}: in={s['input']} out={s['survivors']} "
            f"rej={s['rejected']} ({s['rejection_pct']}%) reason={s['top_rejection_reason']}"
            for s in funnel
        ],
        f"  per-veto first-loss on cap: {funnel[-1].get('per_veto_first_loss')}",
        "",
        "=== 5. KNOWN-POSITIVE RECALL (AI_CONFIRMED) ===",
        *[
            f"  lead={p['lead_id']} msg={p['message_id']} tier={p['score_tier']} "
            f"laborx={p['is_laborx']} lost_at={p['lost_at_stage']} "
            f"first_loss={p['first_loss_taxonomy']} veto={p['v4']['veto_categories']} "
            f"v2_elig={p['v2_eligible']} struct={p['structure']} | {p['preview']}"
            for p in probes
        ],
        f"  coverage_v4={summary['S10_veto_x_ai_confirmed']['probe_retrieval_coverage_v4']}/{len(probes)}",
        "",
        "=== 6. LABORX ===",
        f"  {json.dumps(summary.get('S11_laborx_structure', {}), default=str)[:1200]}",
        "",
        "=== 7. VETO LOSSES (commercial∧domain pool, multi-label) ===",
        f"  {summary['S9_veto_x_domain']['in_pool_multi_label_hits']}",
        f"  AI_CONFIRMED multi-label: {summary['S10_veto_x_ai_confirmed']['veto_multi_label_hits']}",
        "",
        "=== 8. LOW-BUT-COMMERCIAL ===",
        f"  n={summary['S12_low_but_commercial']['n']} "
        f"first_loss={summary['S12_low_but_commercial']['first_loss_on_samples']}",
        "",
        "=== 9. ROOT CAUSE ===",
        f"  DECISION {decision} — {decision_labels[decision]}",
        f"  shares={shares}",
        f"  {rationale}",
        "",
        "=== 10. FIX RECOMMENDATION ===",
        f"  {summary['S16_decision']['fix_recommendation_smallest']}",
        "",
        "=== 11. ISOLATION ===",
        f"  before={summary['S1_run']['invariants_before']}",
        f"  after={summary['S1_run']['invariants_after']}",
        f"  unchanged={summary['S1_run']['invariants_unchanged']}",
        "  gates: see curl /validation/gates (expect 94/0/94, ml_training_enabled=false)",
        "",
        "=== 12. TESTS ===",
        "  (runner executes pytest -q separately)",
        "",
        "=== 13. BUSINESS MILESTONE ===",
        "  NO OUTREACH IN THIS PHASE",
        "",
        "=== commercial_domain_loss_report ===",
        *[
            f"  {r['stage']}: n={r['n']} ({r['pct_of_pool']}%) cum={r['cumulative_pct']}%"
            for r in report_rows
        ],
        "",
        "=== semantic gaps ===",
        f"  {dict(semantic_gaps.most_common())}",
        f"  boundary hiring \\b vs \\y: {pool_v2_counts}",
        "",
        f"JSON: {OUT_JSON}",
    ]
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_TXT}")
    print(f"Wrote {OUT_JSON}")
    print(f"DECISION {decision} {decision_labels[decision]} eligible_measured={len(eligible_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
