# HANDOFF — Cursor full session knowledge transfer

| Field | Value |
|-------|--------|
| Document | `docs/audit/HANDOFF_CURSOR_FULL_SESSION_2026-09-23.md` |
| Written | 2026-09-23 (UTC+3 local / production host) |
| Transcript | `…/agent-transcripts/47f43569-0fbc-4722-b81b-c3b77f9c5f21/47f43569-0fbc-4722-b81b-c3b77f9c5f21.jsonl` |
| Prior short handoff | `docs/ops/SESSION_HANDOFF_2026-09-23.md` (**do not trust blindly**; superseded by this file for claims) |
| Scope | Reconstruct entire Cursor↔user session + verify against live repo/runtime |
| Implementation in this turn | **None** (documentation only) |

**Evidence tags used throughout:**  
`VERIFIED` = checked against live git/Docker/Postgres/API/files **in this handoff pass**.  
`PARTIALLY VERIFIED` = supported by repo/evidence but not fully re-measured.  
`HISTORICAL CLAIM` = asserted in past chat/evidence; may be stale.  
`UNVERIFIED` = not rechecked.

---

## 0. Standing constraints (user-imposed, still in force)

| Constraint | Source | Status |
|------------|--------|--------|
| Work on Ubuntu production host via Remote SSH; **not** Cloud Agent VM | Opening prompts | `VERIFIED` (workspace path) |
| **Never** `docker compose down -v`; never truncate tables / delete volumes / wipe Telegram data / destroy cursors | Opening + Phase E | Must continue |
| Do not commit secrets (`.env`, `docker-secrets/*`) | Ongoing | `VERIFIED` gitignored secrets path exists |
| Buyer-side commercial CRM; do not loosen scorer thresholds / invent ML go | Audit → Phase D/E | In force |
| Independent review must not overwrite `human_labels` | Phase D+ | `VERIFIED` in API design |
| Do not treat HIGH / small-n as sales-validated | Runbook + audit | In force |
| ML NO-GO until independent validation gates clear | Evidence 44 + gate code | `VERIFIED` gates false |

**Security note:** User once pasted SSH/sudo passphrase material in chat. **Do not reproduce.** Rotate if transcript was shared. `HISTORICAL CLAIM` that passphrase was disabled on deploy key — not re-verified here.

---

## 1. Verified production snapshot (this pass)

| Item | Value | Tag |
|------|-------|-----|
| Repo | `/home/slamer-lim/telegram-lead-monitor` | `VERIFIED` |
| Branch | `main` tracking `origin/main` | `VERIFIED` |
| HEAD | `394321be88ee5320748b9d5f1273854c4e844ace` | `VERIFIED` |
| `origin/main` | Same SHA | `VERIFIED` |
| Dirty tree | Untracked: `.cursor/`, `docs/ops/SESSION_HANDOFF_2026-09-23.md` | `VERIFIED` |
| Compose services | api, analyzer, collector, scheduler, postgres, redis — **Up** | `VERIFIED` |
| API bind | `127.0.0.1:8010→8010` | `VERIFIED` |
| `/health` | `ok` / postgres ok / redis ok | `VERIFIED` |
| `/review/` | HTTP **200** | `VERIFIED` |
| `/search`, `/labels/stats` | HTTP **403** (lockdown) | `VERIFIED` |
| `REVIEW_UI_LOCKDOWN` | `true` in `.env` | `VERIFIED` |
| Alembic | `0006_label_reviews (head)` | `VERIFIED` |
| Redis | `db0:keys=2` | `VERIFIED` |
| `SEMANTIC_ENABLED` | `false` | `VERIFIED` |
| Grafana/Prometheus in compose | **Absent** | `VERIFIED` |
| Pytest inventory | **91** tests collected | `VERIFIED` |

### Live data counts (`VERIFIED` via `psql`)

