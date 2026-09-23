# MASTER CURSOR IMPLEMENTATION PROMPT — Autonomous Multi-AI Independent Validation

**Paste this entire document into a Cursor coding agent as the task brief.**  
**Derived from:** `docs/audit/HANDOFF_CURSOR_FULL_SESSION_2026-09-23.md` (verify live state before coding).  
**Date:** 2026-09-23  

---

You are continuing work on the production repository:

`/home/slamer-lim/telegram-lead-monitor`

You are on the Ubuntu production host. Do **not** use a Cloud Agent VM as a substitute for this host’s Docker/Postgres data.

## Absolute constraints

1. Do **not** run `docker compose down -v`, truncate tables, delete volumes, wipe Telegram messages, or destroy collector incremental cursors.
2. Do **not** commit secrets (`.env`, `docker-secrets/*`, API keys).
3. Do **not** loosen scorer thresholds in `config/scoring.yaml` to inflate leads or validation volume.
4. Do **not** enable supervised ML training or claim ML GO until the **new** independent AI-validation gates are honestly satisfied.
5. Do **not** overwrite `human_labels` from the validation path.
6. Do **not** treat existing `agent_*` / `audit_*` / `blind_adjudicator_phaseD` rows as independent validation.
7. Do **not** invent a second unrelated commercial-lead definition. Use `config/scoring.yaml` header objectives only.
8. Do **not** claim multi-AI independence if multiple prompts hit the **same** model/provider silently.
9. Prefer generalizing existing `label_reviews` / blind-queue / HMAC / gate infrastructure over cloning a parallel schema—unless inspection proves generalization is unsafe.
10. Before editing: inspect schemas, migrations, API, settings, tests, compose, live DB counts, and the handoff discrepancies section.

## Product goal (non-negotiable)

Replace the **ongoing human monitoring / clicking** dependency with an **autonomous multi-AI independent validation layer** that decides whether messages are genuine commercial opportunities under the project’s existing definition.

The human GUI at `/review/` may remain for diagnostics/audit, but **production progress must not require humans to review messages**.

This is **not** renaming `human1` → `ai1`. Independence, blindness, provider diversity, provenance, fail-closed gates, and auditable structured outputs are mandatory.

## Commercial-lead definition (copy exactly; do not broaden)

From `config/scoring.yaml`:

1. buy/commission a trading bot  
2. fix/repair an existing trading bot  
3. hire a trading-system developer (contract/project)  
4. hire a quantitative engineer (contract/project)  
5. hire an ML/AI engineer (contract/project)  
6. implement/automate a trading strategy  

Technical relevance ≠ commercial intent. Support/API troubleshooting, generic trading chat, vacancies, marketing broadcasts, and ambiguous chatter are **not** target leads.

## Current verified baseline (re-check on start)

Expect approximately (handoff VERIFIED 2026-09-23; re-query):

- HEAD on `main` around `394321b` (GUI + lockdown) unless newer commits exist  
- ~8.5M messages; ~5 `leads`; ~40k `message_scores`; 1524 agent/audit `human_labels`  
- Sample `indep_review_2026-09-22` = 128  
- `label_reviews`: Phase D automation present; **`human1`/`human2` = 0**  
- Independence / M1 gates closed by design  
- `REVIEW_UI_LOCKDOWN` may be true (blocks `/labels/stats`); do not break ingestion to “fix” stats  
- No Grafana stack in compose today  
- No multi-LLM provider integration today; `SEMANTIC_ENABLED=false`

## Architecture analysis instructions (do this first; write findings in evidence)

Inspect and decide **reuse vs redesign**:

| Existing asset | Likely reuse |
|----------------|--------------|
| `label_review_samples` | Candidate sampling / strata |
| `label_reviews` | Append-only validator opinions (generalize `reviewer_id` → validator identity) |
| Blind queue + HMAC `review_token` | Pattern for binding blind protocol to writes |
| Independence allowlist + secrets | Pattern for fail-closed validator registry (must not be self-issued by scorer process without ops config) |
| Gate logic in `/labels/stats` | Extend with AI-independence criteria; keep fail-closed |
| `human_labels` | Leave as CRM/provisional layer; do not launder AI into it silently |
| GUI `/review/` | Optional audit UI; not the production validator |
| Scorer / analyzer | Remains production decision engine; **inputs must be hidden from blind validators** |

