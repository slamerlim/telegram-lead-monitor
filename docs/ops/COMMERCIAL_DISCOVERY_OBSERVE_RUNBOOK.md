# Commercial discovery OBSERVE runbook (post first-iteration)

**Status:** CONTINUOUS OBSERVE ACTIVE  
**Standing note:** While `OBSERVE_OK` — do **not** invent timed remesure wakes; wait for durable `INTERVAL_SEC=5400` watcher exit 10 (A/B/C). E127 applied E124 Steps A–F (disable 137/140; replace 138→`@hummingbot_io`, 141→`@jesse_trade`; days=14 scans). Post-scan: 141 published 8; 138 resolved group (seen=1/pub=0). **Evidence 129:** first NEW disc_v6 (`candidate_id=67`) → **provider_vendor / CONTAMINATED** — sticky `A_NEW` is expected; do not re-adjudicate. **Evidence 130:** Mode-1 offline LeadScorer+disc_v6 replay (n=1600 RETRIEVED=0) + readiness inventory — artifact-only; OBSERVE ≠ ML unlock. **Evidence 132:** owner E131 option B+delete — community **139** `@NautilusTrader` disabled then hard-deleted; VERIFY-JOIN A **CLOSED abandoned**. Do **not** reopen path_b/c / enable ML from remediation alone.  
**Closed first-iteration:** Evidence 116 (`docs/audit/evidence/116-first-iteration-closure-observe-cycle10.*`)  
**Path freeze:** path_c (E108) + path_b (E109) — do not reopen without auth + new loss evidence  
**Shadow:** `COMMERCIAL_EPISODE_SHADOW_ENABLED=false` intentional — do not enable without explicit authorization  

## Markers

| Marker | Value |
|--------|--------|
| Enable (env-only) | `2026-09-25T19:27:00Z` (Evidence 106) |
| path_b deploy | `2026-09-26T06:53:16Z` (Evidence 109) |
| Earliest timed remesure | `2026-09-26T10:53:16Z` (≥4h post–path_b) — **done** Evidence 117 |
| Cycle 11 remesure | `2026-09-26T10:58Z` — RESIDUAL_SCARCITY; NEW=0 / scores=7649 / ~4.1h |
| Isolation expect | `human_labels=1524`, `AI_CONFIRMED=4` |
| Standing rate UB | `≈0.018/h` (E103/104 empiric LOW) |
| Discovery version | `disc_v6`, cap `120/h`, enabled `true` |

## Resume triggers (any one) — post Evidence 117

Timed ≥4h remesure is **complete** (Evidence 117). Do **not** invent another timed remesure loop.

### (A) First NEW disc_v6
`commercial_discovery_candidates` with `discovery_version='disc_v6'` and `created_at >= path_b deploy` (`2026-09-26T06:53:16Z`).

**Action:** Freeze sample → quality-classify (buyer/project vs contamination). Do **not** reopen path_b/c. One candidate is one observation.

```bash
# Read-only freeze helper (dormant until Trigger A)
python3 scripts/freeze_classify_new_disc_v6.py --out /tmp/trigger_a_freeze.json
```

### (B) PIPELINE / REDIS / WORKER / QUERY failure
Examples: `/health` not ok; discovery/AI/`telegram:messages` lag or pending growth with consumers down; scores stall while messages ingest; verifier `QUERY_FAILURE` / `PIPELINE_FAILURE`; worker crash loops.

**Action:** Investigate/fix smallest correctness/observability gap. Not path semantics.

### (C) Isolation drift
`human_labels ≠ 1524` or `AI_CONFIRMED ≠ 4`.

**Action:** Halt discovery changes; restore isolation evidence before any resume work.

## Do not (while OBSERVE)

- Reopen path_b or path_c
- Enable episode shadow without explicit auth
- Expand communities / autofix invites without new loss evidence
- Busy remesure / invent timed remesure loops after Evidence 117
- Loosen `config/scoring.yaml` / remove global vetoes / claim ML GO / outreach
- Mutate `human_labels` or `AI_CONFIRMED` from discovery/validation paths
- `docker compose down -v`, wipe candidates, or blind XACK

## Cheap watch (idle)