| Table / metric | Count |
|----------------|------:|
| messages | 8,546,569 |
| communities | 136 |
| authors | 188,535 |
| leads | **5** |
| message_scores | 39,675 (NEGATIVE 39,672 / POSITIVE 3) |
| human_labels | 1,524 (TRUE_LEAD 389 / FALSE_POSITIVE 932 / AMBIGUOUS 203) |
| human_labels by `labeled_by` | `agent_business_review_2026-09-22` 1374; `audit_census_2026-09-22` 150 |
| label_review_samples | 128 (`indep_review_2026-09-22`) |
| label_reviews | 130 |
| label_reviews by reviewer | `blind_adjudicator_phaseD` 128; `phaseD_smoke` 1; `smoke_human_2026-09-22` 1 |
| label_reviews for `human1`/`human2` | **0** |
| All `label_reviews` `scorer_shown`/`prior_label_shown` | both **false** (130) |

### Live CRM leads (`VERIFIED`)

| id | tier | score | lead_type | buyer_type |
|----|------|------:|-----------|------------|
| 82 | HIGH | 98 | TRADING_SYSTEM_CONTRACT | RECRUITER |
| 83 | HIGH | 98 | TRADING_SYSTEM_CONTRACT | RECRUITER |
| 339 | HIGH | 97 | COPY_TRADING_PROJECT | CLIENT |
| 1095 | LOW | 34 | TECHNICAL_QUESTION | UNKNOWN |
| 1133 | LOW | 0 | TECHNICAL_QUESTION | UNKNOWN |

### Auth env presence (`VERIFIED` names only; values not logged)

- `INDEPENDENT_HUMAN_REVIEWER_IDS=human1,human2`
- `HUMAN_LABEL_REVIEWER_IDS=human1,human2`
- Tokens + HMAC + `LABEL_WRITE_TOKEN` **present** (non-empty)
- Handout: `docker-secrets/independent-reviewers.txt` (gitignored)

### Independence / ML gate logic implication (`VERIFIED` from code + data)

- Displayed independent counts and `ml_gate_independence_ready` require allowlisted reviewers (`human1`/`human2`) + tokens + HMAC.
- Phase D `blind_adjudicator_phaseD` rows **do not** count toward independence (not in allowlist; reserved-style automation).
- Therefore independence gate remains **closed** despite 128 Phase D rows.
- M1/positive gates require allowlisted `human_labels.labeled_by` **and** `LABEL_WRITE_TOKEN`; current 1524 labels are agent/audit → do not open gates for “human-only” counts when counting only allowlisted IDs (human1/2 have 0 CRM labels).

`/labels/stats` not re-readable while lockdown on (`VERIFIED` 403). Gate closedness inferred from code + SQL = `PARTIALLY VERIFIED` for the JSON payload itself.

---

## 2. Chronological inventory of substantive user prompts

System nudges (“Briefly inform…”, subagent follow-ups) omitted. Quotes truncated; full text in transcript.

### P01 — Production remote verify (opening)

- **Wording (excerpt):** Work on Ubuntu production host; not Cloud Agent; no `down -v` / truncate; PR #2 verify hardening; pull, rebuild, stop scheduler, reprocess 1k/10k, uniqueness, restart scheduler.
- **Intent:** Live production verification of remote-verify work.
- **Agent did:** SSH/git recovery path, compose rebuild/smokes, uniqueness checks. `HISTORICAL CLAIM` detailed smoke numbers → see evidence 49/63/65.
- **Commits/evidence:** PR merge lineage `91ef77b` / remote-verify commits; evidence `65-prod-remote-verify.txt` etc.
- **Complete?** Production verify **usable** — `PARTIALLY VERIFIED`.

### P02 — Register host SSH key

- **Intent:** GitHub deploy access.
- **Complete?** Guided; then superseded by P03–P05.

### P03 — “Key is already in use” + existing deploy key fingerprint

- **Intent:** Reuse existing GitHub deploy key.
- **Complete?** Yes (operational).

### P04–P05 — Passphrase / merge PR #2 / disable passphrase / sudo

- **Intent:** Unattended git; merge PR #2; continue verify.
- **Agent did:** Merge path + key passphrase work. **Do not store secrets in docs.**
- **Complete?** PR #2 on main (`91ef77b`) `VERIFIED` in history.

### P06 — Master production-readiness / lead quality / ML audit

