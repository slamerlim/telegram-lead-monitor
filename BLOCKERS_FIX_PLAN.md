# Minimal BLOCKERS Fix Plan

**Based on Production Readiness, Lead Quality, and ML Readiness Audit — 2026-09-22**

Principle: **precision → persistence → labels → ML**. No ML until gates pass.

## Status (2026-09-22 implementation)

| Item | Status | Evidence |
|------|--------|----------|
| Phase 0 — preserve ingestion | HOLDING | 6/6 up; Redis pending/lag=0; msg/lead dups=0 |
| BLOCKER 1 — commercial vetoes | DONE | `12-blockers-1-3-acceptance.txt` |
| BLOCKER 2 — opportunity dedup | DONE | `13-blocker-2-opportunity-dedup.txt` (257→158 rows, 0 dups) |
| BLOCKER 3 — community_profiles | DONE | `12-blockers-1-3-acceptance.txt` |
| BLOCKER 4 — persist negatives | DONE | `14-blocker-4-message-scores.txt` (6000 negatives) |
| BLOCKER 5 — labeling workflow | DONE | `15-blocker-5-labeling.txt` (API live; ≥500 labels pending humans) |
| BLOCKER 6 — continuous lead stream | PENDING | Needs observation after corrected scorer + traffic |
| Phase 7 — controlled re-score | PENDING | Bounded slices first; not full 8.5M yet |
| Phase 8 — re-measure precision | PENDING | After labels + re-score |
| Phase 9 — ML readiness gate | NO-GO | M1 (≥500 labels) not met; do not install ML |

## Live metrics snapshot

```text
lead_rows:                 158
distinct_opportunities:    158
duplicate_opportunity_groups: 0
persisted_negatives:       6000
reviewed_labels:           3   (workflow ready; volume TBD)
```

## What not to do yet

No LightGBM/Optuna/SHAP/embeddings; no threshold lowering; no uncontrolled full reprocess;
do not train on the lead table alone; do not treat agent census as ground truth.

## Status snapshot 2026-09-22T11:50Z

- Blocker 6 drip: holding; near-zero new HIGH leads on live traffic (evidence 16d)
- Phase 7/8 after veto harden: CRM 5 rows (3 HIGH + 2 CRM-protected LOW); census FP elim 100%; TRUE_LEAD retention 6/6; unit precision 1.00 (n=6, CI lower 0.61)
- Labels: TRUE_LEAD still ≪150 — M1 unmet; ML NO-GO
