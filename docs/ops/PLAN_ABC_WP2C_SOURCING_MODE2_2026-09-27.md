# Plan ABC — WP2c + Sourcing + Mode-2 RETRIEVED FPs (2026-09-27)

**Status:** EXECUTED  
**Baseline HEAD at close:** `b65b17b` (Mode-2 harden + P0 bare-project-token ownership revert)  
**Mode:** OBSERVE live + Mode-1 offline (A) + authorized Mode-2 (B sourcing, C FP hardening)

## Injected research (planning)

| Stream | Agent | Outcome |
|--------|-------|---------|
| A Option A | `7bac6d3c` | WP2c = 118c expand + new adjudication; exclude all 24 prior freeze IDs |
| B Option B | `24b81832` | Lever = communities enabled/add + scans; NOT path_b/c, NOT scoring.yaml |
| C Option C | `24f1aa6a` | mids 60620 / 4780480 path-E; Mode-1 replay then minimal hardening |

## Evidence ID map

| ID | Stream | Artifacts |
|----|--------|-----------|
| **118c** | A expand | `118c-offline-freeze-expand.{txt,json}`, `118c-offline-freeze-samples.json` |
| **119d** | A adjudicate | `119d-wp2c-e118c-freeze-adjudication.{txt,json}` batch `e118c_wp2c_adjudication_2026-09-27` |
| **119c** | C Mode-2 | `119c-retrieved-fp-mode2-root-cause.{txt,json,md}` |
| **121** | B sourcing | `121-sourcing-experiment-t0.{txt,json}` |

## Hard locks (held)

- Isolation `human_labels=1524` / `AI_CONFIRMED=4`
- No path_b/c reopen; no scoring.yaml loosen; no ML GO; no `human_labels` mutation
- No `docker compose down -v`; never wipe `last_message_id`
- Append-only `label_reviews` for A; do not re-adjudicate e118 / e118b batches

## Execution result

| Stream | Evidence | Result |
|--------|----------|--------|
| A WP2c | 118c + 119d | 16/16 FALSE; excluded 24 prior; batch e118c_wp2c_adjudication_2026-09-27 |
| B Sourcing | 121 | T0 applied; 4 EXCHANGE disabled; 5 rooms added + scans days=14; cursors OK |
| C Mode-2 | 119c + `b65b17b` | Both FPs ineligible; LaborX listing + bare `my strategy` hardened; P0 reverted accidental `project` ownership widen; path_b/c untouched |

**Combined WP2+WP2b+WP2c = 40/40 contamination; 0 genuine_buyer_project → WP3 blocked.**

## Next (see master prompt)

Watch NEW-since-T0; Trigger A quality-classify; no more freeze expand unless owner asks; no Optuna/LGBM/SHAP until S3 genuine positives.

Master continuation: `docs/ops/MASTER_CONTINUATION_PROMPT_COMMERCIAL_DISCOVERY.md`  
Runbook: `docs/ops/COMMERCIAL_DISCOVERY_OBSERVE_RUNBOOK.md`