- **Intent:** Strict evidence-based audit of live system.
- **Agent did:** Audit plan + evidence `01+`; verdict commercially/ML not ready.
- **Complete?** Audit executed — `PARTIALLY VERIFIED` via evidence corpus presence.

### P07 — Audit plan corrections (`created_at` not `message_date` for growth)

- **Intent:** Fix methodology before audit execution.
- **Agent did:** Incorporated; evidence `02b-created-at-reality.txt`.
- **Complete?** Yes.

### P08–P09 — Minimal BLOCKERS Fix Plan (duplicated send)

- **Intent:** Fix commercial precision & data foundations before ML; measurable steps; protect ingestion.
- **Agent did:** Commercial vetoes, opportunity dedup, community profiles, `message_scores`, labeling infra.
- **Evidence:** `12`–`16`, `14`, `13`, etc.
- **Complete?** Foundations **DONE** per later user table; continuous flow / precision still open.

### P10–P11 — Post-blockers position + next (duplicated)

- **User state table:** Ingestion HOLDING; vetoes/dedup/profiles/negatives/labeling DONE; continuous flow / rescore / precision PENDING; ML NO-GO.
- **Intent:** Proceed evaluation loop without ML.
- **Agent did:** Phase 7/8, M1 labeling, zen reviews.
- **Complete?** Evaluation loop advanced; continuous HIGH drip remains weak (`VERIFIED` leads=5).

### P12–P13 — Master Cursor continue handoff (duplicated)

- **Intent:** Continue from repo+evidence; no redesign; correctness over elegance.
- **Agent did:** Continued Phase 7/8/M1 work; evidence through ~45–46.
- **Complete?** Ongoing program, not a single ticket.

### P14 — Phase D only: independent human review / blinded evaluation

- **Intent:** Stop scorer/ML drift; build independent blind evaluation evidence.
- **Agent did:** `label_reviews` / samples / API; Phase D **agent** blind adjudication (`blind_adjudicator_phaseD`); evidence 48–58.
- **Critical honesty:** Phase D ≠ real independent humans. `VERIFIED` 128 rows under automation ID.
- **Complete?** Infra + agent adjudication **done**; real humans **not**.

### P15 — “perform real blind human review” + goal/commit/bugbot

- **Intent:** Actual humans; instead agent deepened infra/auth.
- **Complete?** **Incomplete** for humans; auth/hardening progressed.

### P16 — “provide a prompt for yourself to bring this system to production…”

- **Intent:** Self-authored productionize playbook.
- **Agent did:** Produced ops-oriented prompt/runbook material; evidence 60–70 era.
- **Complete?** Playbook exists; “production commercially ready” still **no**.

### P17 — continue + simplify/research/zen/cross/goal

- **Intent:** Hardening with review skills.
- **Complete?** Partial continuous improvement.

### P18–P20 — Maximally critical cross-review of reports (near-duplicates)

- **Intent:** Criticize evidence/claims; pick best fixes.
- **Agent did:** Found P1 auth/blind/M1/lead_write issues; evidence `71`–`72`.
- **Complete?** Findings identified.

### P21 — “fix all findings identified”

- **Intent:** Close P1/P0.
- **Agent did:** Fail-closed tokens+HMAC; blind-only; review_token; M1 allowlist+write token; lead_write fixes. Commits `dae088c`, `0fd68fc`, etc. Evidence `73` (overclaims) + `74` (corrections).
- **Complete?** Critical code fixes **yes**; evidence honesty via `74`.

### P22 — Another critical multi-skill review + commit/push

- **Intent:** Residual gaps + ship.
- **Agent did:** Residual closure `74`; runbook `6f6f48a`.
- **Complete?** Hardening ship **yes**.

### P23 — “let’s get human auth configured.”

- **Intent:** Enable `human1`/`human2` for live blind review.
- **Agent did:** Env allowlists/tokens/HMAC/label write; gitignored handout.
- **Complete?** **Auth configured** `VERIFIED`; reviews not started.

### P24 — commit + full chat report

- **Intent:** Persist + narrative report.
- **Complete?** Commit(s) + in-chat report; later superseded by ops short handoff + this file.

