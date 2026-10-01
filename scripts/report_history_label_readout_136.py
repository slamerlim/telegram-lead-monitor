#!/usr/bin/env python3
"""Evidence 136 — History label readout (human1/human2 only).

Read-only. Counts reviews on hist360_p1_* batches, Clopper-Pearson prevalence,
Cohen kappa, and probe yield. Emits HISTORY_HAS_DEMAND / HISTORY_DEMAND_SCARCE /
HISTORY_SOURCE_ABSENT only when the pre-registered minimums are met.

Zero reviews stay BLOCKED_PENDING_HUMAN_REVIEW. That is not HISTORY_SOURCE_ABSENT
and it is not HISTORY_HAS_DEMAND. Does not invent labels or write human_labels.

Run:
  docker compose run --rm --no-deps -e GIT_HEAD=$(git rev-parse --short HEAD) \\
    -v \"$PWD:/app\" -w /app analyzer \\
    sh -c 'PYTHONPATH=/app python scripts/report_history_label_readout_136.py'
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text as sql_text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.db import SessionLocal

from scripts.build_history_label_frame_135 import (  # noqa: E402
    BATCH_CORE,
    BATCH_OVERLAP,
    DECISION_RULE,
    DEMAND_PREVALENCE_LOWER,
    EXPECT_AC,
    EXPECT_HL,
    MIN_PROBE_REVIEWED,
    MIN_RANDOM_REVIEWED,
)

EV = "136"
OUT_TXT = ROOT / "docs/audit/evidence/136-history-label-readout.txt"
OUT_JSON = ROOT / "docs/audit/evidence/136-history-label-readout.json"
REVIEWERS = ("human1", "human2")
GENUINE_LABEL = "HUMAN_REVIEWED_TRUE"

ISO_SQL = """
SELECT
  (SELECT COUNT(*) FROM human_labels) AS human_labels,
  (SELECT COUNT(*) FROM leads WHERE status = 'AI_CONFIRMED') AS ai_confirmed
"""

REVIEW_SQL = """
SELECT r.message_id, r.sample_batch_id, r.reviewer_id, r.label,
       r.commercially_actionable, s.stratum
FROM label_reviews r
JOIN label_review_samples s
  ON s.sample_batch_id = r.sample_batch_id
 AND s.message_id = r.message_id
WHERE r.sample_batch_id IN (:core, :overlap)
  AND r.reviewer_id IN ('human1', 'human2')
  AND (r.validator_kind IS NULL OR r.validator_kind = 'human')
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


def _betacf(a: float, b: float, x: float) -> float:
    am = bm = az = 1.0
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    bz = 1.0 - qab * x / qap
    for m in range(1, 201):
        em = float(m)
        tem = em + em
        d = em * (b - em) * x / ((qam + tem) * (a + tem))
        ap = az + d * am
        bp = bz + d * bm
        d = -(a + em) * (qab + em) * x / ((a + tem) * (qap + tem))
        app = ap + d * az
        bpp = bp + d * bz
        am, bm = ap / bpp, bp / bpp
        az, bz = app / bpp, 1.0
        if abs(az - am) < 3e-14 * abs(az):
            return az
    raise RuntimeError("betacf did not converge")


def _reg_inc_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _beta_ppf(p: float, a: float, b: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if _reg_inc_beta(mid, a, b) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float] | None:
    """Two-sided Clopper-Pearson interval. None when n is 0."""
    if n <= 0:
        return None
    if k < 0 or k > n:
        raise ValueError(f"k={k} outside n={n}")
    lo = 0.0 if k == 0 else _beta_ppf(alpha / 2.0, k, n - k + 1)
    hi = 1.0 if k == n else _beta_ppf(1.0 - alpha / 2.0, k + 1, n - k)
    return lo, hi


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    n = len(pairs)
    if n == 0:
        return None
    labels = sorted({a for a, _b in pairs} | {b for _a, b in pairs})
    index = {lab: i for i, lab in enumerate(labels)}
    size = len(labels)
    mat = [[0] * size for _ in range(size)]
    for left, right in pairs:
        mat[index[left]][index[right]] += 1
    po = sum(mat[i][i] for i in range(size)) / n
    row = [sum(mat[i]) for i in range(size)]
    col = [sum(mat[i][j] for i in range(size)) for j in range(size)]
    pe = sum(row[i] * col[i] for i in range(size)) / (n * n)
    if abs(1.0 - pe) < 1e-15:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def _genuine(label: str, actionable: bool | None) -> bool:
    return label == GENUINE_LABEL and actionable is True


