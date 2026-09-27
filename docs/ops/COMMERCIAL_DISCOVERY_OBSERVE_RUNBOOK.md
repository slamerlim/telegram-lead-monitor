# Commercial discovery OBSERVE runbook (post first-iteration)

**Status:** CONTINUOUS OBSERVE ACTIVE  
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
# stdout wake line: AGENT_LOOP_WAKE_observe_abc {...}
# Trigger B also covers score stall: msg_1h>=20 with score_1h==0

# Smoke (healthy idle must print OBSERVE_OK, exit 0 — not a wake):
./scripts/observe_abc_watch.sh --once
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
- **UPSTREAM_LOSS@G3 (offline)** — Evidence 118: messages-only stratified sample (n=8788 / ~360d) found **0** disc_v6-eligible (`RETRIEVED=0`); dominant loss `NO_PATH_MATCH` (8235); est near-miss buyer-RX ≈3031; freezes under `docs/audit/evidence/118-offline-freeze-samples.json`. Does **not** authorize path reopen — next is WP2 adjudication.
- **EXPECTED_IDLE** — 900s scheduler waves (Evidence 114)
- **INTENTIONAL_SHADOW_OFF** — episode→AI handoff gated (Evidence 111)
- **Invite-expired** — community 132 NOISE (Evidence 115)
- Retained 6 pre–path_b disc_v6 rows remain CONTAMINATED historically — verifier primary window is NEW-since-path_b (E117 observability fix)

## Offline prep (allowed while OBSERVE; dormant from production)

```bash
# Read-only inventory — does not enable ML/Optuna/SHAP/LLM or alter discovery
python3 scripts/offline_ml_dataset_readiness_inventory.py

# WP1 Evidence 118 — unbiased corpus loss attribution (Mode 1; no prod writes)
docker compose run --rm --no-deps -v "$PWD:/app" -w /app analyzer \
  sh -c 'PYTHONPATH=/app python scripts/audit_offline_corpus_loss_attribution_360d.py'
```

Master continuation prompt: `docs/ops/MASTER_CONTINUATION_PROMPT_COMMERCIAL_DISCOVERY.md`

## Next hypothesis

Live resume only on **(A)** first NEW disc_v6 (quality-classify; do not reopen path_b/c), **(B)** pipeline/Redis/worker/query failure, or **(C)** isolation drift. Offline next: **WP2** adjudicate E118 freeze samples via new `label_reviews` only. Do not busy remesure. Do not enable shadow / reopen paths without auth.