Document which components you will generalize and why. Prefer one validation plane.

## Target end-to-end flow

```text
Telegram message
  → persisted messages
  → production scorer/analyzer (unchanged thresholds)
  → validation candidate sampler (configurable; may reuse strata / new continuous sampler)
  → blind AI validation job(s)
       → Mode A classifier (provider/model 1)
       → Mode B skeptical FP reviewer (provider/model 2)
       → Mode C commercial-intent verifier (provider/model 3)
  → disagreement analysis
  → optional Mode D adjudicator (only after blind results; sees message + anonymized validator outputs; NOT production score)
  → deterministic consensus → validation state
  → analytics / CRM eligibility / ML-gate inputs (provenance-filtered)
```

Blind validators must receive **minimum** fields: message text (+ maybe language-agnostic length metadata if already required). They must **not** receive: production score/tier/decision, veto reasons, predicted label, other validators’ answers (until adjudicator), community identity if it encodes stratum leakage, prior human/agent labels.

## Validation roles (implement all)

### Mode A — Independent commercial-lead classifier

Structured output (example fields; align names to schema you design):

- `is_target_lead` (bool or enum)
- `lead_type` (from existing project types only when positive)
- `confidence` (0–1)
- `evidence` (short quotes/spans from the message)
- `uncertain` (bool)
- `rationale_short` (non-CoT, auditable)

### Mode B — Skeptical false-positive reviewer

Aggressively argue **against** lead status: support, API help, hypothetics, self-promo, job posts, generic chat, ambiguous intent.

### Mode C — Commercial-intent verifier

Independently check buyer/employer/client intent against the six commercial objectives only.

### Mode D — Independent adjudicator

Runs only after A/B/C complete. Input: message text + anonymized validator outputs (no provider-identifying leakage that reveals “which is production”). Must preserve disagreement; must not force TRUE.

## Validation states (uncertainty must not become positive)

Use explicit states, reusing project vocabulary where sensible:

- `VALIDATED_TRUE` / `HUMAN_REVIEWED_TRUE`-compatible mapping only if policy says so  
- `VALIDATED_FALSE`  
- `CONTESTED`  
- `UNCERTAIN`  
- `INSUFFICIENT_EVIDENCE`  
- `FAILED_VALIDATION`  
- `VALIDATION_ERROR`  

**Hard rule:** contested / uncertain / error / insufficient → **never** count as positive for ML/commercial gates.

## Multi-provider / multi-model diversity (fail closed)

1. Create a provider abstraction: configure `validator_slot → provider + model + mode + prompt_version`.
2. Require **real** diversity: distinct providers and/or materially different model families as configured (e.g. different commercial APIs and/or a local open model).  
3. **Forbidden:** three prompts on one model silently presented as three independents.
4. Persist per result: provider, model, mode, prompt_version, config_version, request_id, timestamp, latency, success/error, structured result, confidence, evidence.
5. If required providers/credentials are missing → **fail closed** (gate stays shut; emit clear metric/reason). Do not substitute duplicates.

Env/config should look conceptually like (names yours to choose):

```text
AI_VALIDATORS=a:openai:gpt-…,b:anthropic:claude-…,c:local:… 
AI_VALIDATION_REQUIRE_DISTINCT_PROVIDERS=true
```

Never commit real keys; use `.env` / secret files gitignored.

## Consensus policy (deterministic, configurable)

Implement configurable thresholds with documented rationale, for example:

- minimum validators completed  
- minimum distinct providers/families  
- agreement rules for TRUE vs FALSE  
- confidence floors  
- when adjudicator is required  
- max error rate / partial failure behavior  

Report counts: unanimous true, majority true, contested, false, uncertain, unable to validate.

Do **not** invent thresholds solely to trip old `ml_gate_independence_ready` numbers. Either:

- generalize that gate to “independent validator allowlist including AI validator IDs with diversity requirements”, or  
- add a new gate field and keep the old human gate honest.

Prefer honesty over green checkmarks.

## Provenance & anti-circularity

Prevent: scorer → AI → train → scorer → AI confirmation loops.

