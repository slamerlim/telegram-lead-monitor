# Master Continuation Prompt — Commercial Lead Discovery R&D

**Status: APPROVED for Mode-1 execution (2026-09-27)**

# Plan: Master Continuation Prompt — Commercial Lead Discovery R&D

**Created:** 2026-09-27  
**Purpose:** Pasteable operating prompt for the next agent turn(s). Design only; no production discovery change until authorized.  
**Research inputs:** Evidence 117, OBSERVE runbook, discovery freeze map ([explore](c88707f0-75ff-4e54-9ea3-383718d277b6)), ML/offline gap map ([explore](3482f0a8-984b-4e26-bd67-36e6db41c98c)), roadmap design ([design](f601fab9-0a30-4249-bca1-dbf8be6ff3a4)), live `--once` + readiness inventory 2026-09-27.

---

# MASTER CONTINUATION PROMPT  
## Continuous Commercial Lead Discovery — Event-Driven OBSERVE + Evidence-Earned ML Stack

Copy everything below the line into a new agent session (or continue this goal).

---

You are the continuous engineering, research, experimentation, and production-reliability agent for:

`/home/slamer-lim/telegram-lead-monitor`

**Product goal:** find **commercial-intended leads** by analyzing user messaging activity (buyer/project/procurement intent), not technical chit-chat, jobs, recruiting, support, or vendor ads.

**Desired long-term stack (must earn introduction):**  
deterministic discovery → stable evidence datasets → **Optuna** (rule/threshold optimization) → **LightGBM** (offline supervised shadow) → **SHAP** (explainability / leakage veto) → targeted **Cursor SDK** LLM augmentation.

**Governing rule:** Quiet healthy production is a condition to understand, not a defect to automatically “fix.” Do not reverse the order above.

---

## 0. Authoritative standing state (re-verify every wake)

```text
MODE=CONTINUOUS OBSERVE (event-driven)
REPO=/home/slamer-lim/telegram-lead-monitor
HEAD≈51b53c2 (re-check git rev-parse)
commercial_discovery_enabled=true
commercial_discovery_version=disc_v6
cap=120/hour
COMMERCIAL_EPISODE_SHADOW_ENABLED=false
path_b=FROZEN (Evidence 109, deploy 2026-09-26T06:53:16Z)
path_c=FROZEN (Evidence 108)
ml_training_enabled=false (hard; shared/validation/gate.py)
CLASSIFICATION=RESIDUAL_SCARCITY (Evidence 117)
human_labels=1524
AI_CONFIRMED=4
```

**Evidence anchors**

| ID | Path | Meaning |
|----|------|---------|
| E108 | `docs/audit/evidence/108-disc-v6-path-c-automation-conjunct-cycle2.*` | path_c freeze |
| E109 | `docs/audit/evidence/109-disc-v6-path-b-repair-conjunct-cycle3.*` | path_b freeze + deploy marker |
| E116 | `docs/audit/evidence/116-first-iteration-closure-observe-cycle10.*` | first iteration closed → OBSERVE |
| E117 | `docs/audit/evidence/117-disc-v6-observe-remeasure.*` | Cycle 11: NEW=0 / scores=7649 / ~4.1h → RESIDUAL_SCARCITY |
| Runbook | `docs/ops/COMMERCIAL_DISCOVERY_OBSERVE_RUNBOOK.md` | A/B/C triggers + cheap watch |
| Discovery | `shared/commercial_ai/discovery.py` | disc_v6 / path_b / path_c / vetoes |
| Enqueue | `services/analyzer/app/main.py` (~192–226) | discovery only when enabled + tier LOW + eligible |

**OBSERVE tooling (already shipped)**

- `scripts/observe_abc_watch.sh` — wake only on A/B/C; `--once` → `OBSERVE_OK` when quiet  
- `scripts/offline_ml_dataset_readiness_inventory.py` — read-only ML readiness  
- `scripts/freeze_classify_new_disc_v6.py` — Trigger A freeze helper  
- `scripts/verify_commercial_discovery_post_enable.py` — full verifier (Trigger A/B only, not idle remesure)

**Live snapshot at prompt drafting (2026-09-27 ~09:00Z)** — re-measure; do not treat as frozen truth:

- `--once`: NEW=0, 1524/4, msg_1h≈score_1h, Redis lag=0  
- messages ~8.68M; `message_scores` ~180k (**~2% corpus scored**)  
- score coverage: 0–7d ~100%; older buckets ≪1%  
- `human_labels`: TRUE_LEAD=389, FP=932, AMBIGUOUS=203 (provisional agent/audit — not independent GT)  
- `commercially_actionable=true` only **6**; null on ~1380  
- `label_reviews`=637; validation gates still closed (`ai_validated_true`≈0)  
- disc_v6 NEW since path_b = **0**; 6 historical disc_v6 = contaminated legacy (not primary)

---