### P25 — Phase E: verify auth/gates; no scorer/DB unless regression; prepare humans

- **Intent:** Readiness check only.
- **Agent did:** Evidence `75`; fixed blind `community_id` leak (`ed4792f`); skipped smoke POST as humans.
- **Complete?** Phase E verify **PASS** with residual honesty.

### P26–P27 — Build simple web GUI for human reviewers + review skills (resume)

- **Intent:** Usable same-origin blind UI.
- **Agent did:** `/review` StaticFiles UI; `REVIEW_UI_LOCKDOWN`; reviews; commit `394321b`.
- **Complete?** GUI **shipped** `VERIFIED`.

### P28 — Explicit full-chat handoff document

- **Intent:** Human-readable handoff.
- **Agent did:** `docs/ops/SESSION_HANDOFF_2026-09-23.md` (uncommitted).
- **Complete?** Draft exists; **this file** is the requested audit-grade replacement.

### P29 — PROMPT 3 (this) + PROMPT 4 (AI validation master prompt)

- **Intent:** Full knowledge transfer under `docs/audit/`; then design/prompt for multi-AI autonomous validation replacing human monitoring dependency.
- **This turn:** Handoff document only for PROMPT 3; master implementation prompt produced as sibling artifact (no feature implementation).

---

## 3. Important agent / subagent actions (session)

| Activity | Role | Outcome | Tag |
|----------|------|---------|-----|
| Remote-verify / reprocess smokes | Agent + scripts | Uniqueness / schema hardening | `PARTIALLY VERIFIED` |
| Production readiness audit | Agent + evidence files | Blockers identified | `PARTIALLY VERIFIED` |
| Blocker implementation | Agent | Vetoes, dedup, scores, labels | `PARTIALLY VERIFIED` |
| Phase 7/8 precision loops | Agent | Census precision claims in evidence | `HISTORICAL CLAIM` (wide CI noted in 44) |
| Zen / Bugbot / cross-review / simplify | Subagents | Many findings; P1 hardening | `PARTIALLY VERIFIED` |
| Phase D blind adjudication | Agent scripts | 128 rows as `blind_adjudicator_phaseD` | `VERIFIED` in DB |
| Critical cross-review of own reports | Multi-model subagents | Auth/blind/M1 P1s | Fixed in code |
| GUI reviews (bugbot/zen/cross/simplify) | Subagents | Submit race, weak lockdown test, etc. fixed before `394321b` | `PARTIALLY VERIFIED` |
| CreateGoal for GUI ship | Cursor Goal | Completed ~12m | `HISTORICAL CLAIM` |

**Rejected / avoided approaches (explicit):**

- Wiping volumes / truncating to “clean” eval
- Treating agent TRUE_LEAD as independent validation
- Loosening thresholds to inflate HIGH drip
- Starting LightGBM/Optuna/SHAP (evidence 44 NO-GO)
- Claiming Phase D agent adjudication satisfies independence gate
- Committing reviewer tokens
- Smoke-POSTing as human1/2 during Phase E (would pollute preflight)

---

## 4. Current system architecture (implemented vs remaining)

### 4.1 Telegram ingestion / collector / cursor

- **Implemented:** Telethon collector; community scans; Redis stream handoff to analyzer; incremental cursor discipline emphasized in milestones. `PARTIALLY VERIFIED` (collector Up 14h).
- **Remaining:** Continuous **commercial** lead volume still thin (5 lead rows). Do not destroy cursors.

### 4.2 Message storage (PostgreSQL)

- **Implemented:** `messages`, `authors`, `communities` at multi-million scale. `VERIFIED` counts.
- **Remaining:** Language detection library absent (`HISTORICAL CLAIM` from audit plan); bilingual handling is keyword/heuristic.

### 4.3 Scoring / analyzer / commercial vetoes

- **Implemented:** Rule scorer `config/scoring.yaml` v2.4; analyzer consumer; commercial intent gates; vetoes for support/jobs/marketing etc.; optional semantic boost (**disabled** live). `VERIFIED` SEMANTIC_ENABLED=false.
- **Remaining:** Live HIGH discovery drip weak by design under precision-first policy.