One-shot:

```bash
curl -sS http://127.0.0.1:8010/health
# SQL (non-destructive): isolation 1524/4; disc_v6 NEW since path_b; score_since_path_b
# Redis: XINFO GROUPS telegram:commercial_discovery / telegram:messages (lag/pending)
```

Event-only loop (wake on A/B/C only — not a remesure):

```bash
INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh
# stdout wake line: AGENT_LOOP_WAKE_observe_abc {...}; quiet loop also appends OBSERVE_OK heartbeats (nohup proof); exit 10 only on A/B/C
# Trigger B also covers score stall: msg_1h>=20 with score_1h==0

# Smoke (healthy idle must print OBSERVE_OK, exit 0 — not a wake):
./scripts/observe_abc_watch.sh --once
# T0 metrics (msgs/scores/disc_v6_since_t0): default Evidence 121; override with SOURCING_T0_AT=...
```

Full verifier (on Trigger A/B only — not idle remesure):

```bash
docker compose exec -T analyzer sh -c \
  'cd /app && PYTHONPATH=/app python scripts/verify_commercial_discovery_post_enable.py \
   --enable-at 2026-09-25T19:27:00+00:00 --path-b-at 2026-09-26T06:53:16+00:00 --no-write-evidence'
```

Note: since-enable aggregate still includes 6 historical CONTAMINATED rows; primary class for post-fix is NEW-since-path_b windows.

## Standing classification (until resume)

- **RESIDUAL_SCARCITY** — Evidence 117: 0 NEW / 7649 scored / ~4.1h post–path_b (rate 0 vs UB 0.018/h; expected≈0.07). Post-E117 traffic continues without NEW (spot-check may show larger scored denom).
- **UPSTREAM_LOSS@G3 (offline)** — Evidence 118: messages-only stratified sample (n=8788 / ~360d) found **0** disc_v6-eligible (`RETRIEVED=0`); dominant loss `NO_PATH_MATCH` (8235); est near-miss buyer-RX ≈3031; freezes under `docs/audit/evidence/118-offline-freeze-samples.json`. Does **not** authorize path reopen.
- **WP2_E118_ADJUDICATED_CONTAMINATION_DOMINANT** — Evidence 119: all 8 E118 freezes → `HUMAN_REVIEWED_FALSE` / `commercially_actionable=false` via batch `e118_wp2_adjudication_2026-09-27`. Isolation 1524/4.
- **WP2B_CONTAMINATION_DOMINANT** — Evidence 118b/119b: expanded freezes (n=16 new IDs; stride=89 offsets=11,23,41) → 16/16 FALSE; batch `e118b_wp2b_adjudication_2026-09-27`. Includes **2 disc_v6 RETRIEVED FPs** (LaborX mid=60620; MEXC FOMO mid=4780480) — hardened in Evidence 119c.
- **WP2C_CONTAMINATION_DOMINANT** — Evidence 118c/119d: further freezes (n=16; stride=83 offsets=7,19,37,53; exclude all 24 prior) → 16/16 FALSE; batch `e118c_wp2c_adjudication_2026-09-27`. Combined WP2+WP2b+WP2c = 40/40 contamination, 0 genuine buyer.
- **MODE2_RETRIEVED_FP_HARDENED** — Evidence 119c: path-E FPs fixed (NEW PROJECT LaborX no FO-carve; bare `my strategy` not ownership). path_b/c untouched.
- **SOURCING_T0_APPLIED** — Evidence 121: Phase A disabled 4 high-volume EXCHANGE_OFFICIAL; Phase B added 5 algo/dev rooms + days=14 scans; cursors unchanged.
- **PHASE_B_INTAKE_GAP (SOURCE_ABSENT)** — Evidence 123: communities 137–141 still 0 messages since T0; 137/140 username resolve failures; 138/139/141 scans completed with messages_seen/published=0. Observational Mode-1 only — does **not** authorize path reopen/ML.
- **PHASE_B_REMEDIATION_APPLIED** — Evidence 127: E124 Steps A–F applied under prior Option B auth. Disabled 137/140; replaced 138→`@hummingbot_io`, 141→`@jesse_trade`; queued days=14 for 138/139/141. Immediate scan outcomes: 141 published 8; 138 resolved correct group (seen=1/pub=0); 139 still 0 (VERIFY-JOIN note; no autofix). Isolation 1524/4.
- **TRIGGER_A_FIRST_NEW_CLASSIFIED** — Evidence 129: candidate_id=67 CONTAMINATED provider_vendor; paths stay frozen.
- **MODE1_OFFLINE_REPLAY_130** — Evidence 130: in-process LeadScorer+disc_v6 messages-only sample n=1600; RETRIEVED=0; ML inventory not ready; no prod rescore.
- **VERIFY_JOIN_139_BLOCKED** — Evidence 131: no JoinChannel/membership script/API; owner-next A/B/C (superseded by E132).
- **DISABLE_DELETE_139_DONE** — Evidence 132: owner E131 B+delete; 139 `@NautilusTrader` disabled then hard-deleted (msgs=0); VERIFY-JOIN A abandoned; 138/141 untouched; isolation 1524/4.
- **EXPECTED_IDLE** — 900s scheduler waves (Evidence 114)
- **INTENTIONAL_SHADOW_OFF** — episode→AI handoff gated (Evidence 111)
- **Invite-expired** — community 132 NOISE (Evidence 115)
- Retained 6 pre–path_b disc_v6 rows remain CONTAMINATED historically — verifier primary window is NEW-since-path_b (E117 observability fix)

