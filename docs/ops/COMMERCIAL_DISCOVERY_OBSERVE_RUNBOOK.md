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
| Earliest timed remesure | `2026-09-26T10:53:16Z` (≥4h post–path_b) |
| Isolation expect | `human_labels=1524`, `AI_CONFIRMED=4` |
| Standing rate UB | `≈0.018/h` (E103/104 empiric LOW) |
| Discovery version | `disc_v6`, cap `120/h`, enabled `true` |

## Resume triggers (any one)

### (a) First NEW disc_v6
`commercial_discovery_candidates` with `discovery_version='disc_v6'` and `created_at >= path_b deploy`.

**Action:** Quality-classify clean RFQ/buyer vs residual contamination on an **untouched** path. Do **not** reopen path_b/c. Freeze samples before any semantic change.

### (b) ≥4h post–path_b scored window
Wall clock ≥ `2026-09-26T10:53:16Z` with non-zero cumulative `message_scores` since path_b.

**Action:** Timed remesure NEW rate vs standing UB and scarcity claim (Evidence 112–115). Prefer full verifier once — not busy loops.

### (c) PIPELINE / REDIS / WORKER / QUERY failure
Examples: `/health` not ok; discovery/AI/`telegram:messages` lag or pending growth with consumers down; scores stall while messages ingest; verifier `QUERY_FAILURE` / `PIPELINE_FAILURE`.

**Action:** Investigate/fix smallest correctness/observability gap. Not path semantics.

### (d) Isolation drift
`human_labels ≠ 1524` or `AI_CONFIRMED ≠ 4`.

**Action:** Halt discovery changes; restore isolation evidence before any resume work.

## Do not (while OBSERVE)

- Reopen path_b or path_c
- Enable episode shadow without explicit auth
- Expand communities / autofix invites without new loss evidence
- Busy remesure before (a) or (b)
- Loosen `config/scoring.yaml` / remove global vetoes / claim ML GO / outreach
- Mutate `human_labels` or `AI_CONFIRMED` from discovery/validation paths
- `docker compose down -v`, wipe candidates, or blind XACK

## Cheap watch (idle)

```bash
curl -sS http://127.0.0.1:8010/health
# SQL (non-destructive): isolation 1524/4; disc_v6 NEW since path_b; score_since_path_b
# Redis: XINFO GROUPS telegram:commercial_discovery / telegram:messages (lag/pending)
```

Full verifier (on resume only):

```bash
docker compose exec -T analyzer sh -c \
  'cd /app && PYTHONPATH=/app python scripts/verify_commercial_discovery_post_enable.py \
   --enable-at 2026-09-25T19:27:00+00:00 --no-write-evidence'
```

Note: since-enable aggregate still includes 6 historical CONTAMINATED rows; primary class for post-fix is NEW-since-path_b windows.

## Standing classification (until resume)

- **RESIDUAL_SCARCITY** — 0 NEW / ~2k scored post path_b (empiric ≪ 0.018/h)
- **EXPECTED_IDLE** — 900s scheduler waves (Evidence 114)
- **INTENTIONAL_SHADOW_OFF** — episode→AI handoff gated (Evidence 111)
- **Invite-expired** — community 132 NOISE (Evidence 115)

## Next hypothesis

**Cycle 11:** Timed remesure after ≥4h post–path_b **or** first NEW disc_v6 — classify rate/quality under frozen path_b+c; escalate only on PIPELINE/REDIS/WORKER/QUERY or isolation drift.

First-iteration is **CLOSED**. Continuous observe remains **ACTIVE** (not “finished forever”).