### 4.4 Opportunity deduplication

- **Implemented:** `opportunity_key` unique + lead_write reclaim logic (`0fd68fc`). `VERIFIED` 5 CRM rows.
- **Remaining:** Sales validation of HIGH opportunities (process, not code).

### 4.5 Community profiles

- **Implemented:** Community commercial profile controls (blocker work). `PARTIALLY VERIFIED` via code/evidence.

### 4.6 Feature / negative persistence

- **Implemented:** `message_scores` (~40k rows; overwhelmingly NEGATIVE). `VERIFIED`.
- **Remaining:** Full corpus feature coverage far below 8.5M messages (sampling/reprocess bounded).

### 4.7 Labels — `human_labels`

- **Implemented:** Table + API `POST /labels` with optional `LABEL_WRITE_TOKEN`; 409 on labeled_by conflict; reserved prefixes rejected.
- **Content today:** **Only** agent/audit provisional labels (1524). **Zero** human1/2 CRM labels. `VERIFIED`.
- **Remaining:** Real human CRM labels if still desired; or replace with AI-validation provenance (PROMPT 4 direction).

### 4.8 Independent review — `label_review_samples` / `label_reviews`

- **Implemented:** Stratified sample batch; append-only reviews; unique (message, reviewer, batch).
- **Content today:** 128 sample members; 130 reviews mostly Phase D automation. `VERIFIED`.
- **Remaining:** Allowlisted independent validators (human or future AI identities) at gate-scale volume.

### 4.9 Authentication / HMAC / blind queue / lockdown / GUI

- **Implemented:**
  - Env: reviewer tokens, HMAC secret, allowlists
  - Queue: blind-only; `review_token`; redacted fields; `community_id=0`
  - POST `/labels/reviews` attestation-bound shown flags; never lowerflags
  - GUI `/review/`; lockdown middleware
- **Remaining:** Operator friction when lockdown on; optional server-side skip; no autonomous validator worker yet.

### 4.10 API / scheduler / Redis

- **Implemented:** FastAPI CRM + labels + review; scheduler periodic scans; Redis bus. `VERIFIED` Up.
- **Remaining:** Rich observability (no Grafana in-repo).

### 4.11 ML gates

- **Implemented in API schemas/stats:**
  - `ml_gate_m1_ready`: ≥500 allowlisted human_labels reviewed
  - `ml_gate_positive_ready`: ≥150 allowlisted TRUE_LEAD
  - `ml_gate_independence_ready`: ≥100 distinct blind allowlisted sample reviews **and** ≥30 uncontested TRUE
- **Live:** Effectively **NO-GO** / closed. `PARTIALLY VERIFIED` (lockdown hides stats JSON; SQL+code imply closed).
- **ML training code:** No LightGBM/Optuna in dependencies. `VERIFIED` via evidence 44 + pyproject pattern.

### 4.12 Deployment / monitoring / audit evidence

- **Deploy:** Docker Compose on host; bind API localhost. `VERIFIED`.
- **Monitoring:** Compose healthchecks; **no** Grafana stack in repo. `VERIFIED`.
- **Audit:** Large `docs/audit/evidence/*` corpus (01–75+). 

---

## 5. Commercial objective (do not broaden)

From `config/scoring.yaml` header (`VERIFIED`):

1. buy/commission a trading bot  
2. fix/repair an existing trading bot  
3. hire a trading-system developer (contract/project)  
4. hire a quantitative engineer (contract/project)  
5. hire an ML/AI engineer (contract/project)  
6. implement/automate a trading strategy  

**Explicit non-goals:** Technical relevance ≠ commercial intent; support/API troubleshooting must not become leads.

### How the system distinguishes classes today

| Class | Mechanism |
|-------|-----------|
| Actual commercial opportunities | Commercial intent regex/categories + thresholds → POSITIVE/`leads` when passing vetoes |
| Ordinary trading discussion | Technical categories without commercial purchase/hire/repair intent → blocked / NEGATIVE |
| Exchange support / API troubleshooting | Support-like / non-commercial vetoes & FP classes |
| Generic crypto chatter | Lack of commercial gate + blockers |
| Job vacancies / marketing broadcasts | Explicit FP classes & veto patterns |
| False positives | `FALSE_POSITIVE` + `fp_class` in labels; Phase 8 methodology |