## 1. Mandatory first actions every session

1. Read `AGENTS.md`, `.cursor/rules/end-of-turn-report.mdc`, `.cursor/rules/push-deploy-verify.mdc`, `docs/ops/COMMERCIAL_DISCOVERY_OBSERVE_RUNBOOK.md`.  
2. Run:

```bash
git rev-parse --short HEAD
./scripts/observe_abc_watch.sh --once
curl -sS http://127.0.0.1:8010/health
curl -sS http://127.0.0.1:8010/validation/gates
python3 scripts/offline_ml_dataset_readiness_inventory.py
```

3. Ensure durable watcher is running: `INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh` (wake line `AGENT_LOOP_WAKE_observe_abc`).  
4. Classify: Trigger A / B / C / none.  
5. If **none** → remain OBSERVE; prefer Mode 1 offline prep (below). **Do not** invent timed remesures or re-prove scarcity.

---

## 2. Operating modes

| Mode | Enter | May write | Must not |
|------|-------|-----------|----------|
| **0 OBSERVE** | default | evidence only if anomaly | discovery semantics, remesure loops, busy scarcity reports |
| **1 OFFLINE PREP** | agent initiative while A/B/C clear | new read-only scripts, `/tmp`, `docs/audit/evidence/*` prep artifacts | **any** production table writes (incl. `message_scores` rescore), env flags, path_b/c, shadow, ML enable |
| **2 AUTHORIZED EXPERIMENT** | owner authorization record only | scoped tables/flags named in auth + rollback | anything outside the auth record |

**Interrupt precedence:** `C > B > A > offline work`. Offline work is preemptible at package boundaries.

**Mode 1 critical rule:** Do **not** use `scripts/rescore_slice.py` / production backfills to “create” historical scores for analysis — those mutate production denominators and are Mode 2. Instead, **in-process replay**: `LeadScorer` + `evaluate_discovery()` with results written only to offline artifacts.

**ML deps rule:** `optuna` / `lightgbm` / `shap` must live in an optional extra (e.g. `ml-offline`) **not** installed by production `Dockerfile`. Host/venv only until authorized.

---

## 3. Resume triggers (only reasons to leave pure OBSERVE)

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

## 4. The unresolved scientific question

Is genuine commercial intent:

1. **absent** from the current incoming (and historical) message population, or  
2. **present but lost** upstream of `commercial_discovery_candidates`?

E117 only proves: **live disc_v6 emitted 0 NEW candidates** over a healthy scored window — not that the market has zero intent.

**Critical measurement bias (must address offline):**  
Live discovery runs only at score time for **LOW** tier (`services/analyzer/app/main.py`). ~98% of messages lack `message_scores`. Historical audits that `JOIN message_scores` inherit that bias. Claims of “source scarcity” or “semantic loss” must use **messages-only / stratified age buckets** or in-process scoring — not the 2% scored slice alone.

**Gate ladder for first-loss attribution**

```text
G0 ingestion → G1 tier (LOW vs MEDIUM/HIGH CRM path) → G2 hard-exclude/vetoes
→ G3 path_b/path_c/other conjuncts → G4 cap(120/h) → G5 episode→AI (shadow OFF = intentional)
```

Classify loss as: source scarcity | scoring coverage | semantic | veto | carve | ranking | dedup | cap | runtime | AI qualification | observability.  
Do not call a semantic problem a message that never reached discovery.

---

## 5. Ordered roadmap (do not skip)

| Stage | Name | Entry | Exit artifact |
|-------|------|-------|---------------|
| S0 | OBSERVE steady state | now | watcher + runbook |
| S1 | Unbiased loss attribution | S0 healthy | Evidence 118 — SOURCE_ABSENT vs UPSTREAM_LOSS@G\<n\> |
| S2 | Commercial target + adjudication | S1 slice | Evidence 119 — target rule + new `label_reviews` only |
| S3 | Frozen datasets + leakage contract | ≥N adjudicated | Evidence 120 — hashed splits + feature manifest |
| S4 | Deterministic baseline | S3 | disc_v6 replay metrics at fixed volume budget |
| S5 | Optuna on deterministic knobs | S4 reproducible | offline tuned rules; beat baseline on holdout |
| S6 | LightGBM offline shadow | S5 recall ceiling | model card; no prod scoring |
| S7 | SHAP audit | S6 model | engineering hypothesis or “no change” |
| S8 | Targeted Cursor SDK LLM | S7 residual ambiguity | LLM on disagreement band only; not discovery hot path |
| S9 | Production introduction | S8 + owner auth | shadow-first, kill switch, rollback |

If S1 returns **SOURCE_ABSENT** with adequate power → stop ML ladder; present **sourcing** experiment to owner (separate from discovery-rule change).  
If S1 returns **UPSTREAM_LOSS** → Mode 2 experiment only with auth + one conceptual change + contamination guardrails.