## Offline prep (allowed while OBSERVE; dormant from production)

```bash
# Read-only inventory — does not enable ML/Optuna/SHAP/LLM or alter discovery
python3 scripts/offline_ml_dataset_readiness_inventory.py

# Evidence 130 — in-process LeadScorer + disc_v6 replay (messages-only; no prod writes)
docker compose run --rm --no-deps -e GIT_HEAD=$(git rev-parse --short HEAD) \
  -v "$PWD:/app" -w /app analyzer \
  sh -c 'PYTHONPATH=/app python scripts/audit_offline_ml_replay_disc_v6.py'

# WP1 Evidence 118 — unbiased corpus loss attribution (Mode 1; no prod writes)
docker compose run --rm --no-deps -v "$PWD:/app" -w /app analyzer \
  sh -c 'PYTHONPATH=/app python scripts/audit_offline_corpus_loss_attribution_360d.py'

# WP2 Evidence 119 — E118 freeze adjudication → label_reviews only (append; DONE)
docker compose run --rm --no-deps -e GIT_HEAD=$(git rev-parse --short HEAD) \
  -v "$PWD:/app" -w /app analyzer \
  sh -c 'PYTHONPATH=/app python scripts/audit_commercial_target_wp2_e118.py'

# WP2b Evidence 118b — expand freezes (read-only; DONE)
docker compose run --rm --no-deps -e GIT_HEAD=$(git rev-parse --short HEAD) \
  -v "$PWD:/app" -w /app analyzer \
  sh -c 'PYTHONPATH=/app python scripts/expand_offline_freeze_118b.py'

# WP2b Evidence 119b — adjudicate NEW freeze IDs only (append; DONE)
docker compose run --rm --no-deps -e GIT_HEAD=$(git rev-parse --short HEAD) \
  -v "$PWD:/app" -w /app analyzer \
  sh -c 'PYTHONPATH=/app python scripts/adjudicate_wp2b_e118b_freeze_label_reviews.py'
```

Master continuation prompt: `docs/ops/MASTER_CONTINUATION_PROMPT_COMMERCIAL_DISCOVERY.md`

## Next hypothesis

NEXT = Trigger A/B/C from durable watcher (further NEW beyond E129 still freeze+classify). E132 done (139 deleted; VERIFY-JOIN A abandoned). Live resume only on **(A)** additional NEW disc_v6 (no path reopen), **(B)** pipeline failure, or **(C)** isolation drift. Offline: E130 replay artifacts ready; WP2/WP2b/WP2c DONE — do not re-adjudicate `e118_*`; freeze expand needs separate scoped ask. **WP3** blocked until genuine-positive adjudicated n. Do not invent remesure wakes / enable shadow / reopen paths / claim ML GO. Do not UpdateGoal complete.