**Honesty:** Current **3 HIGH** CRM rows are scorer outputs, **not** sales-validated and **not** independently validated. `VERIFIED` count; validation status `UNVERIFIED` commercially.

---

## 6. Labeling / review system — independence definition

### What the system currently calls “independent”

**Code definition (`VERIFIED` `main.py`):** A `label_reviews` row counts toward independence **only if**:

1. Independence auth ready (allowlist + tokens + HMAC configured);
2. `reviewer_id` ∈ `INDEPENDENT_HUMAN_REVIEWER_IDS` (live: `human1`,`human2`);
3. `scorer_shown=false` AND `prior_label_shown=false`;
4. `sample_batch_id` present and joins `label_review_samples`;
5. For TRUE threshold: message not contested by any allowlisted blind FALSE.

**Not independent (despite similar tables):**

- `human_labels` with `agent_*` / `audit_*` (`VERIFIED` 1524)
- `blind_adjudicator_phaseD` reviews (`VERIFIED` 128) — automation / reserved-style ID, not allowlisted humans
- Any review where scorer/prior labels were shown (laundering prevented; upsert never lowers flags)

### Evidence required before a label is “independent validation”

- Allowlisted validator identity with secret token
- Blind queue attestation (`review_token` HMAC binds blind=true)
- Membership in declared sample batch
- Volume thresholds for **gate** (100 blind distinct messages + 30 uncontested TRUE) — configurable constants in code today

### Evidence currently missing

- **Any** `human1`/`human2` reviews (`VERIFIED` 0)
- Therefore **zero** gate-eligible independent validation data
- No multi-provider AI validator layer yet
- No Grafana independence dashboard

### GUI

- Path: `http://127.0.0.1:8010/review/`
- Token in memory; refuses start if `/search` not 403
- Does not write `human_labels`

### Contested / anti-laundering

- Contested TRUE excluded when allowlisted blind FALSE exists
- `blind=false` forbidden on independent queue
- Client `scorer_shown` ignored; attestation drives flags
- Independent path never overwrites `human_labels`

---

## 7. Bug / security / correctness register

Status values: **FIXED-DEPLOYED**, **FIXED-COMMITTED**, **OPEN**, **ACCEPTED-RISK**, **DOC-ONLY**.

| ID | Sev | Component | Problem | Expected | Status | Fix / evidence | Regression test |
|----|-----|-----------|---------|----------|--------|----------------|-----------------|
| S1 | P1 | Indep auth | Allowlist spoofable without tokens | Fail closed | FIXED-DEPLOYED | tokens+HMAC or 503; `dae088c`, `74` | pytest 503/401 |
| S2 | P1 | Blind queue | `blind=false` / client flags laundering | Blind-only; attestation | FIXED-DEPLOYED | 403 blind=false; ignore client flags | pytest |
| S3 | P1 | M1 gates | Arbitrary `labeled_by` inflated M1 | Allowlist + write token | FIXED-DEPLOYED | `74` | gate code |
| S4 | P1 | lead_write | opportunity_key / protected lead races | Savepoints + reclaim | FIXED-DEPLOYED | `0fd68fc` | lead_write tests |
| S5 | P1 | Blind queue | Real `community_id` leaked | `community_id=0` | FIXED-DEPLOYED | `ed4792f`, `75` | Phase E |
| S6 | P1 | GUI | Submit race / double-click | Single in-flight submit | FIXED-DEPLOYED | `394321b` GUI | manual/review |
| S7 | P2 | Lockdown test | `in (401,403)` false-pass | Assert 401 + not lockdown detail | FIXED-COMMITTED | test update in `394321b` | pytest |
| S8 | P2 | Same-origin | Scorer endpoints leak if lockdown off | Lockdown during humans | MITIGATED | `REVIEW_UI_LOCKDOWN` + GUI probe | live 403 |
| S9 | P3 | Queue | `ORDER BY random()` + client skip bias | Deterministic / recorded skips | OPEN | not done | — |
| S10 | P3 | Ops | Lockdown process-wide; settings cached | Document restart | DOC-ONLY | runbook | — |
| S11 | — | Evidence | `73` overclaimed completeness | Honest residual | DOC-ONLY | `74` | — |
| S12 | — | Secrets | Passphrase in chat | Rotate | ACCEPTED-RISK / ops | — | — |
| S13 | — | Phase D | Agent “blind” treated as independence | Must not open gate | CORRECT BY DESIGN | allowlist excludes | SQL |
| S14 | — | Label write | Shared `LABEL_WRITE_TOKEN` | Per-human ideal | ACCEPTED-RISK | documented `74` | — |
| S15 | — | Observability | No Grafana validation dashboards | Metrics for independence | OPEN | none | — |
| S16 | — | AI providers | No multi-LLM validation | Autonomous independence | OPEN | PROMPT 4 | — |