def _decide(n_random: int, k_random: int, n_probe: int, k_probe: int) -> dict[str, Any]:
    if n_random < MIN_RANDOM_REVIEWED or n_probe < MIN_PROBE_REVIEWED:
        return {
            "decision": None,
            "decision_status": "BLOCKED_PENDING_HUMAN_REVIEW",
            "emitted_code": None,
            "history_has_demand": False,
            "history_source_absent": False,
            "reason": (
                "Reviews are not yet complete. HISTORY_SOURCE_ABSENT requires "
                f"n_random>={MIN_RANDOM_REVIEWED} and n_probe>={MIN_PROBE_REVIEWED} "
                "actually reviewed. Zero reviews are not source absence."
            ),
        }
    if k_random == 0 and k_probe == 0:
        code = "HISTORY_SOURCE_ABSENT"
    else:
        interval = clopper_pearson(k_random, n_random)
        assert interval is not None
        lo, _hi = interval
        code = (
            "HISTORY_HAS_DEMAND"
            if lo >= DEMAND_PREVALENCE_LOWER
            else "HISTORY_DEMAND_SCARCE"
        )
    return {
        "decision": code,
        "decision_status": "DECIDED",
        "emitted_code": code,
        "history_has_demand": code == "HISTORY_HAS_DEMAND",
        "history_source_absent": code == "HISTORY_SOURCE_ABSENT",
        "reason": DECISION_RULE["codes"][code],
    }