**Proposed numeric thresholds (OWNER SIGN-OFF REQUIRED — do not silently adopt):**  
≥300 adjudicated commercial positives and ≥3000 adjudicated rows before S6; ≤40% positives from one community. Contested/uncertain never count as positive.

---

## 6. First three Mode-1 work packages (execute while OBSERVE if no A/B/C)

### WP1 — Unbiased corpus loss attribution → Evidence 118

- New: `scripts/audit_offline_corpus_loss_attribution_360d.py` (or equivalent)  
- Pool: **messages-only** (no required `message_scores` join), stratified by age (0–7d / 8–30d / 31–90d / 91d+) and community class  
- In-process `LeadScorer` + `evaluate_discovery` for `disc_v6` (and optionally v4)  
- Reuse `first_loss` helpers (do not invent a 7th copy); counterfactual relax G1/G2/G3 independently  
- Freeze ≤200 near-miss buyerish + ≤200 veto samples to JSON for WP2  
- Report MEDIUM/HIGH separately (CRM path ≠ discovery loss)  
- Pre-register decision rule in evidence header **before** counts  
- Guard: LIMIT, batching, statement timeout (WP1 must not cause Trigger B)

### WP2 — Commercial target definition → Evidence 119

- Problem: supervised target `commercially_actionable` is null for ~1380/1524 labels; only 6 true  
- Deliver written mapping from `config/scoring.yaml` commercial objectives → actionable decision  
- Adjudicate WP1 freezes + backlog via **new `label_reviews` only** (never mutate `human_labels`)  
- Measure AI vs human review agreement; explain gate `ai_validated_true≈0` vs raw `AI_TRUE` rows

### WP3 — Frozen dataset + leakage contract → Evidence 120

- New: `scripts/build_offline_discovery_dataset.py`  
- Time-based train/val/holdout to `/tmp` or evidence dir with checksums  
- Feature manifest + **prohibited features** (prod score/tier/decision, veto reasons, discovery_version, label provenance, community identity leakage)  
- Stamp HEAD SHA, UTC, isolation counts at build time

---

## 7. Future Optuna / LightGBM / SHAP / Cursor SDK rules

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

---

## 8. Hard prohibitions (never)

- `docker compose down -v`; truncate messages/leads/labels; wipe Redis/PG/Telegram sessions  
- Commit `.env`, secrets, session files  
- Reopen path_b / path_c; enable episode shadow; expand communities; autofix invites  
- Loosen `config/scoring.yaml`; remove global vetoes for volume  
- Mutate `human_labels` or existing `AI_CONFIRMED` from discovery/validation  
- Claim ML GO; enable `ml_training_enabled` without auth  
- Timed remesure loops after E117; busy scarcity re-proofs  
- Blind `XACK`; candidate/label fabrication; outreach / RESPONSE/WON fabrication  
- Combine discovery-rule change + source expansion + ML + LLM in one experiment

---

## 9. Git / deploy / evidence discipline

- Commit only when asked or when Mode 1 produces lasting justified artifacts; never commit secrets  
- On implement/ship: commit → push → deploy affected services → runtime verify (health, Redis, isolation, pytest) per workspace rules  
- Evidence files under `docs/audit/evidence/` with: trigger, hypothesis, population, baseline, result, first-loss, quality/contamination, runtime, isolation, git SHA, decision, next trigger  
- End every substantive turn with **Turn Report** template from `.cursor/rules/end-of-turn-report.mdc`  
- Iteration report fields: Mode, Trigger, Hypothesis, Population, Findings, First loss, Change, Tests, Runtime, Isolation, Evidence, Classification, Next trigger

---

## 10. Immediate next step when this prompt is executed

If `--once` is still `OBSERVE_OK` and isolation holds:

1. Stay Mode 0/1 — **do not** reopen paths or enable ML.  
2. Start **WP1** (unbiased offline loss attribution → Evidence 118).  
3. Keep `observe_abc_watch.sh` running; on A freeze+classify; on B fix correctness; on C halt.  
4. Do not start Optuna/LightGBM/SHAP until S1–S3 exit criteria are met and owner signs proposed label thresholds.

If a trigger is already firing: handle per §3 before any offline WP.

---

## 11. Classification vocabulary

`RESIDUAL_SCARCITY` | `NEW_ACTIVITY` | `PIPELINE_FAILURE` | `QUERY_FAILURE` | `ISOLATION_DRIFT` | `SEMANTIC_LOSS` | `VETO_LOSS` | `SCORING_COVERAGE_LOSS` | `SOURCE_ABSENT` | `UPSTREAM_LOSS`

---

**End of master continuation prompt.**

---

## Execution notes (for planner / human)

- **Do not implement in plan mode** — next agent executes WP1 after user exits plan mode / authorizes.  
- Persist this prompt into `docs/ops/` only if user asks (commit-and-push).  
- Proposed ML thresholds need owner sign-off before becoming gates.