Bugbot on final GUI: **no bugs** reported (`HISTORICAL CLAIM` from session).

---

## 8. ML readiness

| Question | Answer | Tag |
|----------|--------|-----|
| ML training components in repo? | No LightGBM/Optuna/SHAP stack | `PARTIALLY VERIFIED` (evidence 44 + deps) |
| Optional semantic model? | sentence-transformers setting; **disabled** live | `VERIFIED` |
| Labels usable for supervised ML as independent? | **No** — agent/audit + Phase D automation only for volume | `VERIFIED` |
| Gates open? | **No** | `PARTIALLY VERIFIED` |
| Decision | **NO-GO** for training | Aligns with evidence 44 + current SQL |

Do **not** start ML because 8.5M messages exist.

---

## 9. Human validation gap (critical)

| Layer | State |
|-------|--------|
| **INFRASTRUCTURE EXISTS** | Yes — samples, `label_reviews`, auth, HMAC, blind queue, GUI, lockdown, gates |
| **REAL INDEPENDENT VALIDATION DATA EXISTS** | **No** — `human1`/`human2` = 0 reviews |
| Phase D agent adjudication | Exists as **provisional methodology evidence**, not gate fuel |
| Blocker | Dependency on **actual independent validators** (human **or** future multi-AI layer with equal/stronger independence guarantees) |
| Safeguards to preserve | Fail-closed auth; blind attestation; contested exclusion; no overwrite of `human_labels`; reserved automation IDs excluded from independence |

User’s later PROMPT 4 intent: **replace ongoing human monitoring** with autonomous multi-AI validation — **not implemented**; must not silently equate AI-to-self with independence without provider diversity + blindness + provenance.

---

## 10. Commits of note (session-relevant)

| SHA | Summary |
|-----|---------|
| `91ef77b` | Merge PR #2 remote-verify hardening |
| `c4016f4` / collector milestone | Reprocess/collector hardening lineage |
| `22d9f65` | Independent blind-review layer + Phase D evidence |
| `0fd68fc` | opportunity_key reclaim |
| `dae088c` | Fail-closed independent auth + M1 honesty |
| `6f6f48a` | Runbook + evidence 60–74 |
| `acf7dce` | Env/secrets handout docs |
| `ed4792f` | Blind community_id=0 |
| `adeb1b5` | Phase E evidence SHA |
| **`394321b`** | Blind-review GUI + REVIEW_UI_LOCKDOWN |

---

## 11. Tests & runtime verification

| Check | Result | Tag |
|-------|--------|-----|
| `pytest` collect | 91 tests | `VERIFIED` |
| `tests/test_independent_review_api.py` | Passed in GUI ship session (13) | `HISTORICAL CLAIM` (not re-run this pass) |
| Health | ok | `VERIFIED` |
| GUI / lockdown | 200 / 403 | `VERIFIED` |
| Phase E | evidence 75 | `PARTIALLY VERIFIED` |
| Remote-verify 1k/10k | evidence 49/63/65 | `HISTORICAL CLAIM` |

---

## 12. Discrepancies: historical claims vs current reality

