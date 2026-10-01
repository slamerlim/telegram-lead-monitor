#!/usr/bin/env python3
"""Evidence 135 — History-first label frame (append-only label_review_samples).

Draws a 360-day labeling frame and inserts it into label_review_samples only.
LeadScorer and evaluate_discovery annotations are written to the evidence
manifest, never into the queue row, message_scores, human_labels, or candidates.

Does not run rescore_slice.py, does not insert label_reviews, and does not
re-arm the OBSERVE watcher.

Run:
  docker compose run --rm --no-deps -e GIT_HEAD=$(git rev-parse --short HEAD) \\
    -v \"$PWD:/app\" -w /app analyzer \\
    sh -c 'PYTHONPATH=/app python scripts/build_history_label_frame_135.py'
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text as sql_text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.discovery import DISCOVERY_VERSION_V6, evaluate_discovery
from shared.db import SessionLocal
from shared.models import LabelReviewSample
from shared.settings import get_settings

from scripts.audit_disc_v6_refined_population_cycle2 import first_loss  # noqa: E402

EV = "135"
BATCH_CORE = "hist360_p1_core_2026-09-30"
BATCH_OVERLAP = "hist360_p1_overlap_2026-09-30"
SEED = "hist360_p1_2026-09-30"
EXPECT_HL = 1524
EXPECT_AC = 4
WINDOW_DAYS = 360
MIN_TEXT = 20
PROBE_COMMUNITY_CAP = 40

# Keep in sync with scripts/report_history_label_readout_136.py
MIN_RANDOM_REVIEWED = 1200
MIN_PROBE_REVIEWED = 600
DEMAND_PREVALENCE_LOWER = 0.005

CORE_STRATA = (
    ("hist_random", 1200),
    ("hist_recall_probe", 600),
    ("hist_community_spread", 600),
    ("hist_negative_control", 200),
)
OVERLAP_N = 150

FREEZE_FILES = (
    ROOT / "docs/audit/evidence/118-offline-freeze-samples.json",
    ROOT / "docs/audit/evidence/118b-offline-freeze-samples.json",
    ROOT / "docs/audit/evidence/118c-offline-freeze-samples.json",
)
FREEZE_KEYS = (
    "nopath_near_miss",
    "veto_near_or_refined",
    "eligible_non_low",
    "eligible_retrieved",
)

# Postgres ARE. Same buyer phrases as TRUE_BUYER_NEAR_MISS_RX (audit cycle 6).
BUYER_SQL_RX = (
    "(hire[[:space:]]+(an?[[:space:]]+)?(developer|engineer|freelancer|contractor)"
    "|looking[[:space:]]+for[[:space:]]+(a[[:space:]]+)?(developer|engineer|freelancer|contractor)"
    "|need[[:space:]]+(a[[:space:]]+)?(developer|engineer|freelancer|contractor|dev)\\M"
    "|freelance[[:space:]]+opportunity"
    "|(build|fix|repair|develop).{0,40}(trading[[:space:]]+)?bot"
    "|budget[[:space:]]*[:=]?[[:space:]]*\\$[[:space:]]*[0-9]"
    "|нужен[[:space:]]+разработчик|починить)"
)
CONTROL_SQL_RX = (
    "(pleased[[:space:]]+to[[:space:]]+announce|new[[:space:]]+feature|english-only"
    "|take[[:space:]]+profit|stop[[:space:]]+loss|we[[:space:]]+are[[:space:]]+hiring"
    "|full-time|part-time|apply[[:space:]]+now|[[:<:]]gm[[:>:]]|сигнал)"
)

OUT_TXT = ROOT / "docs/audit/evidence/135-history-label-frame.txt"
OUT_JSON = ROOT / "docs/audit/evidence/135-history-label-frame.json"
OUT_SAMPLES = ROOT / "docs/audit/evidence/135-history-label-frame-samples.json"

DECISION_RULE = {
    "reviewers": ["human1", "human2"],
    "batches": [BATCH_CORE, BATCH_OVERLAP],
    "genuine_positive": (
        "HUMAN_REVIEWED_TRUE AND commercially_actionable is true, "
        "unanimous among human1/human2 rows present on that message"
    ),
    "prevalence_denominator": "hist_random messages with at least one human1/human2 review",
    "prevalence_interval": "Clopper-Pearson 95% two-sided",
    "probe_yield": "genuine positives / reviewed hist_recall_probe",
    "kappa": "Cohen kappa on paired human1/human2 labels",
    "blocked_unless": {
        "hist_random_reviewed_n>=": MIN_RANDOM_REVIEWED,
        "hist_recall_probe_reviewed_n>=": MIN_PROBE_REVIEWED,
    },
    "codes": {
        "HISTORY_SOURCE_ABSENT": (
            "both denominators meet the minimums AND genuine positives are 0 "
            "on hist_random AND on hist_recall_probe"
        ),
        "HISTORY_HAS_DEMAND": (
            f"denominators meet the minimums AND hist_random Clopper-Pearson "
            f"lower bound >= {DEMAND_PREVALENCE_LOWER}"
        ),
        "HISTORY_DEMAND_SCARCE": (
            "denominators meet the minimums AND the row is neither SOURCE_ABSENT "
            "nor HAS_DEMAND"
        ),
    },
    "zero_reviews": (
        "BLOCKED_PENDING_HUMAN_REVIEW. Do not emit HISTORY_SOURCE_ABSENT or "
        "HISTORY_HAS_DEMAND from an empty review set."
    ),
    "ml_go_claim": False,
    "not_training_authorization": True,
}

METHODOLOGY = (
    "History-first frame hist360 p1 (360d, length(text)>=20). "
    "hist_random is not conditioned on message_scores or disc_v6. "
    "hist_recall_probe matches buyer phrasing with a cap of 40 messages per community. "
    "hist_community_spread is round-robin across communities. "
    "hist_negative_control excludes buyer phrasing. "
    "hist_overlap is a separate seeded draw from the same window for calibration. "
    "Scorer and discovery annotations are evidence-only and are not stored on this row. "
    "Humans label via /review as human1/human2. This row does not write human_labels."
)

ISO_SQL = """
SELECT
  (SELECT COUNT(*) FROM human_labels) AS human_labels,
  (SELECT COUNT(*) FROM leads WHERE status = 'AI_CONFIRMED') AS ai_confirmed,
  (SELECT COUNT(*) FROM message_scores) AS message_scores,
  (SELECT COUNT(*) FROM messages) AS messages,
  (SELECT COUNT(*) FROM commercial_discovery_candidates) AS candidates,
  (SELECT COUNT(*) FROM label_reviews) AS label_reviews
