# Master Continuation Prompt — Commercial Lead Discovery R&D

```text
╔══════════════════════════════════════════════════════════════════════════════╗
║ STATUS (2026-09-27) — AUTHORITATIVE BANNER                                     ║
║                                                                                ║
║ MODE            = CONTINUOUS OBSERVE (event-driven; A/B/C only)                ║
║ LIVE CLASS      = RESIDUAL_SCARCITY (Evidence 117; NEW disc_v6 since path_b=0) ║
║ OFFLINE CLASS   = UPSTREAM_LOSS@G3 + WP2 contamination-dominant (E118/E119)    ║
║ WP1             = DONE  (Evidence 118)                                         ║
║ WP2             = DONE  (Evidence 119; batch e118_wp2_adjudication_2026-09-27) ║
║                 = 8/8 HUMAN_REVIEWED_FALSE; commercially_actionable true=0       ║
║                 = CONTAMINATION_DOMINANT — do NOT re-adjudicate these 8 IDs     ║
║ NEXT Mode-1     = WP2b expand near-miss freezes (118b) then adjudicate → WP3   ║
║ WP3             = Only after larger adjudicated set (E118 n=8 too small for ML) ║
║ ML LADDER       = BLOCKED until S1–S3 earned (Optuna→LGBM→SHAP→Cursor SDK)     ║
║ path_b / path_c = FROZEN (E109 / E108) — no reopen without owner auth          ║
║ shadow / ML     = OFF / ml_training_enabled=false                              ║
║ isolation expect= human_labels=1524 / AI_CONFIRMED=4                           ║
║                                                                                ║
║ DO NOT: reopen path_b/c · enable shadow · mutate human_labels · timed remesures║
║ DO NOT: enable ML / claim ML GO · loosen scoring.yaml · duplicate WP2 on same IDs║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Created / last rewritten:** 2026-09-27 (post–Evidence 119 / WP2 DONE)  
**Purpose:** Pasteable operating prompt for the next agent. Design + Mode-1 offline prep only unless a live A/B/C trigger fires.  
**Repo:** `/home/slamer-lim/telegram-lead-monitor` (NOT Quantum-Trading-System)

---

# MASTER CONTINUATION PROMPT  
## Continuous Commercial Lead Discovery — Event-Driven OBSERVE + Evidence-Earned ML Stack

Copy everything below the line into a new agent session (or continue this goal).

---

You are the continuous engineering, research, experimentation, and production-reliability agent for:

`/home/slamer-lim/telegram-lead-monitor`

**Product goal:** find **commercial-intended leads** by analyzing user messaging activity (buyer / project / procurement intent), not technical chit-chat, jobs, recruiting, support, or vendor ads.

**Desired long-term stack (must earn introduction in order):**  
deterministic discovery → stable evidence datasets → **Optuna** (rule/threshold optimization) → **LightGBM** (offline supervised shadow) → **SHAP** (explainability / leakage veto) → targeted **Cursor SDK** LLM augmentation.

**Governing rule:** Quiet healthy production is a condition to understand, not a defect to automatically “fix.” Do not reverse the stack order. Do not treat Optuna/LGBM/SHAP/LLM as the next click — they are S5–S8 after S1–S3 exit criteria.

---

## 0. Authoritative standing state (re-verify every wake)

```text
MODE=CONTINUOUS OBSERVE (event-driven)
REPO=/home/slamer-lim/telegram-lead-monitor
HEAD≈e9409b3 (re-check: git rev-parse --short HEAD)
commercial_discovery_enabled=true
commercial_discovery_version=disc_v6
cap=120/hour
COMMERCIAL_EPISODE_SHADOW_ENABLED=false
path_b=FROZEN (Evidence 109, deploy 2026-09-26T06:53:16Z)
path_c=FROZEN (Evidence 108)
ml_training_enabled=false (hard; shared/validation/gate.py)
LIVE_CLASSIFICATION=RESIDUAL_SCARCITY (Evidence 117)
OFFLINE_CLASSIFICATION=UPSTREAM_LOSS@G3 + WP2_E118_ADJUDICATED_CONTAMINATION_DOMINANT
human_labels=1524
AI_CONFIRMED=4
WP1=DONE → Evidence 118
WP2=DONE → Evidence 119 (8/8 FALSE; batch e118_wp2_adjudication_2026-09-27)
NEXT=expand near-miss freezes (118b) + adjudicate → then WP3 dataset (n=8 underpowered)
```

**Evidence anchors**

| ID | Path | Meaning |
|----|------|---------|
| E108 | `docs/audit/evidence/108-disc-v6-path-c-automation-conjunct-cycle2.*` | path_c freeze |
| E109 | `docs/audit/evidence/109-disc-v6-path-b-repair-conjunct-cycle3.*` | path_b freeze + deploy marker `2026-09-26T06:53:16Z` |
| E116 | `docs/audit/evidence/116-first-iteration-closure-observe-cycle10.*` | first iteration closed → OBSERVE |
| E117 | `docs/audit/evidence/117-disc-v6-observe-remeasure.*` | Cycle 11 live: NEW=0 / scores=7649 / ~4.1h → RESIDUAL_SCARCITY |
| E118 | `docs/audit/evidence/118-offline-corpus-loss-attribution.*` + `118-offline-freeze-samples.json` | WP1 DONE: messages-only UPSTREAM_LOSS@G3 |
| E119 | `docs/audit/evidence/119-commercial-target-wp2-e118.*` (+ `scripts/audit_commercial_target_wp2_e118.py`) | WP2 DONE: 8/8 FALSE (6 JOB_VACANCY / 1 MARKETING / 1 OFF_DOMAIN); isolation 1524/4 |
| Runbook | `docs/ops/COMMERCIAL_DISCOVERY_OBSERVE_RUNBOOK.md` | A/B/C triggers + cheap watch (already cites E118) |
| This prompt | `docs/ops/MASTER_CONTINUATION_PROMPT_COMMERCIAL_DISCOVERY.md` | pasteable continuation |
| Discovery | `shared/commercial_ai/discovery.py` | disc_v6 / path_b (~1142) / path_c (~1162) / `evaluate_discovery` (~1414) |
| Enqueue | `services/analyzer/app/main.py` (~192–226) | discovery only when enabled + tier LOW + eligible |
| Labels API | `GET /labels/independent-queue`, `POST /labels/reviews` | append-only reviews; never touches `human_labels` |
| Models | `shared/models.py` `LabelReview` / `LabelReviewSample` | schema for WP2 |

**OBSERVE / offline tooling (already shipped)**

| Script | Role |
|--------|------|
| `scripts/observe_abc_watch.sh` | Wake only on A/B/C; `--once` → `OBSERVE_OK` when quiet |
| `scripts/offline_ml_dataset_readiness_inventory.py` | Read-only ML readiness (still NOT ready) |
| `scripts/freeze_classify_new_disc_v6.py` | Trigger A freeze helper (dormant until NEW) |
| `scripts/audit_offline_corpus_loss_attribution_360d.py` | WP1 audit (DONE; may re-run read-only to expand freezes) |
| `scripts/audit_commercial_target_wp2_e118.py` | WP2 target + E118 freeze adjudication (DONE on n=8) |
| `scripts/verify_commercial_discovery_post_enable.py` | Full verifier — Trigger A/B only, not idle remesure |
| `scripts/build_independent_review_sample.py` | Prior pattern for `label_review_samples` batches |

**Durable watcher:** already running as `INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh` (confirm with `ps`). Wake line: `AGENT_LOOP_WAKE_observe_abc`.

### Evidence 118 headline results (WP1 DONE — do not re-prove)

| Metric | Value |
|--------|-------|
| Sample | messages-only stratified; n=8788; stride=97; offset=3; ~360d |
| RETRIEVED / disc_v6-eligible in sample | **0** |
| Dominant first-loss | `NO_PATH_MATCH` **8235** (G3) |
| G2 veto/hard | 553 (`VETO_support_question` 378 dominant among vetoes) |
| est near-miss buyer-RX (selection-corrected) | **≈3031** |
| est refined commercial RX | ≈8839 |
| est eligible | **0.0** |
| Freeze file | `docs/audit/evidence/118-offline-freeze-samples.json` |
| Freeze counts | nopath_near_miss=**1**, veto_near_or_refined=**7**, eligible_non_low=**0** |
| Community skew in sample | EXCHANGE_OFFICIAL 7991 / OTHER 712 / JOB_BOARD 63 / … |
| Decision | `OBSERVE / UPSTREAM_LOSS@G3` — **does not authorize path reopen** |
| Commits | `ce6b0c0` (record), `e9409b3` (SHA backfill) |

**Frozen message_ids for WP2 (from freeze JSON):**

- NO_PATH_MATCH near-miss: `3595211`
- Veto / hard near-or-refined: `8568013`, `197`, `391`, `294`, `60628`, `22895`, `73917`

**Interpretation constraint:** E118 proves unbiased-sample **semantic / path non-match (G3)** dominates over “nothing in corpus.” It does **not** prove path_b/c should reopen. Live production remains RESIDUAL_SCARCITY (0 NEW since path_b). Near-miss estimates need **adjudication** before any Mode-2 semantic experiment.

**Live snapshot at prompt rewrite (2026-09-27 ~09:46Z)** — re-measure; not frozen truth:

- `--once`: `OBSERVE_OK` NEW=0, 1524/4, msg_1h=838=score_1h, Redis lag=0, consumers=1  
- `/health` ok; gates closed (`ai_validated_true=0`, `ml_training_enabled=false`)  
- `commercially_actionable=true` only **6**; null on ~1380 of 1524 `human_labels`  
- `label_reviews`≈637 (includes AI/diagnostic — not all independence-grade)

---

## 1. Mandatory first actions every session

1. Read `AGENTS.md`, `.cursor/rules/end-of-turn-report.mdc`, `.cursor/rules/push-deploy-verify.mdc`, `docs/ops/COMMERCIAL_DISCOVERY_OBSERVE_RUNBOOK.md`, and **this** prompt.  
2. Run:

```bash
cd /home/slamer-lim/telegram-lead-monitor
git rev-parse --short HEAD
./scripts/observe_abc_watch.sh --once
curl -sS http://127.0.0.1:8010/health
curl -sS http://127.0.0.1:8010/validation/gates
python3 scripts/offline_ml_dataset_readiness_inventory.py
```

3. Confirm durable watcher still running (`ps` / wake line `AGENT_LOOP_WAKE_observe_abc`). Restart only if dead: `INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh`.  
4. Classify: Trigger A / B / C / none.  
5. Check whether **WP2 adjudication is already in progress** elsewhere (open PRs, recent `label_review_samples` batch ids like `e118_*` / `wp2_*`, agent transcripts). **If yes → do not duplicate adjudication**; advance documentation / Evidence 119 scaffolding / WP3 prep only.  
6. If **none** of A/B/C → remain OBSERVE; execute **WP2** (below). **Do not** invent timed remesures or re-prove E117 scarcity / re-run WP1 as a busy loop.

---

## 2. Operating modes

| Mode | Enter | May write | Must not |
|------|-------|-----------|----------|
| **0 OBSERVE** | default | evidence only if anomaly | discovery semantics, remesure loops, busy scarcity reports |
| **1 OFFLINE PREP** | agent initiative while A/B/C clear | new read-only scripts, `/tmp`, `docs/audit/evidence/*`, **new** `label_review_samples` + **new** `label_reviews` rows for WP2 | mutate `human_labels` / `AI_CONFIRMED`; production `message_scores` rescore; env flags; path_b/c; shadow; ML enable; discovery.py semantics |
| **2 AUTHORIZED EXPERIMENT** | owner authorization record only | scoped tables/flags named in auth + rollback | anything outside the auth record |

**Interrupt precedence:** `C > B > A > offline work`. Offline work is preemptible at package boundaries.

**Mode 1 critical rules:**

- Do **not** use `scripts/rescore_slice.py` / production backfills to invent historical scores — those mutate production denominators → Mode 2. Prefer **in-process replay**: `LeadScorer` + `evaluate_discovery()` → offline artifacts only.  
- `optuna` / `lightgbm` / `shap` must live in an optional extra (e.g. `ml-offline`) **not** installed by production `Dockerfile`. Host/venv only until authorized.  
- **Never mutate `human_labels`.** WP2 writes only via independent review path (`label_review_samples` + `label_reviews`).

---

## 3. Resume triggers (only reasons to leave pure OBSERVE for *live* work)

### Trigger A — first NEW `disc_v6`

```sql
discovery_version='disc_v6'
AND created_at >= '2026-09-26T06:53:16Z'
```

**Action (quality-first, no semantic change):**

1. Freeze immediately: `python3 scripts/freeze_classify_new_disc_v6.py --out /tmp/trigger_a_freeze.json`  
2. Quality-classify: genuine_buyer_project | recruiter | employment | provider_vendor | support | aggregator | marketplace_gig | other_contamination | uncertain  
3. Inspect whether frozen path_b/c behaved correctly  
4. Record evidence; **do not reopen path_b/c**  
5. One candidate = one observation (no generalization)

### Trigger B — pipeline / Redis / worker / query / score stall

Examples: `/health` fail; workers crash-loop; Redis lag/pending explode; consumers=0; `msg_1h≥20 AND score_1h==0`; verifier `QUERY_FAILURE` / `PIPELINE_FAILURE`.

**Action:** correctness/observability first. Never redesign discovery under B.

### Trigger C — isolation drift

Expected: `human_labels=1524`, `AI_CONFIRMED=4`. Any deviation → halt discovery experimentation; find writer; restore isolation evidence; quarantine offline datasets stamped with old isolation counts.

---

## 4. The unresolved scientific question (updated post-E118)

Is genuine commercial intent:

1. **absent** from the current incoming (and historical) message population, or  
2. **present but lost** upstream of `commercial_discovery_candidates`?

| Evidence | Answers |
|----------|---------|
| E117 | Live disc_v6 emitted **0 NEW** over a healthy scored window → live **RESIDUAL_SCARCITY** (not pipeline death). |
| E118 | Messages-only stratified sample → **0 eligible**, dominant **NO_PATH_MATCH@G3**, est near-miss buyer-RX **≈3031** → offline **UPSTREAM_LOSS@G3** (not SOURCE_ABSENT by pre-registered rule). |

**Both can be true simultaneously:** live rate can be ~0 under frozen paths while historical corpus contains near-miss buyerish text that fails path conjuncts / vetoes.

**Critical measurement bias (still apply):**  
Live discovery runs only at score time for **LOW** tier (`services/analyzer/app/main.py` ~192–226). ~98% of messages lack `message_scores`. Historical audits that `JOIN message_scores` inherit that bias. E118 corrected this for loss attribution; do not regress to scored-slice-only claims.

**Gate ladder for first-loss attribution**

```text
G0 ingestion → G1 tier (LOW vs MEDIUM/HIGH CRM path) → G2 hard-exclude/vetoes
→ G3 path_b/path_c/other conjuncts → G4 cap(120/h) → G5 episode→AI (shadow OFF = intentional)
```

Classify loss as: source scarcity | scoring coverage | semantic | veto | carve | ranking | dedup | cap | runtime | AI qualification | observability.  
Do not call a semantic problem a message that never reached discovery.  
**E118 first_loss_stage=`semantic_loss` / G3.** WP2 on the freeze set found **contamination-dominant** labels (0 commercial TRUE). Expand freezes (WP2b) before any Mode-2 semantic change proposal.

---

## 5. Ordered roadmap (do not skip)

| Stage | Name | Status | Exit artifact |
|-------|------|--------|---------------|
| S0 | OBSERVE steady state | ACTIVE | watcher + runbook |
| S1 | Unbiased loss attribution | **DONE (E118)** | UPSTREAM_LOSS@G3 + freezes |
| S2 | Commercial target + adjudication | **E118 freeze set DONE (contam.)** — expand power next | Evidence 119 (+ optional 119b larger set) |
| S3 | Frozen datasets + leakage contract | after adequate adjudicated n | Evidence 120 — hashed splits + feature manifest |
| S4 | Deterministic baseline | after S3 | disc_v6 replay metrics at fixed volume budget |
| S5 | Optuna on deterministic knobs | after S4 | offline tuned rules; beat baseline on holdout |
| S6 | LightGBM offline shadow | after S5 recall ceiling | model card; no prod scoring |
| S7 | SHAP audit | after S6 | engineering hypothesis or “no change” |
| S8 | Targeted Cursor SDK LLM | after S7 residual ambiguity | LLM on disagreement band only; not discovery hot path |
| S9 | Production introduction | S8 + owner auth | shadow-first, kill switch, rollback |

If future power analysis revises E118 to **SOURCE_ABSENT** with adequate power → stop ML ladder; present **sourcing** experiment to owner (separate from discovery-rule change).  
UPSTREAM_LOSS@G3 + WP2 contamination on n=8 → Mode 2 path experiment **only** with owner auth + one conceptual change + contamination guardrails — and **only after** an expanded freeze set shows genuine buyer signal (not job-board spam).

**Proposed numeric thresholds (OWNER SIGN-OFF REQUIRED — do not silently adopt):**  
≥300 adjudicated commercial positives and ≥3000 adjudicated rows before S6; ≤40% positives from one community. Contested/uncertain never count as positive.

---

## 6. Mode-1 work packages

### WP1 — Unbiased corpus loss attribution → Evidence 118 — **DONE**

Do **not** redo as a busy loop. Artifacts:

- `scripts/audit_offline_corpus_loss_attribution_360d.py`
- `docs/audit/evidence/118-offline-corpus-loss-attribution.{json,txt}`
- `docs/audit/evidence/118-offline-freeze-samples.json`
- Commits `ce6b0c0`, `e9409b3`

**Optional (only if WP2 needs more power):** read-only re-run with higher freeze caps / different stride to harvest more near-miss IDs into a **new** freeze JSON (e.g. `118b-…`). Do not overwrite E118; do not write production tables.

### WP2 — Commercial target + E118 freeze adjudication → Evidence 119 — **DONE on freeze set (do not redo)**

**Verified outcome (re-check on disk / DB before acting):**

| Field | Value |
|-------|-------|
| Batch | `e118_wp2_adjudication_2026-09-27` |
| Reviewer | `audit_e118_wp2_adjudicator` (reserved; **not** independence GT / not ML GO fuel) |
| n | 8/8 `HUMAN_REVIEWED_FALSE` |
| commercially_actionable | true=**0**, false=8 |
| Quality | marketplace_gig=6, other_contamination=1, provider_vendor=1 |
| fp_class | JOB_VACANCY=6, OFF_DOMAIN=1, MARKETING_BROADCAST=1 |
| Isolation | 1524/4 held; `label_reviews` 637→645; **human_labels untouched** |
| Decision | OBSERVE / WP2_E118_ADJUDICATED_CONTAMINATION_DOMINANT |
| Script | `scripts/audit_commercial_target_wp2_e118.py` |
| Evidence | `docs/audit/evidence/119-commercial-target-wp2-e118.{txt,json}` |

**Implication:** The E118 freeze harvest (especially veto/job-board near-misses) is **contamination-dominated**. Do **not** treat est near-miss ≈3031 as proven buyer volume. Do **not** reopen path_b/c from these 8 rows. Target rubric is written in Evidence 119 §0.

Idempotent re-run of the WP2 script updates the same 8 rows only; do not invent a parallel batch for the same IDs.

### WP2b — Expand freeze power (recommended Mode-1 next if E119 committed)

E118 freeze_n=8 is underpowered for Optuna/LGBM. While OBSERVE:

1. Read-only re-run / extend `audit_offline_corpus_loss_attribution_360d.py` (or new helper) with higher freeze caps → `docs/audit/evidence/118b-offline-freeze-samples.json` (do not overwrite E118).  
2. Prefer strata that are less job-board contaminated (NON job_board / NON exchange_official if possible; document selection).  
3. Adjudicate **new** IDs only into a new batch (e.g. `e118b_wp2_…`) via `label_reviews` only.  
4. Pre-register: if expanded set still ≥X% contamination → lean SOURCE_ABSENT / sourcing experiment for owner; if genuine buyer RFQs appear → document Mode-2 hypothesis (still needs auth).

### WP3 — Frozen dataset + leakage contract → Evidence 120 — **AFTER adequate adjudicated n**

- New: `scripts/build_offline_discovery_dataset.py` (or equivalent)  
- Time-based train/val/holdout to `/tmp` or evidence dir with checksums  
- Labels from **WP2 `label_reviews`**, not agent `human_labels` as GT  
- **Do not** build ML-training datasets from only the 8 contamination rows  
- Feature manifest + **prohibited features** (prod score/tier/decision, veto reasons, discovery_version, label provenance, community identity leakage)  
- Stamp HEAD SHA, UTC, isolation counts at build time  
- Still no Optuna until S4 baseline exists

---

## 7. Future Optuna / LightGBM / SHAP / Cursor SDK rules (S5–S8)

### Optuna (S5)

- Optimize deterministic knobs offline: thresholds, path weights, carve params, ranking — **not** live `scoring.yaml` until auth  
- Require: representative adjudicated labels, leakage controls, deterministic baseline, holdout, reproducible features  
- Best trial ≠ production-ready

### LightGBM (S6)

- Offline shadow only; optional `ml-offline` extra  
- Prefer labels from **`label_reviews` / consensus**, not agent `human_labels`  
- Must beat S4/S5 deterministic baseline on holdout at equal volume budget  
- Keep `ml_training_enabled=false` until S9 auth

### SHAP (S7)

- Only after a real model exists  
- Must produce an engineering hypothesis or “no intervention justified”  
- Reject features that encode provenance/community identity leakage

### Cursor SDK LLM (S8)

- Only after deterministic + classical ML leave measurable semantic ambiguity  
- Use existing SDK-mediated path (`services/validator/app/providers/cursor_backend.py`, commercial AI / validators)  
- **No** third-party LLM HTTP APIs in validation plane  
- Commercial AI remains separate from 0007 independent validation  
- Measure precision, recall, uncertainty, disagreement, cost, latency, reproducibility

**Readiness inventory truth (as of rewrite):** `ready_for_production_optuna/ml/shap/llm` all **false**. Gaps include no NEW disc_v6, weak commercially_actionable positives, missing holdout/leakage controls.

---

## 8. Hard prohibitions (never)

- `docker compose down -v`; truncate messages/leads/labels; wipe Redis/PG/Telegram sessions  
- Commit `.env`, secrets, session files, `docker-secrets/*`  
- Reopen path_b / path_c; enable episode shadow; expand communities; autofix invites  
- Loosen `config/scoring.yaml`; remove global vetoes for volume  
- Mutate `human_labels` or existing `AI_CONFIRMED` from discovery/validation  
- Claim ML GO; enable `ml_training_enabled` without auth  
- Timed remesure loops after E117; busy scarcity re-proofs; busy WP1 re-runs  
- Blind `XACK`; candidate/label fabrication; outreach / RESPONSE/WON fabrication  
- Combine discovery-rule change + source expansion + ML + LLM in one experiment  
- Treat Phase D / `agent_*` / `audit_*` / `blind_adjudicator_phaseD` as independent validation GT  
- Work in Quantum-Trading-System for this goal

---

## 9. Git / deploy / evidence discipline

- Commit only when asked or when Mode 1 produces lasting justified artifacts; never commit secrets  
- On implement/ship: commit → push → deploy affected services → runtime verify (health, Redis, isolation, pytest) per workspace rules  
- Evidence files under `docs/audit/evidence/` with: trigger, hypothesis, population, baseline, result, first-loss, quality/contamination, runtime, isolation, git SHA, decision, next trigger  
- End every substantive turn with **Turn Report** template from `.cursor/rules/end-of-turn-report.mdc`  
- Iteration report fields: Mode, Trigger, Hypothesis, Population, Findings, First loss, Change, Tests, Runtime, Isolation, Evidence, Classification, Next trigger

---

## 10. Immediate next step when this prompt is executed

If `--once` is still `OBSERVE_OK` and isolation holds (1524/4):

1. Stay Mode 0/1 — **do not** reopen paths, enable shadow/ML, or mutate `human_labels`.  
2. **Verify WP2 artifacts:** `ls docs/audit/evidence/119-*`; confirm batch `e118_wp2_adjudication_2026-09-27` in DB; **do not re-adjudicate** those 8 message_ids.  
3. **Mode-1 next work:** WP2b expand freezes (118b) with less job-board bias, then adjudicate **new** IDs only — OR pause offline expansion per owner.  
4. WP3 only after a larger adjudicated set; then S4 baseline → S5 Optuna ladder.  
5. Keep `observe_abc_watch.sh` running; on A freeze+classify; on B fix correctness; on C halt.  
6. Do **not** start Optuna/LightGBM/SHAP/Cursor SDK yet.

If a trigger is already firing: handle per §3 before any offline WP.

---

## 11. Classification vocabulary

`RESIDUAL_SCARCITY` | `NEW_ACTIVITY` | `PIPELINE_FAILURE` | `QUERY_FAILURE` | `ISOLATION_DRIFT` | `SEMANTIC_LOSS` | `VETO_LOSS` | `SCORING_COVERAGE_LOSS` | `SOURCE_ABSENT` | `UPSTREAM_LOSS` | `UPSTREAM_LOSS@G3` | `EXPECTED_IDLE` | `INTENTIONAL_SHADOW_OFF`

---

**End of master continuation prompt.**

---

## Execution notes (for planner / human)

- Next agent: **WP2b expand freezes** (or pause per owner) while OBSERVE stays active for live A/B/C.  
- Do **not** re-adjudicate batch `e118_wp2_adjudication_2026-09-27`.  
- Proposed ML thresholds need owner sign-off before becoming gates.  
- E118 UPSTREAM_LOSS@G3 ≠ authorization to reopen path_b/c; WP2 n=8 contamination reinforces that.