| Claim (historical) | Current reality | Tag |
|--------------------|-----------------|-----|
| Evidence 75: `label_reviews` only empty for humans; “independent_*=0” display | Still 0 allowlisted human reviews; but **130** total reviews exist (Phase D) | `VERIFIED` |
| Evidence 44: M1 numeric thresholds “PASS” on agent labels | Gate code now fail-closed on allowlist — agent volume **does not** open M1 | `VERIFIED` design change |
| Evidence 73 “all findings fixed” | Overclaim; use `74` | `VERIFIED` doc |
| “Ready for real human review” | Infra yes; **humans have not reviewed**; GUI ready | `VERIFIED` |
| Continuous commercial lead flow healthy | Ingestion healthy; **CRM HIGH volume tiny** (3 HIGH) | `VERIFIED` |
| Ops handoff said independent_reviews=0 | Compatible with allowlisted display; Phase D rows excluded | clarified |
| Short ops handoff uncommitted | Exists as dirty file; this audit handoff is canonical for PROMPT 3 | `VERIFIED` |
| Leads uniqueness / opportunity model | 5 leads present; 2 LOW technical leftovers remain in CRM table | `VERIFIED` |

---

## 13. Unfinished work & prioritized next steps

### Do NOT do yet

- Enable ML training / claim ML GO  
- Loosen scorer thresholds to manufacture volume  
- Wipe DB/volumes/cursors  
- Count Phase D / agent labels as independence  
- Commit secrets  
- Assume multi-prompt same-model “ensemble” = independent AI (PROMPT 4 forbids)

### Priority plan (dependency-aware)

1. **Immediate product decision:** Keep human GUI path **or** pivot to autonomous multi-AI validation (PROMPT 4). Both can share `label_reviews` abstraction if generalized carefully.
2. **If humans still used short-term:** Run `human1`/`human2` on `/review/` under lockdown; monitor SQL counts; do not disable lockdown mid-session without restart discipline.
3. **If AI validation (preferred per PROMPT 4):** Generalize validator identity; multi-provider providers; blind inputs; deterministic consensus; fail-closed diversity; provenance; metrics; tests — see sibling master prompt.
4. **Data quality / commercial:** Sales-validate 3 HIGH opportunities; clean or explain LOW rows in `leads`; continue bounded reprocess for `message_scores` coverage without wiping.
5. **Observability:** Add validation/gate metrics (Grafana currently missing).
6. **Docs:** Keep evidence honesty; prefer 74/75 over 73.

### Blockers

| Blocker | Type |
|---------|------|
| No allowlisted independent validation data | **Data / process** |
| Independence & M1 gates closed | **Gate** (correct) |
| No multi-AI validator runtime | **Feature gap** (PROMPT 4) |
| No Grafana independence dashboards | **Observability gap** |
| Lockdown vs operator tooling conflict | **Ops** |

---

## 14. File map (key paths)

| Path | Role |
|------|------|
| `config/scoring.yaml` | Commercial definition + rules |
| `shared/lead_write.py` | CRM write / opportunity_key |
| `shared/independent_review_auth.py` | Tokens / HMAC |
| `shared/settings.py` | Env including lockdown |
| `services/api/app/main.py` | API, gates, queue, lockdown |
| `services/api/static/review/index.html` | Human GUI |
| `services/analyzer/app/*` | Scoring consumer |
| `services/collector/*` | Ingestion |
| `alembic/versions/0005_human_labels.py` | CRM labels |
| `alembic/versions/0006_label_reviews.py` | Independent reviews |
| `docs/ops/PRODUCTION_RUNBOOK.md` | Ops |
| `docs/audit/evidence/44,74,75` | ML NO-GO / residual / Phase E |
| `docker-secrets/independent-reviewers.txt` | Human tokens (**gitignored**) |

---

## 15. Handoff quality statement

This document was produced **without implementing features**, using live verification where possible, and marking uncertainty explicitly. Prior `docs/ops/SESSION_HANDOFF_2026-09-23.md` is a shorter narrative; **prefer this file** for agent continuation. Transcript remains the raw source for exact wording.

**Next agent:** Re-run §1 verification commands before coding. Do not trust chat memory over SQL + git HEAD.

---

*End of HANDOFF_CURSOR_FULL_SESSION_2026-09-23.md*