1. Never overwrite original `human_labels` / prior reviews silently.  
2. Store AI outputs as first-class rows (generalized `label_reviews` or new tables linked by `validation_run_id`).  
3. Persist: source label (if any), each validator output, consensus, adjudicator, provenance, timestamps, model/provider/prompt versions, run id, blindness flags.  
4. ML/data gates consume **only** rows meeting independence + diversity + blindness + non-contested policy.  
5. Production scorer outputs remain separately queryable and must not be mixed into “independent TRUE” without provenance filters.

## AuthZ for validators

- Scorer/analyzer must **not** be able to mint allowlisted independent validator identities at runtime without operator configuration.
- Reuse fail-closed secret patterns (HMAC/tokens/service credentials) adapted for service accounts.
- Reserved prefixes (`agent`, `audit`, `smoke`, `blind_adjudicator`, …) must remain excluded from independence counts unless explicitly redesigned with equal rigor.

## Autonomy / no human monitoring

Implement workers (scheduler/analyzer sidecar or new service) that automatically:

- sample candidates  
- enqueue blind validation  
- call providers with retries/timeouts/rate limits  
- aggregate consensus  
- quarantine contested  
- update stats  
- expose why gates are closed  

GUI optional for audit only.

## Observability

There is **no** Grafana in compose today. Add the minimum viable path (Prometheus metrics endpoint and/or Grafana compose profile) showing:

- validation intake/throughput/backlog  
- per-provider latency/errors  
- agreement/disagreement/contested rates  
- TRUE/FALSE/UNCERTAIN distribution  
- provider diversity satisfaction  
- gate open/closed **and exact closed reasons**  
- cost proxies if available  

Dashboards must make “configured but not running” obvious.

## Testing (mandatory categories)

Add tests for:

- blind-input enforcement (adversarial: attempt to inject score/tier into validator prompt builder—must fail)  
- provider diversity fail-closed  
- model identity persistence  
- provenance / immutability  
- deterministic consensus  
- contested / uncertain never → positive gate  
- provider failure / timeout / malformed JSON  
- duplicate review / replay / concurrency  
- migration safety  
- API authZ  
- runtime integration smoke (non-destructive)

Run relevant pytest; record results in `docs/audit/evidence/`.

## Cost / performance

Support configurable sampling, async jobs, retries, per-provider rate limits, timeouts, dedupe by message+run, backpressure. Do not sacrifice blindness/diversity for cost.

## Acceptance criteria (do not declare done early)

1. ≥3 validator modes operate with **recorded** distinct provider/model identities when configured.  
2. Blindness enforced in code + tested adversarially.  
3. Deterministic consensus + preserved disagreement.  
4. AI validation cannot overwrite/launder `human_labels`.  
5. Provenance-complete storage.  
6. Gates use only eligible independent validation data; fail closed on diversity/blindness/errors.  
7. System runs without humans clicking `/review/` for routine validation.  
8. Tests cover critical integrity cases and pass.  
9. Non-destructive runtime verification on this host proves pipeline on a small sample (prefer existing `indep_review_2026-09-22` or a new clearly named batch—**do not** delete Phase D rows; use new reviewer_ids / run ids).  
10. Metrics expose validation health + gate closed reasons.  
11. Docs explain independence precisely (update runbook + new evidence file).  
12. Final report lists providers used, coverage, gate state, blockers, next steps.

## Implementation order

1. Re-verify live git/compose/SQL/health (update evidence).  
2. Design doc + schema/migration plan (reuse analysis).  
3. Provider abstraction + fake/stub providers for tests.  
4. Blind prompt builder + adversarial tests.  
5. Persistence + consensus engine.  
6. Worker/scheduler integration.  
7. Gate updates (fail closed).  
8. Metrics.  
9. Controlled live smoke with real providers **if** keys present; else prove fail-closed + stub path.  
10. Evidence + runbook + final report.

## Final report format (required)

- What was implemented (paths, migrations, env keys names-only)  
- What was verified (commands + results)  
- Tests run + outcomes  
- Runtime smoke results  
- Validation coverage + diversity status  
- Gate states + closed reasons  
- Remaining blockers  
- Exact next steps  

Remember: the ultimate goal is an autonomous, multi-AI, independently validated commercial-lead pipeline that continuously operates **without** requiring the product owner to manually inspect Telegram messages—while remaining fail-closed and non-circular.