def _stratum_stats(groups: dict[int, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    reviewed = len(groups)
    genuine = 0
    label_pairs: list[tuple[str, str]] = []
    genuine_pairs: list[tuple[str, str]] = []
    for reviews in groups.values():
        if reviews and all(_genuine(r["label"], r["commercially_actionable"]) for r in reviews.values()):
            genuine += 1
        if "human1" in reviews and "human2" in reviews:
            label_pairs.append((reviews["human1"]["label"], reviews["human2"]["label"]))
            genuine_pairs.append(
                (
                    "genuine" if _genuine(reviews["human1"]["label"], reviews["human1"]["commercially_actionable"]) else "other",
                    "genuine" if _genuine(reviews["human2"]["label"], reviews["human2"]["commercially_actionable"]) else "other",
                )
            )
    interval = clopper_pearson(genuine, reviewed)
    return {
        "reviewed_n": reviewed,
        "genuine_n": genuine,
        "prevalence": (genuine / reviewed) if reviewed else None,
        "clopper_pearson_95": (
            {"low": interval[0], "high": interval[1]} if interval is not None else None
        ),
        "paired_n": len(label_pairs),
        "kappa_label": cohen_kappa(label_pairs),
        "kappa_genuine": cohen_kappa(genuine_pairs),
    }


async def main() -> int:
    t0 = time.time()
    now = datetime.now(timezone.utc)
    head = _git_head()
    async with SessionLocal() as session:
        iso = {
            k: int(v)
            for k, v in (await session.execute(sql_text(ISO_SQL))).mappings().one().items()
        }
        if iso["human_labels"] != EXPECT_HL or iso["ai_confirmed"] != EXPECT_AC:
            raise SystemExit(
                f"isolation abort: human_labels={iso['human_labels']} "
                f"AI_CONFIRMED={iso['ai_confirmed']}"
            )
        rows = (
            await session.execute(
                sql_text(REVIEW_SQL),
                {"core": BATCH_CORE, "overlap": BATCH_OVERLAP},
            )
        ).mappings().all()

    by_stratum: dict[str, dict[int, dict[str, dict[str, Any]]]] = defaultdict(lambda: defaultdict(dict))
    for row in rows:
        by_stratum[str(row["stratum"])][int(row["message_id"])][str(row["reviewer_id"])] = {
            "label": str(row["label"]),
            "commercially_actionable": row["commercially_actionable"],
        }

    strata = {name: _stratum_stats(groups) for name, groups in sorted(by_stratum.items())}
    for name in (
        "hist_random",
        "hist_recall_probe",
        "hist_community_spread",
        "hist_negative_control",
        "hist_overlap",
    ):
        strata.setdefault(name, _stratum_stats({}))

    random_stats = strata["hist_random"]
    probe_stats = strata["hist_recall_probe"]
    decision = _decide(
        int(random_stats["reviewed_n"]),
        int(random_stats["genuine_n"]),
        int(probe_stats["reviewed_n"]),
        int(probe_stats["genuine_n"]),
    )
    if int(random_stats["reviewed_n"]) == 0 and decision["emitted_code"] is not None:
        raise SystemExit("refusing to emit a history code from zero random reviews")
    if decision["history_has_demand"] and int(random_stats["reviewed_n"]) < MIN_RANDOM_REVIEWED:
        raise SystemExit("refusing HISTORY_HAS_DEMAND below the random minimum")

    probe_n = int(probe_stats["reviewed_n"])
    probe_k = int(probe_stats["genuine_n"])
    payload = {
        "evidence_id": EV,
        "generated_at": now.isoformat(),
        "head": head,
        "runtime_sec": round(time.time() - t0, 2),
        "read_only": True,
        "reviewers": list(REVIEWERS),
        "batches": [BATCH_CORE, BATCH_OVERLAP],
        "review_rows": len(rows),
        "strata": strata,
        "probe_yield": (probe_k / probe_n) if probe_n else None,
        "pre_registered_decision_rule": DECISION_RULE,
        "decision": decision["decision"],
        "decision_status": decision["decision_status"],
        "emitted_code": decision["emitted_code"],
        "history_has_demand": decision["history_has_demand"],
        "history_source_absent": decision["history_source_absent"],
        "reason": decision["reason"],
        "reviews_not_yet_done": decision["decision_status"] != "DECIDED",
        "ml_go_claim": False,
        "human_labels_mutated": False,
        "ml_training_enabled": False,
        "training_blocked": True,
        "labeling_status": "not_started" if len(rows) == 0 else "in_progress",
        "isolation": {
            **iso,
            "expect_human_labels": EXPECT_HL,
            "expect_ai_confirmed": EXPECT_AC,
            "isolation_ok": True,
        },
    }
    lines = [
        f"Evidence {EV} — history label readout",
        f"generated_at={now.isoformat()} head={head}",
        f"review_rows_human1_human2={len(rows)}",
        f"decision_status={payload['decision_status']}",
        f"decision={payload['decision']}",
        f"emitted_code={payload['emitted_code']}",
        f"history_has_demand={payload['history_has_demand']}",
        f"history_source_absent={payload['history_source_absent']}",
        payload["reason"],
        "Reviews are not yet done." if payload["reviews_not_yet_done"] else "Review minimums met.",
        "Decision is blocked pending human1/human2." if payload["reviews_not_yet_done"] else "",
        f"hist_random reviewed={random_stats['reviewed_n']} genuine={random_stats['genuine_n']}",
        f"hist_recall_probe reviewed={probe_stats['reviewed_n']} genuine={probe_stats['genuine_n']} "
        f"probe_yield={payload['probe_yield']}",
        f"ml_go_claim=false human_labels_mutated=false training_blocked=true",
        f"isolation human_labels={iso['human_labels']} AI_CONFIRMED={iso['ai_confirmed']}",
        "labeling_status=not_started; do not train.",
    ]
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_TXT.write_text("\n".join(line for line in lines if line) + "\n", encoding="utf-8")
    print(OUT_TXT.read_text(encoding="utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
