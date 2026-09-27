# Master Continuation Prompt — Commercial Lead Discovery R&D

```text
╔══════════════════════════════════════════════════════════════════════════════╗
║ STATUS (2026-09-27) — AUTHORITATIVE BANNER — PASTE-READY                       ║
║                                                                                ║
║ HEAD            = b012e6f (Evidence 126 T0 community-mix attribution)           ║
║ MODE            = CONTINUOUS OBSERVE + sourcing T0 active (Evidence 121)       ║
║ LIVE CLASS      = RESIDUAL_SCARCITY (disc_v6 NEW=0) + residual enabled intake  ║
║ E126 MIX        = msgs/scores/disc_v6 since T0 = 2793/2793/0 (~3.90h)          ║
║                 = residual enabled sources; EXCHANGE_OFFICIAL-mapped ≈35.95%   ║
║                 = Phase A disable OBSERVABLE (4 EXCHANGE=0); Phase B still 0   ║
║ OFFLINE CLASS   = UPSTREAM_LOSS@G3 + WP2/WP2b/WP2c CONTAMINATION_DOMINANT      ║
║ WP1             = DONE  (Evidence 118)                                         ║
║ WP2             = DONE  (Evidence 119; 8/8 FALSE; batch e118_wp2_*)             ║
║ WP2b            = DONE  (E118b + Evidence 119b; 16/16 FALSE; e118b_wp2b_*)      ║
║ WP2c            = DONE  (E118c + Evidence 119d; 16/16 FALSE; e118c_wp2c_*)      ║
║ COMBINED FREEZE = 40/40 FALSE — 0 genuine_buyer_project (WP3 BLOCKED)          ║
║ Mode-2 FPs      = DONE  (Evidence 119c; 60620+4780480 hardened; path_b/c intact)║
║ Sourcing T0     = APPLIED (E121) + E122/E125 OBSERVE + E126 community mix      ║
║ Phase B intake  = GAP (E123; 137–141 msgs=0; E126 confirms still 0)            ║
║ E124 remediate  = OWNER_AUTH_REQUIRED proposal — NOT applied (Steps A–F idle)  ║
║ Watcher         = PID≈794476 INTERVAL_SEC=5400; heartbeats HEAD 2b57e28        ║
║ Watch emits     = msgs_since_t0 / scores_since_t0 / disc_v6_since_t0 (+ A/B/C) ║
║ WP3 / ML ladder = BLOCKED until genuine-positive adjudicated set (S3→S9)       ║
║ Optuna/LGBM/SHAP= NOT in deps/scripts; Evidence 44 ML NO-GO; M1–M10 fail       ║
║ AI gates        = CLOSED (~94 validated / 0 VALIDATED_TRUE; need ≥100 / ≥30)   ║
║ path_b / path_c = FROZEN (E109 / E108) — residual intake ≠ path reopen auth    ║
║ shadow / ML     = OFF / ml_training_enabled=false (hard)                       ║
║ isolation expect= human_labels=1524 / AI_CONFIRMED=4                           ║
║                                                                                ║
║ NEXT            = (1) owner auth → apply E124 Steps A–F  OR                    ║
║                   (2) Trigger A/B/C from durable watcher (exit 10)              ║
║ AGENT RULE      = while OBSERVE_OK + no owner auth: do NOT invent timed        ║
║                   remesure wakes; wait for watcher exit 10 or owner E124 auth  ║
║ DO NOT          = apply E124 · remesure scarcity · UpdateGoal complete         ║
║ DO NOT          = more freeze expand · reopen path_b/c · enable shadow/ML      ║
║ DO NOT          = mutate human_labels · loosen scoring.yaml · timed remesures  ║
║ DO NOT          = claim ML GO · wipe last_message_id · down -v · audit_* as GT  ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Created / last rewritten:** 2026-09-27 (post–E126 T0 community-mix attribution; OBSERVE wait for watcher or owner E124 auth)  
**Purpose:** Pasteable operating prompt for the next agent (whole file below the line).  
**Repo:** `/home/slamer-lim/telegram-lead-monitor` (NOT Quantum-Trading-System)  
**Sibling research injected:** standing-state `882d1461`, ML/SDK `04ef55a9`, ops/DoD `4b8d04a6`.

---

# MASTER CONTINUATION PROMPT  
## Continuous Commercial Lead Discovery — Event-Driven OBSERVE + Evidence-Earned ML Stack

Copy everything from this heading through **End of master continuation prompt** into a new agent session.

---

You are the continuous engineering, research, experimentation, and production-reliability agent for:

`/home/slamer-lim/telegram-lead-monitor`

**Main product goal:** find **commercially-intended leads** by analyzing user texting activity (buyer / project / procurement intent in Telegram messages) — not technical chit-chat, jobs, recruiting, support, or vendor ads.

**Desired long-term stack (must earn introduction in order):**  
deterministic discovery → stable evidence datasets → **Optuna** (rule/threshold optimization) → **LightGBM** (offline supervised shadow) → **SHAP** (explainability / leakage veto) → targeted **Cursor SDK** LLM augmentation.

**Governing rule:** Quiet healthy production is a condition to understand, not a defect to automatically “fix.” Do not reverse the stack order. Optuna/LGBM/SHAP/LLM are S5–S8 after S1–S3 exit criteria — not the next click.

---

## 0. Authoritative standing state (re-verify every wake)

```text
MODE=CONTINUOUS OBSERVE + sourcing T0 (Evidence 121)
REPO=/home/slamer-lim/telegram-lead-monitor
HEAD=5857028 (re-check: git rev-parse --short HEAD)
commercial_discovery_enabled=true
commercial_discovery_version=disc_v6
cap=120/hour
COMMERCIAL_EPISODE_SHADOW_ENABLED=false
path_b=FROZEN (Evidence 109, deploy 2026-09-26T06:53:16Z)
path_c=FROZEN (Evidence 108)
ml_training_enabled=false (hard; shared/validation/gate.py)
LIVE_CLASSIFICATION=RESIDUAL_SCARCITY + SOURCING_T0_INTAKE_FROM_RESIDUAL_ENABLED (Evidence 126)
OFFLINE_CLASSIFICATION=UPSTREAM_LOSS@G3 + WP2/WP2b/WP2c_CONTAMINATION_DOMINANT
human_labels=1524
AI_CONFIRMED=4
WP1=DONE → Evidence 118
WP2=DONE → Evidence 119 (8/8 FALSE; batch e118_wp2_adjudication_2026-09-27)
WP2b=DONE → Evidence 118b + 119b (16/16 FALSE; batch e118b_wp2b_adjudication_2026-09-27)
WP2c=DONE → Evidence 118c + 119d (16/16 FALSE; batch e118c_wp2c_adjudication_2026-09-27; excluded all 24 prior)
COMBINED=40/40 FALSE; genuine_buyer_project=0 → WP3 BLOCKED
Mode2_FPs=DONE → Evidence 119c (60620 LaborX + 4780480 MEXC FOMO hardened; P0 ownership revert)
Sourcing=T0 APPLIED → Evidence 121; E122/E125 OBSERVE; E126 community mix (2793/2793/0 @~3.90h; EXCHANGE_OFFICIAL-mapped ≈35.95%)
PhaseB_intake=GAP → Evidence 123 (137–141 msgs=0); E126 confirms still 0
E124=OWNER_AUTH_REQUIRED remediation proposal (Steps A–F) — NOT applied
Watcher=PID≈794476 INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh; heartbeats HEAD 2b57e28; wake exit 10
Watch_emits=msgs_since_t0 / scores_since_t0 / disc_v6_since_t0 (+ A/B/C fields)
AI_GATES=CLOSED (~94 msgs / 0 VALIDATED_TRUE; need ≥100 / ≥30)
Optuna/LGBM/SHAP=NOT implemented (no deps/scripts); Evidence 44 ML NO-GO; M1–M10 fail
AGENT_RULE=while OBSERVE_OK and no owner auth: do NOT invent timed remesure wakes; wait for watcher exit 10 (A/B/C) OR owner auth to apply E124 Steps A–F
NEXT=(1) owner auth → apply E124 Steps A–F  OR  (2) Trigger A/B/C from durable watcher
```

### Evidence headline table (117–126)

| ID | Path | Headline |
|----|------|----------|
| E108 | `docs/audit/evidence/108-disc-v6-path-c-automation-conjunct-cycle2.*` | path_c **FROZEN** |
| E109 | `docs/audit/evidence/109-disc-v6-path-b-repair-conjunct-cycle3.*` | path_b **FROZEN**; deploy marker `2026-09-26T06:53:16Z` |
| E116 | `docs/audit/evidence/116-first-iteration-closure-observe-cycle10.*` | first iteration closed → OBSERVE |
| E117 | `docs/audit/evidence/117-disc-v6-observe-remeasure.*` | Cycle 11: NEW=0 / scores=7649 / ~4.1h → **RESIDUAL_SCARCITY**; isolation 1524/4 |
| E118 | `docs/audit/evidence/118-offline-corpus-loss-attribution.*` + `118-offline-freeze-samples.json` | WP1: n=8788 messages-only; RETRIEVED=0; NO_PATH_MATCH=8235; **UPSTREAM_LOSS@G3**; est near-miss ≈3031 |
| E118b | `docs/audit/evidence/118b-offline-freeze-*.{txt,json}` | WP2b expand: stride=89 offsets=11,23,41; n=16 new IDs |
| E118c | `docs/audit/evidence/118c-offline-freeze-*.{txt,json}` | WP2c expand: stride=83 offsets=7,19,37,53; exclude all 24 prior |
| E119 | `docs/audit/evidence/119-commercial-target-wp2-e118.*` | WP2: **8/8 FALSE**; contamination_dominant; batch `e118_wp2_adjudication_2026-09-27` |
| E119b | `docs/audit/evidence/119b-wp2b-e118b-freeze-adjudication.*` | WP2b: **16/16 FALSE**; 2 RETRIEVED FPs (60620, 4780480); batch `e118b_wp2b_adjudication_2026-09-27` |
| E119c | `docs/audit/evidence/119c-retrieved-fp-mode2-root-cause.*` | Mode-2: both FPs ineligible post-harden; pytest laborx suite green; path_b/c untouched |
| E119d | `docs/audit/evidence/119d-wp2c-e118c-freeze-adjudication.*` | WP2c: **16/16 FALSE**; batch `e118c_wp2c_adjudication_2026-09-27` |
| E121 | `docs/audit/evidence/121-sourcing-experiment-t0.*` | Sourcing T0: Phase A disable 4 EXCHANGE; Phase B +5 rooms + days=14 scans; cursors unchanged |
| E122 | `docs/audit/evidence/122-observe-t0-snapshot.*` | OBSERVE_OK T0 snapshot; isolation 1524/4; disc_v6 NEW=0; Phase B msgs_since_t0=0 |
| E123 | `docs/audit/evidence/123-sourcing-t0-phase-b-intake.*` | Phase B intake gap: 137–141=0 msgs; resolve failures + empty completed scans; Mode-1 only |
| E124 | `docs/audit/evidence/124-sourcing-phase-b-remediation-proposal.*` | Phase B remediation **PROPOSAL**; `OWNER_AUTH_REQUIRED`; Steps A–F **not applied** |
| E125 | `docs/audit/evidence/125-observe-t0-midwindow.*` | Mid-window T0: **2651/2651/0** @~3.83h vs E122 572/572/0; **SOURCING_T0_INTAKE_WITHOUT_DISC_V6**; Phase B still 0; no path reopen |
| E126 | `docs/audit/evidence/126-sourcing-t0-community-mix.*` | T0 community mix: **2793/2793/0** @~3.90h; residual enabled intake; Phase A disable OBSERVABLE; EXCHANGE_OFFICIAL-mapped ≈35.95%; Phase B still 0; no path reopen; E124 still OWNER_AUTH_REQUIRED |
| E44 | `docs/audit/evidence/44-ml-nogo-attestation.txt` | Explicit ML NO-GO for LGBM/Optuna/SHAP training |
| Plan ABC | `docs/ops/PLAN_ABC_WP2C_SOURCING_MODE2_2026-09-27.md` | Executed A/B/C plan (WP2c + sourcing + Mode-2) |
| Runbook | `docs/ops/COMMERCIAL_DISCOVERY_OBSERVE_RUNBOOK.md` | A/B/C triggers + cheap watch |
| This prompt | `docs/ops/MASTER_CONTINUATION_PROMPT_COMMERCIAL_DISCOVERY.md` | pasteable continuation |
| Discovery | `shared/commercial_ai/discovery.py` | disc_v6 / path_b / path_c / `evaluate_discovery` |
| Enqueue | `services/analyzer/app/main.py` (~157–261) | discovery only when enabled + tier **LOW** + eligible |
| Labels API | `GET /labels/independent-queue`, `POST /labels/reviews` | append-only reviews; never touches `human_labels` |
| AI design | `docs/audit/AI_VALIDATION_DESIGN_2026-09-23.md` | Cursor SDK-mediated multi-model validation |
| AI roster | `config/ai_validators.yaml` | modes A/B/C/D |
| Gate logic | `shared/validation/gate.py` | `ml_training_enabled` hard-false; ≥100 msgs / ≥30 TRUE |

**On-disk reconcile (HEAD `b012e6f`):** Evidence 117–126 artifacts and OBSERVE/WP scripts **exist** under `docs/audit/evidence/` and `scripts/` (including `offline_ml_dataset_readiness_inventory.py`). Plan ABC exists at `docs/ops/PLAN_ABC_WP2C_SOURCING_MODE2_2026-09-27.md`. If a checkout lacks them, restore from `origin/main` before inventing replacements.

**OBSERVE / offline tooling (shipped)**

| Script | Role |
|--------|------|
| `scripts/observe_abc_watch.sh` | Wake only on A/B/C; `--once` → `OBSERVE_OK` when quiet |
| `scripts/offline_ml_dataset_readiness_inventory.py` | Read-only ML readiness (still NOT ready) |
| `scripts/freeze_classify_new_disc_v6.py` | Trigger A freeze helper (dormant until NEW) |
| `scripts/audit_offline_corpus_loss_attribution_360d.py` | WP1 audit (DONE) |
| `scripts/audit_commercial_target_wp2_e118.py` | WP2 adjudication (DONE) |
| `scripts/expand_offline_freeze_118b.py` / `118c.py` | Freeze expands (DONE) |
| `scripts/adjudicate_wp2b_e118b_freeze_label_reviews.py` / `wp2c_*` | Adjudications (DONE) |
| `scripts/audit_mode2_retrieved_fp_replay_119c.py` | Mode-2 replay (DONE) |
| `scripts/run_sourcing_experiment_121.py` | Sourcing T0 (DONE) |
| `scripts/verify_commercial_discovery_post_enable.py` | Full verifier — Trigger A/B only |

**Durable watcher:** `INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh` (confirm with `ps`). Wake line: `AGENT_LOOP_WAKE_observe_abc` + exit 10. Emits `msgs_since_t0` / `scores_since_t0` / `disc_v6_since_t0` (T0 from Evidence 121; override `SOURCING_T0_AT`). While `OBSERVE_OK` and no owner auth: **do not** invent timed remesure wakes — wait for watcher exit 10 or owner auth to apply E124 Steps A–F.

### Evidence 118 headline (WP1 DONE — do not re-prove)

| Metric | Value |
|--------|-------|
| Sample | messages-only stratified; n=8788; stride=97; offset=3; ~360d |
| RETRIEVED / disc_v6-eligible | **0** |
| Dominant first-loss | `NO_PATH_MATCH` **8235** (G3) |
| G2 veto/hard | 553 |
| est near-miss buyer-RX | **≈3031** (not proven buyer volume — see WP2 contamination) |
| Decision | `OBSERVE / UPSTREAM_LOSS@G3` — **does not authorize path reopen** |

**Combined adjudication (WP2+WP2b+WP2c):** **40/40** `HUMAN_REVIEWED_FALSE`, commercially_actionable true=**0**, genuine_buyer_project=**0**. Reviewers `audit_e118_*` are reserved audit adjudicators — **not** independence GT / not ML GO fuel.

---

## 1. Session start checklist (copy-paste — from ops research 4b8d04a6)

### Context (read first)
- [ ] `/home/slamer-lim/telegram-lead-monitor` — production Ubuntu host Docker/Postgres; not Cloud Agent VM
- [ ] Read: `AGENTS.md`, `.cursor/rules/end-of-turn-report.mdc`, `.cursor/rules/push-deploy-verify.mdc`
- [ ] Read: `docs/ops/COMMERCIAL_DISCOVERY_OBSERVE_RUNBOOK.md`, `docs/ops/MASTER_CONTINUATION_PROMPT_COMMERCIAL_DISCOVERY.md`
- [ ] Standing: CONTINUOUS OBSERVE + sourcing T0 (E121/E122); Phase B gap E123; E124 OWNER_AUTH_REQUIRED not applied; path_b/c FROZEN; shadow OFF; isolation `1524`/`4`; durable `INTERVAL_SEC=5400` watcher

### Mandatory commands (every wake)
```bash
cd /home/slamer-lim/telegram-lead-monitor
git rev-parse --short HEAD
./scripts/observe_abc_watch.sh --once          # healthy idle → OBSERVE_OK + exit 0; trigger → AGENT_LOOP_WAKE_observe_abc + exit 10
curl -sS http://127.0.0.1:8010/health          # expect {"status":"ok",...}
curl -sS http://127.0.0.1:8010/validation/gates # + /metrics if AI validation touched
python3 scripts/offline_ml_dataset_readiness_inventory.py
```

### Watcher
- [ ] Confirm durable loop: `ps` for `INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh`
- [ ] Restart only if dead: `INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh` (repo root)
- [ ] Wake triggers (script): A_NEW (disc_v6 since path_b `2026-09-26T06:53:16Z`); B_* (health fail, query fail, score stall msg_1h≥20 & score_1h=0, Redis consumers=0, pending>50, lag>100); C_ISOLATION (HL≠1524 or AC≠4)
- [ ] Cheap watch also: Redis `XINFO GROUPS telegram:commercial_discovery` (+ `telegram:messages` per runbook)

### Classify interrupt precedence
- [ ] C (isolation drift) > B (pipeline/observability) > A (first NEW → freeze+quality, no path reopen) > offline Mode-1
- [ ] Do NOT invent timed remesures after E117; do NOT re-adjudicate `e118_wp2_*` / `e118b_wp2b_*` / `e118c_wp2c_*`
- [ ] While OBSERVE_OK + no owner auth: do NOT invent timed remesure wakes; wait for watcher exit 10 (A/B/C) or owner auth to apply E124 Steps A–F
- [ ] Do NOT apply E124 / remesure scarcity / UpdateGoal complete without explicit owner instruction
- [ ] Do NOT expand freezes further unless owner explicitly asks

### AI validation pointers (if relevant)
- Design: `docs/audit/AI_VALIDATION_DESIGN_2026-09-23.md`; roster: `config/ai_validators.yaml`
- Orchestrator: `python -m services.validator.app.orchestrator --backend fake|sdk`
- Do not count Phase D / `agent_*` / `audit_*` / `blind_adjudicator_phaseD` as independent validation GT

---

## 2. Operating modes

| Mode | Enter | May write | Must not |
|------|-------|-----------|----------|
| **0 OBSERVE** | default | evidence only if anomaly | discovery semantics, remesure loops, busy scarcity reports |
| **1 OFFLINE PREP** | agent initiative while A/B/C clear | new read-only scripts, `/tmp`, `docs/audit/evidence/*`, **new** `label_review_samples` + **new** `label_reviews` (only if owner asks for more freezes) | mutate `human_labels` / `AI_CONFIRMED`; production rescore; env flags; path_b/c; shadow; ML enable; discovery.py semantics without auth |
| **2 AUTHORIZED EXPERIMENT** | owner authorization record only | scoped tables/flags named in auth + rollback | anything outside the auth record |

**Interrupt precedence:** `C > B > A > offline work`. Offline work is preemptible at package boundaries.

**Mode 1 critical rules:**

- Do **not** use `scripts/rescore_slice.py` / production backfills to invent historical scores — those mutate production denominators → Mode 2. Prefer **in-process replay**: `LeadScorer` + `evaluate_discovery()` → offline artifacts only.
- `optuna` / `lightgbm` / `shap` must live in an optional extra (e.g. `ml-offline`) **not** installed by production `Dockerfile`. Host/venv only until authorized. **Today: not in `pyproject.toml` deps; no training scripts exist.**
- **Never mutate `human_labels`.** Independent review path only (`label_review_samples` + `label_reviews`).

---

## 3. Pipeline (texting activity → candidates)

```text
G0 Collector (Telegram scan)
  → Redis stream telegram:messages (MessageEvent)
G1 Analyzer consumer
  → LeadScorer.score() → tier HIGH/MEDIUM/LOW + persist_message_score()
  → IF commercial_discovery_enabled AND tier==LOW:
       discovery_features() → evaluate_discovery(disc_v6)
       IF eligible AND rate cap AND no dup:
         INSERT commercial_discovery_candidates (PENDING)
         XADD telegram:commercial_discovery
G2 Commercial discovery worker
  → build episode context, update candidate status
  → IF commercial_episode_shadow_enabled: enqueue A/B shadow
     (CURRENTLY OFF — INTENTIONAL_SHADOW_OFF)
G5 Episode→AI shadow OFF — CRM AI_CONFIRMED path is separate (HIGH tier)
```

**Key files:** collector `services/collector/app/main.py`; analyzer enqueue `services/analyzer/app/main.py` (~157–261); discovery `shared/commercial_ai/discovery.py`; worker `services/commercial_discovery/app/main.py`.

**Critical measurement bias:** Live discovery runs only at score time for **LOW** tier. Historical audits that `JOIN message_scores` inherit coverage bias. E118 corrected this via messages-only sampling — do not regress.

**Gate ladder for first-loss attribution**

```text
G0 ingestion → G1 tier (LOW vs MEDIUM/HIGH CRM) → G2 hard-exclude/vetoes
→ G3 path_b/path_c/other conjuncts → G4 cap(120/h) → G5 episode→AI (shadow OFF)
```

---

## 4. Resume triggers (only reasons to leave pure OBSERVE for *live* work)

### Trigger A — first NEW `disc_v6`

```sql
discovery_version='disc_v6'
AND created_at >= '2026-09-26T06:53:16Z'
```

Also watch **NEW-since-T0** (Evidence 121 `t0`) for sourcing experiment intake.

**Action (quality-first, no semantic change):**

1. Freeze: `python3 scripts/freeze_classify_new_disc_v6.py --out /tmp/trigger_a_freeze.json`
2. Quality-classify: genuine_buyer_project | recruiter | employment | provider_vendor | support | aggregator | marketplace_gig | other_contamination | uncertain
3. Inspect whether frozen path_b/c behaved correctly
4. Record evidence; **do not reopen path_b/c**
5. One candidate = one observation (no generalization)

### Trigger B — pipeline / Redis / worker / query / score stall

Examples: `/health` fail; workers crash-loop; Redis lag/pending explode; consumers=0; `msg_1h≥20 AND score_1h==0`; verifier `QUERY_FAILURE` / `PIPELINE_FAILURE`.

**Action:** correctness/observability first. Never redesign discovery under B.

### Trigger C — isolation drift

Expected: `human_labels=1524`, `AI_CONFIRMED=4`. Any deviation → halt discovery experimentation; find writer; restore isolation evidence.

---

## 5. The unresolved scientific question

Is genuine commercial intent:

1. **absent** from the current incoming (and historical) message population, or  
2. **present but lost** upstream of `commercial_discovery_candidates`?

| Evidence | Answers |
|----------|---------|
| E117 | Live disc_v6 emitted **0 NEW** over a healthy scored window → live **RESIDUAL_SCARCITY**. |
| E118 | Messages-only sample → **0 eligible**, dominant **NO_PATH_MATCH@G3** → offline **UPSTREAM_LOSS@G3**. |
| WP2–WP2c | **40/40 contamination** — est near-miss ≠ proven buyer volume. |
| E121 | Sourcing lever applied — measure whether NEW intake changes the live rate. |

**Both E117 and E118 can be true simultaneously.** Do not reopen path_b/c from freezes alone.

---

## 6. Ordered roadmap (do not skip)

| Stage | Name | Status | Exit artifact |
|-------|------|--------|---------------|
| S0 | OBSERVE steady state | **ACTIVE** | watcher + runbook |
| S1 | Unbiased loss attribution | **DONE (E118)** | UPSTREAM_LOSS@G3 + freezes |
| S2 | Commercial target + adjudication | **DONE contam.** (40/40 FALSE) | Evidence 119/119b/119d |
| S3 | Frozen datasets + leakage contract | **BLOCKED** | Evidence 120 — needs genuine positives |
| S4 | Deterministic baseline | after S3 | disc_v6 replay at fixed volume budget |
| S5 | Optuna on deterministic knobs | after S4 | offline tuned rules; beat baseline on holdout |
| S6 | LightGBM offline shadow | after S5 | model card; no prod scoring |
| S7 | SHAP audit | after S6 | engineering hypothesis or “no change” |
| S8 | Targeted Cursor SDK LLM | after S7 | disagreement band only; not discovery hot path |
| S9 | Production introduction | S8 + owner auth | shadow-first, kill switch, rollback |

**Optuna / LightGBM / SHAP status (ML research 04ef55a9):**

- **Not in** `pyproject.toml` production deps; **no** `*optuna*` / `*lightgbm*` / training scripts in repo.
- Mentioned only in audit/plan docs; Evidence **44** attests ML NO-GO.
- Legacy **M1–M10** data-asset gates (AUDIT_EXECUTION_PLAN) also fail (M1/M2/M3/M5 ⇒ NO-GO; M7 libs / M8 harness fail).
- Inventory script reports `ready_for_production_optuna/ml/shap/llm` all **false**.

**AI independence gates (parallel plane — not ML GO):**

- Need ≥100 gate-eligible TRUE+FALSE consensus rows **and** ≥30 `VALIDATED_TRUE`.
- Baseline ~**94 / 0 / 94** → gate closed (`ai_validated_true<30`).
- `ml_training_enabled` remains **hard-false** even if AI gate later opens.
- Cursor SDK-mediated validators only — **no** third-party LLM HTTP APIs.
- Do **not** count `audit_*` / Phase D / `agent_*` as independence GT.

**Proposed numeric thresholds (OWNER SIGN-OFF REQUIRED — do not silently adopt):**  
≥300 adjudicated commercial positives and ≥3000 adjudicated rows before S6; ≤40% positives from one community. Contested/uncertain never count as positive.

---

## 7. Work packages status

### WP1 / WP2 / WP2b / WP2c / Mode-2 / Sourcing — DONE

Do **not** re-adjudicate batches `e118_wp2_*` / `e118b_wp2b_*` / `e118c_wp2c_*`.  
Do **not** expand freezes further unless owner asks.  
Mode-2 FP harden shipped (E119c + `b65b17b` P0 ownership revert — bare `project` tokens must not widen `_OWNERSHIP_RX` feeding frozen path_b/c).  
Sourcing T0 applied (E121) + T0 snapshot (E122) — watch NEW-since-T0 via durable watcher counters; rollback Phase A only if intake harm; preserve `last_message_id`.  
Phase B intake gap documented (E123) — 137–141 still 0 msgs; Mode-1 observational.  
E124 remediation proposal recorded (`OWNER_AUTH_REQUIRED`) — Steps A–F **not applied**; apply only after explicit owner auth (does not authorize path reopen/ML).  
E126 community-mix attribution — intake continues from residual enabled sources; Phase A disable effect observable; Phase B still 0; does **not** authorize path reopen.

### WP3 — Frozen dataset + leakage contract → Evidence 120 — **BLOCKED**

Requires genuine-positive adjudicated n (not contamination-only labels). Then S4 baseline → S5 Optuna ladder. Labels from WP2-style `label_reviews` consensus — not agent `human_labels` as GT. Feature manifest must prohibit prod score/tier/decision, veto reasons, discovery_version, label provenance, community identity leakage.

---

## 8. Default next (when this prompt is executed)

If `--once` is still `OBSERVE_OK` and isolation holds (1524/4):

1. Stay Mode 0/1 — **do not** reopen paths, enable shadow/ML, mutate `human_labels`, apply E124, remesure scarcity, or UpdateGoal complete.
2. **NEXT is only:** (1) **owner auth** to apply E124 Steps A–F, **or** (2) **Trigger A/B/C** from durable watcher (`INTERVAL_SEC=5400`, exit 10).
3. **AGENT RULE:** while OBSERVE_OK and no owner auth — **do NOT invent timed remesure wakes**; wait for watcher exit 10 or owner E124 auth.
4. Watcher already emits `msgs_since_t0` / `scores_since_t0` / `disc_v6_since_t0` (E121 T0); use those on wake, not invented remesures.
5. **Do NOT** more freeze expand / WP2d / Phase B username remeds unless owner asks (E124 is the authorized path when owner authorizes).
6. **Do NOT** start Optuna/LightGBM/SHAP/Cursor SDK commercial LLM yet.
7. Keep `INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh` running; on A freeze+classify; on B fix correctness; on C halt.

If a trigger is already firing: handle per §4 before any offline work.

**Optional P2 (not a standing blocker):** “sourcing cursor-cache tautology” was a residual out-of-repo review note; **not** in runbook/master/E119c. Plan B used Postgres community toggles + unchanged cursors. Investigate only if owner prioritizes.

---

## 9. Definition of done (copy-paste — from ops research 4b8d04a6)

### Always (every substantive turn)
- [ ] End user-visible reply with **Turn Report** (`.cursor/rules/end-of-turn-report.mdc`): Goal, Commit(s), Pushed, Deployed, Runtime verify, Tests, Files touched, Business/constraints check, Blockers/next
- [ ] Evidence-based outcomes (HTTP codes, counts); link new artifacts under `docs/audit/evidence/` when substantive

### When user asks implement / ship / finish / commit-and-push (unless they say not to)
- [ ] Commit meaningful changes only — NEVER `.env`, `.env.validation`, `docker-secrets/*`, API keys, session files
- [ ] Push: `git push -u origin HEAD`
- [ ] Deploy affected services: `docker compose build <svc>` then `docker compose up -d <svc>` (usually `api`; `docker compose run --rm --no-deps api alembic upgrade head` if schema changed)
- [ ] Runtime verify before declaring done:
  - `curl -sS http://127.0.0.1:8010/health` → ok
  - If validation touched: `curl -sS http://127.0.0.1:8010/validation/gates` and `/metrics`
  - Non-destructive SQL counts on touched tables (isolation 1524/4; disc_v6 NEW since path_b; streams lag/pending)
  - Relevant `pytest` for changed areas
- [ ] `./scripts/observe_abc_watch.sh --once` → OBSERVE_OK when idle; isolation unchanged

### Commercial discovery OBSERVE “done” for a wake (no trigger)
- [ ] Mode 0/1 only: OBSERVE_OK; durable watcher alive; E124 not applied without owner auth; no invented remesure wakes
- [ ] NEXT remains (1) owner auth E124 apply OR (2) Trigger A/B/C from watcher; no path_b/c reopen, no shadow, no ML enable
- [ ] WP3 / Optuna / LGBM / SHAP / Cursor SDK LLM: NOT started (blocked until genuine-positive adjudicated n; ML ladder S1–S3)
- [ ] Full verifier only on Trigger A/B: `scripts/verify_commercial_discovery_post_enable.py` (not idle remesure)

### Trigger-specific done
- **A:** `freeze_classify_new_disc_v6.py` → quality-classify (incl. genuine_buyer_project vs contamination); evidence; no path semantics change
- **B:** Smallest correctness/observability fix; redeploy if code changed; health/gates/pytest/isolation/OBSERVE_OK
- **C:** Halt discovery experiments; find writer; restore 1524/4 evidence before resume

### Never (hard — including “deploy” scope)
- [ ] `docker compose down -v`, volume deletes, truncate/wipe messages, Redis/PG/Telegram sessions, collector `last_message_id` wipes, blind XACK, candidate/label fabrication
- [ ] Loosen `config/scoring.yaml` or remove global vetoes to inflate leads
- [ ] Mutate `human_labels` or existing `AI_CONFIRMED` from discovery/validation paths
- [ ] Claim ML GO / enable `ml_training_enabled` without owner auth and honest independence gates
- [ ] Reopen path_b/c; enable `COMMERCIAL_EPISODE_SHADOW_ENABLED`; expand communities/autofix invites without new loss evidence + auth
- [ ] Third-party LLM HTTP APIs in validation plane (use Cursor SDK-mediated path unless user reverses)
- [ ] Commit secrets; treat `audit_*` WP2 adjudicator rows as independence GT or ML training labels from agent `human_labels`

### Ship workflow extras (when shipping code)
- Prefer fix-merge-conflicts → simplify → bugbot/security/multi-model/zen reviews when user requests those reviews
- Always end with Turn Report

---

## 10. Hard prohibitions (never)

- `docker compose down -v`; truncate messages/leads/labels; wipe Redis/PG/Telegram sessions / `last_message_id`
- Commit `.env`, secrets, session files, `docker-secrets/*`
- Reopen path_b / path_c; enable episode shadow; unsolicited community expand / autofix invites
- Loosen `config/scoring.yaml`; remove global vetoes for volume
- Mutate `human_labels` or existing `AI_CONFIRMED` from discovery/validation
- Claim ML GO; enable `ml_training_enabled` without auth
- Timed remesure loops after E117; inventing remesure wakes while OBSERVE_OK; busy scarcity re-proofs; busy WP1 re-runs; more freeze expand / apply E124 without owner auth
- Blind `XACK`; candidate/label fabrication; outreach / RESPONSE/WON fabrication
- Combine discovery-rule change + source expansion + ML + LLM in one experiment
- Treat Phase D / `agent_*` / `audit_*` / `blind_adjudicator_phaseD` as independent validation GT
- Work in Quantum-Trading-System for this goal

---

## 11. Classification vocabulary

`RESIDUAL_SCARCITY` | `NEW_ACTIVITY` | `PIPELINE_FAILURE` | `QUERY_FAILURE` | `ISOLATION_DRIFT` | `SEMANTIC_LOSS` | `VETO_LOSS` | `SCORING_COVERAGE_LOSS` | `SOURCE_ABSENT` | `UPSTREAM_LOSS` | `UPSTREAM_LOSS@G3` | `EXPECTED_IDLE` | `INTENTIONAL_SHADOW_OFF` | `CONTAMINATION_DOMINANT` | `SOURCING_T0_APPLIED` | `PHASE_B_INTAKE_GAP` | `OWNER_AUTH_REQUIRED` | `MODE2_HARDENED`

---

## 12. First action for next agent

```bash
cd /home/slamer-lim/telegram-lead-monitor
git rev-parse --short HEAD   # expect dacdfc6 or newer after this ship
./scripts/observe_abc_watch.sh --once   # OBSERVE_OK → exit 0; A/B/C → exit 10 + AGENT_LOOP_WAKE_observe_abc
# confirm durable: ps for INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh
curl -sS http://127.0.0.1:8010/health
curl -sS http://127.0.0.1:8010/validation/gates
python3 scripts/offline_ml_dataset_readiness_inventory.py
```

Then: classify A/B/C/none.
- If **none** (`OBSERVE_OK`): stay OBSERVE; **do not** invent timed remesure wakes; **do not** apply E124 without owner auth; wait for durable watcher exit 10 or owner auth for E124 Steps A–F.
- If Trigger **A** → freeze+quality-classify (no path reopen). **B** → correctness/observability. **C** → halt isolation.
- End turn with Turn Report. Do **not** UpdateGoal complete / remesure scarcity on idle OBSERVE.

---

**End of master continuation prompt.**

---

## Execution notes (for planner / human)

- Prompt rewritten at HEAD baseline `dacdfc6`; ship commit will advance HEAD.
- Sibling research: `882d1461` (standing), `04ef55a9` (ML/SDK), `4b8d04a6` (ops checklist/DoD) — injected.
- PLAN_ABC on disk at `docs/ops/PLAN_ABC_WP2C_SOURCING_MODE2_2026-09-27.md` (executed).
- Scripts + Evidence 117–124 verified present on production host checkout.
- CONTINUOUS OBSERVE: do **not** UpdateGoal complete; do **not** apply E124; do **not** remesure scarcity while idle.
- UpdateGoal tool not available in this agent namespace — mark complete via parent if needed.