"""

EXCLUSION_SQL = """
SELECT message_id FROM label_reviews
UNION
SELECT message_id FROM ai_validation_attempts
UNION
SELECT message_id FROM validation_consensus WHERE synthetic IS FALSE
"""


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


def _preview(text: str, n: int = 140) -> str:
    return " ".join((text or "").split())[:n]


def _freeze_ids() -> list[int]:
    found: list[int] = []
    for path in FREEZE_FILES:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for key in FREEZE_KEYS:
            for row in doc.get(key) or []:
                if isinstance(row, dict) and "message_id" in row:
                    found.append(int(row["message_id"]))
    unique = sorted(set(found))
    if len(unique) != 40:
        raise SystemExit(f"expected 40 WP2 freeze message ids, found {len(unique)}")
    return unique


def _sql_ints(ids: set[int]) -> str:
    if not ids:
        return ""
    return " AND m.id NOT IN (" + ",".join(str(int(i)) for i in sorted(ids)) + ")"


async def _ids(session, sql: str, params: dict[str, Any]) -> list[int]:
    rows = (await session.execute(sql_text(sql), params)).all()
    return [int(r[0]) for r in rows]


async def _hash_draw(
    session,
    *,
    lo: datetime,
    hi: datetime,
    seed: str,
    limit: int,
    exclude: set[int],
    extra_sql: str = "",
    extra_params: dict[str, Any] | None = None,
) -> list[int]:
    modulus = 10007
    last: list[int] = []
    params_extra = extra_params or {}
    # Hash bucket is materialized first so optional regex filters do not run on the full window.
    cuts = (2, 5, 12, 40, 120, 400, 1500, modulus) if not extra_sql else (120, 400, 1500, modulus)
    for cut in cuts:
        filt = ""
        if cut < modulus:
            filt = (
                " AND abs(mod(hashtext(m.id::text || :seed)::bigint, :modulus)) < :cut"
            )
        sql = f"""
            WITH bucket AS MATERIALIZED (
              SELECT m.id, m.text
              FROM messages m
              WHERE m.message_date >= :lo AND m.message_date < :hi
                AND m.text IS NOT NULL AND length(m.text) >= {MIN_TEXT}
                {_sql_ints(exclude)}
                {filt}
            )
            SELECT m.id
            FROM bucket m
            WHERE TRUE
              {extra_sql}
            ORDER BY abs(mod(hashtext(m.id::text || :seed)::bigint, :modulus)), m.id
            LIMIT :lim
        """
        last = await _ids(
            session,
            sql,
            {
                "lo": lo,
                "hi": hi,
                "seed": seed,
                "modulus": modulus,
                "cut": cut,
                "lim": limit,
                **params_extra,
            },
        )
        print(f"  hash draw seed={seed} cut={cut} got={len(last)} need={limit}", flush=True)
        if len(last) >= limit:
            return last[:limit]
    return last


async def _probe_draw(
    session,
    *,
    lo: datetime,
    hi: datetime,
    seed: str,
    limit: int,
    exclude: set[int],
) -> list[int]:
    sql = f"""
        SELECT ranked.id
        FROM (
          SELECT m.id, m.community_id,
                 row_number() OVER (
                   PARTITION BY m.community_id
                   ORDER BY abs(mod(hashtext(m.id::text || :seed)::bigint, 2147483629)), m.id
                 ) AS rn,
                 abs(mod(hashtext(m.id::text || :seed)::bigint, 2147483629)) AS ord_key
          FROM messages m
          WHERE m.message_date >= :lo AND m.message_date < :hi
            AND m.text IS NOT NULL AND length(m.text) >= {MIN_TEXT}
            {_sql_ints(exclude)}
            AND m.text ~* :buyer_rx
        ) ranked
        WHERE ranked.rn <= :cap
        ORDER BY ranked.rn, ranked.ord_key, ranked.id
        LIMIT :lim
    """
    return await _ids(
        session,
        sql,
        {
            "lo": lo,
            "hi": hi,
            "seed": seed,
            "buyer_rx": BUYER_SQL_RX,
            "cap": PROBE_COMMUNITY_CAP,
            "lim": limit,
        },
    )


async def _spread_draw(
    session,
    *,
    lo: datetime,
    hi: datetime,
    seed: str,
    limit: int,
    exclude: set[int],
) -> list[int]:
    last: list[int] = []
    for cap in (5, 8, 12, 20, 40, 80):
        sql = f"""
            SELECT ranked.id
            FROM (
              SELECT m.id,
                     row_number() OVER (
                       PARTITION BY m.community_id
                       ORDER BY abs(mod(hashtext(m.id::text || :seed)::bigint, 2147483629)), m.id
                     ) AS rn,
                     abs(mod(hashtext(m.id::text || :seed)::bigint, 2147483629)) AS ord_key
              FROM messages m
              WHERE m.message_date >= :lo AND m.message_date < :hi
                AND m.text IS NOT NULL AND length(m.text) >= {MIN_TEXT}
                {_sql_ints(exclude)}
            ) ranked
            WHERE ranked.rn <= :cap
            ORDER BY ranked.rn, ranked.ord_key, ranked.id
            LIMIT :lim
        """
        last = await _ids(
            session,
            sql,
            {"lo": lo, "hi": hi, "seed": seed, "cap": cap, "lim": limit},
        )
        print(f"  community spread cap={cap} got={len(last)} need={limit}", flush=True)
        if len(last) >= limit:
            return last[:limit]
    return last


async def _load_rows(session, ids: list[int]) -> dict[int, dict[str, Any]]:
    found: dict[int, dict[str, Any]] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i : i + 400]
        in_list = ",".join(str(int(x)) for x in chunk)
        sql = f"""
            SELECT m.id, m.text, m.community_id, m.message_date,
                   c.username AS community_username, c.name AS community_name
            FROM messages m
            LEFT JOIN communities c ON c.id = m.community_id
            WHERE m.id IN ({in_list})
        """
        rows = (await session.execute(sql_text(sql))).mappings().all()
        for row in rows:
            found[int(row["id"])] = dict(row)
    return found


def _annotate(scorer: LeadScorer, row: dict[str, Any]) -> dict[str, Any]:
    text = row.get("text") or ""
    username = row.get("community_username")
    name = row.get("community_name")
    try:
        scored = scorer.score(text, community_username=username, community_name=name)
        lead = {
            "score": float(getattr(scored, "score", 0.0) or 0.0),
            "tier": str(getattr(scored, "tier", "UNSCORED") or "UNSCORED").upper(),
            "lead_type": getattr(scored, "lead_type", None),
            "buyer_type": getattr(scored, "buyer_type", None),
        }
    except Exception as exc:  # noqa: BLE001 — manifest must record the failure, not persist it
        lead = {"score": None, "tier": "ERROR", "error": type(exc).__name__}
    try:
        feats = evaluate_discovery(scorer, text, version=DISCOVERY_VERSION_V6)
        disc = {
            "discovery_version": DISCOVERY_VERSION_V6,
            "eligible": bool(feats.eligible),
            "first_loss": first_loss(feats),
            "buyer_direction": feats.buyer_direction,
            "trigger_type": getattr(feats, "trigger_type", None),
        }
    except Exception as exc:  # noqa: BLE001
        disc = {"discovery_version": DISCOVERY_VERSION_V6, "error": type(exc).__name__}
    return {
        "lead_scorer": lead,
        "discovery": disc,
        "preview": _preview(text),
    }


async def _iso(session) -> dict[str, int]:
    row = (await session.execute(sql_text(ISO_SQL))).mappings().one()
    return {k: int(row[k]) for k in row}


def _require_isolation(iso: dict[str, int], label: str) -> None:
    if iso["human_labels"] != EXPECT_HL or iso["ai_confirmed"] != EXPECT_AC:
        raise SystemExit(
            f"isolation abort {label}: human_labels={iso['human_labels']} "
            f"AI_CONFIRMED={iso['ai_confirmed']}"
        )


async def _existing_counts(session) -> dict[str, dict[str, int]]:
    rows = (
        await session.execute(
            sql_text(
                """
                SELECT sample_batch_id, stratum, COUNT(*) AS n
                FROM label_review_samples
                WHERE sample_batch_id IN (:core, :overlap)
                GROUP BY sample_batch_id, stratum
                """
            ),
            {"core": BATCH_CORE, "overlap": BATCH_OVERLAP},
        )
    ).all()
    out: dict[str, dict[str, int]] = {}
    for batch, stratum, n in rows:
        out.setdefault(str(batch), {})[str(stratum)] = int(n)
    return out


def _expected_counts() -> dict[str, dict[str, int]]:
    core = {name: n for name, n in CORE_STRATA}
    return {BATCH_CORE: core, BATCH_OVERLAP: {"hist_overlap": OVERLAP_N}}


async def _load_membership(session) -> list[tuple[str, str, int]]:
    rows = (
        await session.execute(
            sql_text(
                """
                SELECT sample_batch_id, stratum, message_id
                FROM label_review_samples
                WHERE sample_batch_id IN (:core, :overlap)
                ORDER BY sample_batch_id, stratum, message_id
                """
            ),
            {"core": BATCH_CORE, "overlap": BATCH_OVERLAP},
        )
    ).all()
    return [(str(b), str(s), int(m)) for b, s, m in rows]


def _write_evidence(
    *,
    head: str,
    now: datetime,
    runtime: float,
    iso_before: dict[str, int],
    iso_after: dict[str, int],
    freeze_ids: list[int],
    exclusion_counts: dict[str, int],
    strata_counts: dict[str, int],
    negative_control_pattern_n: int,
    samples: list[dict[str, Any]],
    inserted: int,
) -> None:
    tier_hist: dict[str, Counter[str]] = {}
    eligible_by_stratum: Counter[str] = Counter()
    for row in samples:
        stratum = row["stratum"]
        ann = row["annotation_only"]
        tier = str((ann.get("lead_scorer") or {}).get("tier") or "UNKNOWN")
        tier_hist.setdefault(stratum, Counter())[tier] += 1
        if (ann.get("discovery") or {}).get("eligible"):
            eligible_by_stratum[stratum] += 1

    payload = {
        "evidence_id": EV,
        "generated_at": now.isoformat(),
        "head": head,
        "runtime_sec": round(runtime, 2),
        "seed": SEED,
        "window_days": WINDOW_DAYS,
        "min_text_length": MIN_TEXT,
        "batches": {"core": BATCH_CORE, "overlap": BATCH_OVERLAP},
        "rows_inserted_this_run": inserted,
        "conditioning": {
            "hist_random": "360d AND length(text)>=20 only; not score; not disc_v6",
            "hist_recall_probe": f"buyer phrasing; cap {PROBE_COMMUNITY_CAP}/community",
            "hist_community_spread": "round-robin by community_id",
            "hist_negative_control": "buyer phrasing excluded; prefer non-demand templates",
            "hist_overlap": "same window as hist_random; distinct seed; exclusive ids",
        },
        "negative_control_from_template": negative_control_pattern_n,
        "exclusions": {
            "wp2_freeze_message_ids": freeze_ids,
            "wp2_freeze_n": len(freeze_ids),
            **exclusion_counts,
        },
        "strata_counts": strata_counts,
        "pre_registered_decision_rule": DECISION_RULE,
        "annotation_policy": (
            "LeadScorer and evaluate_discovery results are in this manifest and "
            "135-history-label-frame-samples.json only. They are not written to "
            "label_review_samples, message_scores, human_labels, or candidates."
        ),
        "annotation_rollup": {
            "tier_by_stratum": {k: dict(v) for k, v in tier_hist.items()},
            "disc_v6_eligible_by_stratum": dict(eligible_by_stratum),
        },
        "isolation_before": iso_before,
        "isolation_after": iso_after,
        "human_labels_mutated": False,
        "ml_go_claim": False,
        "ml_training_enabled": False,
        "path_b_path_c": "frozen",
        "shadow": "off",
        "observe_watch_rearmed": False,
        "label_reviews_inserted": 0,
    }
    sample_doc = {
        "evidence_id": EV,
        "generated_at": now.isoformat(),
        "head": head,
        "seed": SEED,
        "exclusions": payload["exclusions"],
        "strata_counts": strata_counts,
        "pre_registered_decision_rule": DECISION_RULE,
        "samples": samples,
    }
    lines = [
        f"Evidence {EV} — history-first label frame",
        f"generated_at={now.isoformat()} head={head} runtime_sec={payload['runtime_sec']}",
        f"seed={SEED} window_days={WINDOW_DAYS} min_text={MIN_TEXT}",
        f"batches core={BATCH_CORE} overlap={BATCH_OVERLAP}",
        f"inserted_this_run={inserted}",
        "strata:",
    ]
    for key, n in strata_counts.items():
        lines.append(f"  {key}={n}")
    lines.extend(
        [
            f"wp2_freeze_excluded={len(freeze_ids)}",
            f"exclusion_counts={json.dumps(exclusion_counts, sort_keys=True)}",
            f"isolation_before hl={iso_before['human_labels']} ac={iso_before['ai_confirmed']} "
            f"scores={iso_before['message_scores']} candidates={iso_before['candidates']}",
            f"isolation_after hl={iso_after['human_labels']} ac={iso_after['ai_confirmed']} "
            f"scores={iso_after['message_scores']} candidates={iso_after['candidates']}",
            "decision_rule: prevalence on hist_random (Clopper-Pearson); "
            f"HAS_DEMAND if lower>={DEMAND_PREVALENCE_LOWER} after n_random>={MIN_RANDOM_REVIEWED} "
            f"and n_probe>={MIN_PROBE_REVIEWED}; SOURCE_ABSENT only if both genuine counts are 0 "
            "at those minimums; empty reviews stay BLOCKED.",
            "annotations=evidence only; human_labels_mutated=false; ml_go_claim=false",
            "labeling_status=not_started; training_blocked=true",
            "observe_watch_rearmed=false; path_b/path_c frozen; shadow off",
        ]
    )
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_SAMPLES.write_text(
        json.dumps(sample_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> int:
    t0 = time.time()
    now = datetime.now(timezone.utc)
    lo = now - timedelta(days=WINDOW_DAYS)
    head = _git_head()
    freeze_ids = _freeze_ids()
    expected = _expected_counts()
    print(f"evidence {EV} head={head} freeze_ids={len(freeze_ids)}", flush=True)

    async with SessionLocal() as session:
        await session.execute(sql_text("SET statement_timeout = 0"))
        await session.execute(sql_text("SET jit = off"))
        iso_before = await _iso(session)
        _require_isolation(iso_before, "before")
        existing = await _existing_counts(session)
        already = existing == expected
        if existing and not already:
            raise SystemExit(f"partial hist360 batches present; aborting: {existing}")

        exclusion_rows = (await session.execute(sql_text(EXCLUSION_SQL))).all()
        db_excluded = {int(r[0]) for r in exclusion_rows}
        exclude = set(freeze_ids) | db_excluded
        exclusion_counts = {
            "label_reviews_or_ai_attempts_or_nonsynthetic_consensus": len(db_excluded),
            "combined_with_wp2_freeze": len(exclude),
        }
        print(f"exclusions={len(exclude)} already_loaded={already}", flush=True)

        inserted = 0
        negative_control_pattern_n = 0
        if already:
            membership = await _load_membership(session)
            print(f"reusing {len(membership)} existing sample rows", flush=True)
        else:
            picked: list[tuple[str, str, int]] = []
            print("drawing hist_random", flush=True)
            random_ids = await _hash_draw(
                session, lo=lo, hi=now, seed=SEED + ":random", limit=1200, exclude=exclude
            )
            if len(random_ids) < 1200:
                raise SystemExit(f"hist_random shortfall {len(random_ids)}")
            exclude.update(random_ids)
            picked.extend((BATCH_CORE, "hist_random", mid) for mid in random_ids)

            print("drawing hist_recall_probe", flush=True)
            probe_ids = await _probe_draw(
                session, lo=lo, hi=now, seed=SEED + ":probe", limit=600, exclude=exclude
            )
            if len(probe_ids) < 600:
                raise SystemExit(f"hist_recall_probe shortfall {len(probe_ids)}")
            exclude.update(probe_ids)
            picked.extend((BATCH_CORE, "hist_recall_probe", mid) for mid in probe_ids)

            print("drawing hist_community_spread", flush=True)
            spread_ids = await _spread_draw(
                session, lo=lo, hi=now, seed=SEED + ":spread", limit=600, exclude=exclude
            )
            if len(spread_ids) < 600:
                raise SystemExit(f"hist_community_spread shortfall {len(spread_ids)}")
            exclude.update(spread_ids)
            picked.extend((BATCH_CORE, "hist_community_spread", mid) for mid in spread_ids)

            print("drawing hist_negative_control", flush=True)
            control_params = {"buyer_rx": BUYER_SQL_RX, "control_rx": CONTROL_SQL_RX}
            template_ids = await _hash_draw(
                session,
                lo=lo,
                hi=now,
                seed=SEED + ":control",
                limit=200,
                exclude=exclude,
                extra_sql=" AND m.text !~* :buyer_rx AND m.text ~* :control_rx",
                extra_params=control_params,
            )
            negative_control_pattern_n = len(template_ids)
            control_ids = list(template_ids)
            if len(control_ids) < 200:
                filler = await _hash_draw(
                    session,
                    lo=lo,
                    hi=now,
                    seed=SEED + ":control_fill",
                    limit=200 - len(control_ids),
                    exclude=exclude | set(control_ids),
                    extra_sql=" AND m.text !~* :buyer_rx",
                    extra_params={"buyer_rx": BUYER_SQL_RX},
                )
                control_ids.extend(filler)
            if len(control_ids) < 200:
                raise SystemExit(f"hist_negative_control shortfall {len(control_ids)}")
            exclude.update(control_ids)
            picked.extend((BATCH_CORE, "hist_negative_control", mid) for mid in control_ids)

            print("drawing hist_overlap", flush=True)
            overlap_ids = await _hash_draw(
                session, lo=lo, hi=now, seed=SEED + ":overlap", limit=OVERLAP_N, exclude=exclude
            )
            if len(overlap_ids) < OVERLAP_N:
                raise SystemExit(f"hist_overlap shortfall {len(overlap_ids)}")
            picked.extend((BATCH_OVERLAP, "hist_overlap", mid) for mid in overlap_ids)
            if len(picked) != 2750 or len({mid for _, _, mid in picked}) != 2750:
                raise SystemExit(f"frame size {len(picked)} is not an exclusive 2750")
            membership = picked

        texts = await _load_rows(session, [mid for _, _, mid in membership])
        missing = [mid for _, _, mid in membership if mid not in texts]
        if missing:
            raise SystemExit(f"missing message rows for {len(missing)} ids")

        settings = get_settings()
        scorer = LeadScorer(settings.scoring_config)
        samples: list[dict[str, Any]] = []
        for idx, (batch, stratum, mid) in enumerate(membership, start=1):
            src = texts[mid]
            ann = _annotate(scorer, src)
            message_date = src.get("message_date")
            samples.append(
                {
                    "message_id": mid,
                    "sample_batch_id": batch,
                    "stratum": stratum,
                    "community_id": int(src["community_id"]) if src.get("community_id") is not None else None,
                    "community_username": src.get("community_username"),
                    "message_date": message_date.isoformat() if message_date is not None else None,
                    "annotation_only": ann,
                }
            )
            if idx % 500 == 0:
                print(f"  annotated {idx}/{len(membership)}", flush=True)

        if not already:
            for batch, stratum, mid in membership:
                session.add(
                    LabelReviewSample(
                        sample_batch_id=batch,
                        message_id=mid,
                        stratum=stratum,
                        methodology_notes=METHODOLOGY,
                    )
                )
            inserted = len(membership)
            iso_mid = await _iso(session)
            _require_isolation(iso_mid, "pre-commit")
            await session.commit()
            print(f"committed {inserted} label_review_samples", flush=True)
        else:
            await session.rollback()

        iso_after = await _iso(session)
        _require_isolation(iso_after, "after")

        strata_counts: dict[str, int] = {}
        for _batch, stratum, _mid in membership:
            strata_counts[stratum] = strata_counts.get(stratum, 0) + 1

        _write_evidence(
            head=head,
            now=now,
            runtime=time.time() - t0,
            iso_before=iso_before,
            iso_after=iso_after,
            freeze_ids=freeze_ids,
            exclusion_counts=exclusion_counts,
            strata_counts=strata_counts,
            negative_control_pattern_n=negative_control_pattern_n,
            samples=samples,
            inserted=inserted,
        )
        print(f"wrote {OUT_TXT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
