#!/usr/bin/env python3
"""Read-only V2 exclusion forensics + V3 retrieval audit on disc_v2 seeds.

Does NOT call Cursor SDK. Does NOT mutate commercial_ai / CRM / human_labels.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.analyzer.app.scoring import LeadScorer
from shared.commercial_ai.context import build_commercial_context
from shared.commercial_ai.retrieval_v3 import (
    forensic_v2_exclusions,
    build_commercial_context_v3,
)
from shared.db import SessionLocal
from shared.settings import get_settings

SEEDS = [
    5094046, 264164, 8583467, 5094057, 5103205, 5153559, 5153646, 269825,
    59787, 58794, 270199, 8509479, 1898, 58136, 8509507, 8509492, 8533266,
    8546593, 268975, 265801, 263708, 196, 22581, 8509509, 269002, 269010,
    8583460, 8533281, 8509489, 74373,
]


def _preview(text: str, n: int = 100) -> str:
    return " ".join((text or "").split())[:n]


async def main() -> int:
    settings = get_settings()
    scorer = LeadScorer(settings.scoring_config)
    forensic_rows = []
    retrieval_rows = []
    quality_samples = []
    excl_total: Counter[str] = Counter()
    cases: Counter[str] = Counter()

    async with SessionLocal() as session:
        for sid in SEEDS:
            fr = await forensic_v2_exclusions(session, sid, scorer=scorer)
            forensic_rows.append(fr)
            cases[fr.get("case", "?")] += 1
            for k, v in (fr.get("exclusion_counts") or {}).items():
                excl_total[k] += v

            ctx_v2 = await build_commercial_context(
                session, sid, include_related=True, scorer=scorer, context_version="ctx_v1"
            )
            ctx_v3 = await build_commercial_context_v3(
                session, sid, include_related=True, scorer=scorer
            )
            v2_size = len(ctx_v2.members)
            v3_size = len(ctx_v3.members)
            recovered = max(0, v3_size - v2_size)
            retrieval_rows.append(
                {
                    "seed": sid,
                    "available_history": fr.get("available_history", 0),
                    "v2_selected": v2_size,
                    "v3_stage1_pool": ctx_v3.n_stage1_candidates,
                    "v3_selected": v3_size,
                    "recovered_vs_v2": recovered,
                    "case": fr.get("case"),
                    "plausibly_relevant_excluded_v2": fr.get("plausibly_relevant_excluded", 0),
                    "v3_exclusion_counts": ctx_v3.exclusion_counts,
                    "topic_fp": ctx_v3.topic_fingerprint,
                }
            )

            # Collect quality samples when V3 recovered support beyond seed.
            if v3_size >= 2 and len(quality_samples) < 12:
                supports = [m for m in ctx_v3.members if m.relation != "SEED"]
                quality_samples.append(
                    {
                        "seed": sid,
                        "seed_preview": _preview(ctx_v3.members[0].text if ctx_v3.members else ""),
                        "supports": [
                            {
                                "message_id": m.message_id,
                                "date": m.message_date.isoformat(),
                                "relation": m.relation,
                                "relevance_score": (m.relevance or {}).get("relevance_score"),
                                "reason": m.selection_reason,
                                "preview": _preview(m.text),
                            }
                            for m in supports[:8]
                        ],
                    }
                )

    sizes_v2 = [r["v2_selected"] for r in retrieval_rows]
    sizes_v3 = [r["v3_selected"] for r in retrieval_rows]
    recovered_n = sum(1 for r in retrieval_rows if r["recovered_vs_v2"] > 0)
    hist_exists = sum(1 for r in retrieval_rows if r["available_history"] > 0)

    def pct(n: int, d: int = 30) -> float:
        return round(100.0 * n / d, 1) if d else 0.0

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_seeds": len(SEEDS),
        "cases": dict(cases),
        "v2_exclusion_taxonomy_totals": dict(excl_total),
        "history_exists_pct": pct(hist_exists),
        "case_B_history_exists_plausibly_relevant_filtered": cases.get("B", 0),
        "case_C_history_exists_unrelated": cases.get("C", 0),
        "case_A_no_history": cases.get("A", 0),
        "plausibly_relevant_excluded_sum": sum(
            r.get("plausibly_relevant_excluded", 0) for r in forensic_rows
        ),
        "v2_context_sizes": {
            "mean": round(statistics.mean(sizes_v2), 2),
            "median": statistics.median(sizes_v2),
            "pct_size_1": pct(sum(1 for s in sizes_v2 if s == 1)),
            "pct_size_2_3": pct(sum(1 for s in sizes_v2 if 2 <= s <= 3)),
            "pct_size_ge4": pct(sum(1 for s in sizes_v2 if s >= 4)),
        },
        "v3_context_sizes": {
            "mean": round(statistics.mean(sizes_v3), 2),
            "median": statistics.median(sizes_v3),
            "pct_size_1": pct(sum(1 for s in sizes_v3 if s == 1)),
            "pct_size_2_3": pct(sum(1 for s in sizes_v3 if 2 <= s <= 3)),
            "pct_size_ge4": pct(sum(1 for s in sizes_v3 if s >= 4)),
        },
        "v3_recovery_pct_seeds": pct(recovered_n),
        "avg_stage1_pool": round(
            statistics.mean([r["v3_stage1_pool"] for r in retrieval_rows]), 2
        ),
        "retrieval_rows": retrieval_rows,
        "forensic_rows": forensic_rows,
        "quality_samples": quality_samples,
    }

    out_json = ROOT / "docs/audit/evidence/93-commercial-context-retrieval-v3.json"
    out_txt = ROOT / "docs/audit/evidence/93-commercial-context-retrieval-v3.txt"
    out_json.write_text(json.dumps(summary, indent=2, default=str) + "\n")

    lines = [
        "=== 93 commercial context retrieval V3 (forensics + recall) ===",
        f"generated_at={summary['generated_at']}",
        f"seeds=disc_v2 n={len(SEEDS)}",
        "",
        "--- V2 exclusion taxonomy (code-backed) ---",
        f"cases A(no hist)={cases.get('A',0)} B(relevant filtered)={cases.get('B',0)} "
        f"C(unrelated hist)={cases.get('C',0)}",
        f"exclusion_totals={dict(excl_total)}",
        f"plausibly_relevant_excluded_sum={summary['plausibly_relevant_excluded_sum']}",
        "",
        "--- V2 vs V3 context sizes ---",
        f"v2={summary['v2_context_sizes']}",
        f"v3={summary['v3_context_sizes']}",
        f"v3_recovery_pct_seeds={summary['v3_recovery_pct_seeds']}%",
        f"avg_stage1_pool={summary['avg_stage1_pool']}",
        "",
        "Seed | hist | V2 | V3pool | V3sel | recovered | case",
        "-" * 64,
    ]
    for r in retrieval_rows:
        lines.append(
            f"{r['seed']:8d} | {r['available_history']:4d} | {r['v2_selected']:2d} | "
            f"{r['v3_stage1_pool']:5d} | {r['v3_selected']:2d} | "
            f"{r['recovered_vs_v2']:3d} | {r['case']}"
        )
    lines.append("")
    lines.append("--- quality samples (previews truncated) ---")
    for qs in quality_samples[:10]:
        lines.append(f"SEED {qs['seed']}: {_preview(qs['seed_preview'], 80)}")
        for s in qs["supports"][:5]:
            lines.append(
                f"  + id={s['message_id']} rel={s['relation']} "
                f"score={s['relevance_score']} reason={s['reason']}"
            )
            lines.append(f"    {_preview(s['preview'], 90)}")
    lines.append("")
    lines.append("NO SDK A/B in this artifact. NO OUTREACH. Isolation check separate.")
    out_txt.write_text("\n".join(lines) + "\n")
    print(out_txt.read_text())
    print(f"Wrote {out_json} and {out_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
