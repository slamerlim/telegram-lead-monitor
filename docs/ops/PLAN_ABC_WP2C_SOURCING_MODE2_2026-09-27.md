# Plan ABC — WP2c + Sourcing + Mode-2 RETRIEVED FPs (2026-09-27)

**Status:** EXECUTED — HEAD `e2061ff` pushed; analyzer+discovery-worker rebuilt  
**HEAD at plan write:** re-check `git rev-parse --short HEAD`  
**Mode:** OBSERVE live + Mode-1 offline (A) + authorized Mode-2 (B sourcing, C FP hardening)

## Injected research

| Stream | Agent | Outcome |
|--------|-------|---------|
| A Option A | `7bac6d3c` | WP2c = 118c expand + new adjudication; **exclude all 24** prior freeze IDs; new stride/offsets |
| B Option B | `24b81832` | Lever = communities enabled/add + scans; NOT path_b/c, NOT scoring.yaml, NOT E105 bridge |
| C Option C | `24f1aa6a` | mids 60620 / 4780480 hypothesized path-E; Mode-1 replay then minimal hardening under auth |

## Evidence ID map (collision-safe)

| ID | Stream | Artifacts |
|----|--------|-----------|
| **118c** | A expand | `118c-offline-freeze-expand.{txt,json}`, `118c-offline-freeze-samples.json` |
| **119d** | A adjudicate | `119d-wp2c-e118c-freeze-adjudication.{txt,json}` batch `e118c_wp2c_adjudication_2026-09-27` |
| **119c** | C Mode-2 | `119c-retrieved-fp-mode2-root-cause.{txt,json,md}` |
| **121** | B sourcing | `121-sourcing-experiment-t0.{txt,json}` |

(119c reserved for Mode-2 per Option C deliverable; WP2c adjudication = **119d**.)

## Hard locks

- Isolation `human_labels=1524` / `AI_CONFIRMED=4`; `observe_abc_watch --once` → `OBSERVE_OK` before/after
- No path_b/c reopen; no scoring.yaml loosen; no ML GO; no `human_labels` mutation
- No `docker compose down -v`; never wipe `last_message_id`
- Append-only `label_reviews` for A; do not re-adjudicate e118 / e118b batches
- API lockdown: community toggles via **Postgres** (same lever as API PATCH); scans via SessionLocal+RedisBus

## A — WP2c

1. Script `expand_offline_freeze_118c.py`: exclude 24 IDs from 118+118b; stride=83 offsets=7,19,37,53; rank away JOB_BOARD/EXCHANGE_OFFICIAL
2. Run expand → 118c artifacts
3. Script `adjudicate_wp2c_e118c_freeze_label_reviews.py`: `--dry-run` then write; batch `e118c_wp2c_adjudication_2026-09-27`

## B — Sourcing

1. Pre-register T0 + owner auth in Evidence 121
2. Phase A: disable 4 high-volume EXCHANGE_OFFICIAL (BitgetENOfficial, OKXOfficial_English, WeexGlobal_Group, BybitEnglish)
3. Phase B: add ≤5 E95-style rooms not already present; `POST`-equivalent scans days=14 for **new ids only**
4. Measure NEW-since-T0 messages/scores/disc_v6 + isolation; no code deploy

## C — Mode-2 RETRIEVED FPs

1. DB-backed `evaluate_discovery` replay v4 vs v6 for 60620 / 4780480 → Evidence 119c
2. If falsified gaps match hyp: minimal patches (LaborX listing veto when not carve; narrow path-E ownership away from bare `my strategy`)
3. `pytest tests/test_discovery_laborx_veto_carve_v6.py` (+ focused FP cases); rebuild analyzer if patched

## Close-out

1. Update master continuation prompt + runbook standing state
2. Commit + push (no secrets)
3. Deploy only if discovery.py patched
4. Runtime verify health + gates + isolation + OBSERVE_OK
5. Turn Report with all evidence paths + HEAD


## Execution result (2026-09-27)

| Stream | Evidence | Result |
|--------|----------|--------|
| A WP2c | 118c + 119d | 16/16 FALSE; excluded 24 prior; batch e118c_wp2c_adjudication_2026-09-27; lr 661→677 |
| B Sourcing | 121 | T0 applied; 4 EXCHANGE disabled; 5 rooms added + scans; cursors OK; iso 1524/4 |
| C Mode-2 | 119c | Both FPs ineligible post-harden; pytest 43 passed; analyzer deployed |
