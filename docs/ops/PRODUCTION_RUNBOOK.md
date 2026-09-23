# Production Runbook — telegram-lead-monitor

Host: Ubuntu Compose stack at `/home/slamer-lim/telegram-lead-monitor`  
API: `127.0.0.1:8010`  
Services: `api`, `collector`, `analyzer`, `scheduler`, `postgres`, `redis`

## Safety (never)

- `docker compose down -v`
- Truncate `messages` / `leads` / label tables
- Wipe `postgres_data`, `redis_data`, or `telegram_session`
- Blind Redis `XACK` without proving stream entries are gone
- Commit `.env`, Telethon `*.session`, keys, tokens
- Full-corpus reprocess without bounded 1k → 10k measurement
- ML training while independence gate is false

## Preflight

```bash
df -h
docker system df
docker compose ps
git status -sb
git rev-parse --short HEAD
```

## Deploy / upgrade

```bash
git fetch origin
git checkout main
git pull --ff-only origin main

docker compose build api analyzer collector scheduler
docker compose up -d api analyzer collector scheduler

docker compose exec -T api alembic current
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic current

curl -sS http://127.0.0.1:8010/health
curl -sS http://127.0.0.1:8010/labels/stats
```

Rollback (no volume wipe):

```bash
git checkout <previous_sha>
docker compose build api analyzer collector scheduler
docker compose up -d api analyzer collector scheduler
# Prefer forward-fix migrations; only alembic downgrade if migration is known-safe
```

## Verify

Local production-like:

```bash
REQUIRE_REAL_DATA=1 MAX_MESSAGES=1000 BATCH_SIZE=250 \
  bash scripts/remote_ssh_verify.sh
```

Or GitHub Actions workflow `remote-verify` on self-hosted runner `telegram-lead-monitor`, `ref=main`.

Bounded reprocess:

```bash
docker compose exec -T analyzer \
  python scripts/reprocess_messages.py --max-messages 1000 --batch-size 250
```

## Scheduler control

```bash
docker compose stop scheduler
# ... controlled experiments ...
docker compose start scheduler
```

Check work via Postgres `scan_runs` and Redis groups — not logs alone.

## Redis PEL playbook

1. `XPENDING <stream> <group>`
2. Inspect consumer and referenced IDs
3. Confirm entries still exist (`XRANGE` / `XACK` only if gone)
4. Never ACK live pending work blindly

## Collector FloodWait / expired invite

- FloodWait is handled with buffer; historical FloodWait ≠ current bug
- Expired invite → `completed_with_errors` for that community only

## Independence human allowlist

All three are required or independent queue/POST return **503** and gates stay closed:

```bash
INDEPENDENT_HUMAN_REVIEWER_IDS=alice,bob
INDEPENDENT_HUMAN_REVIEWER_TOKENS=alice:replace-me,bob:replace-me
INDEPENDENT_REVIEW_HMAC_SECRET=replace-with-long-random
```

- Queue + `POST /labels/reviews` require `X-Reviewer-Token`. Partial config (IDs without tokens, or tokens without HMAC) does **not** enable writes.
- Queue is **blind-only** (`blind=false` → 403). Returns `review_token` (HMAC). POST must echo it; server sets shown flags from attestation (client flags ignored). Upsert never lowers shown flags.
- Displayed `independent_*` and `ml_gate_independence_ready` share the same ready gate (tokens+HMAC+allowlist).
- Contested messages (any allowlisted blind FALSE) do not count toward the TRUE threshold.
- Preflight before enabling IDs: `SELECT reviewer_id, count(*) FROM label_reviews WHERE reviewer_id IN (...) GROUP BY 1` must be empty for those humans.
- Deploy API only from a **pushed** git SHA (`git pull --ff-only` then rebuild).
- Operator credentials (generated locally): `docker-secrets/independent-reviewers.txt` (gitignored). Never commit tokens.
- **Reviewer GUI:** open `http://127.0.0.1:8010/review/` — enter reviewer ID + token from the handout; default batch `indep_review_2026-09-22`. Blind-only; does not modify `human_labels`.
- Set `REVIEW_UI_LOCKDOWN=true` during human sessions so `/search`, `/leads`, `/docs`, etc. return 403 (same-origin blinding). Disable only for operators who need the full API. **Restart the API** after changing this flag (`get_settings` is process-cached).

Automation prefixes (`agent`, `audit`, `smoke`, `aival`, …) match as exact id or `prefix_…` only (so `auditor` is allowed). AI validators use `aival_*` and are **excluded** from human independence counts.

## Cursor SDK-mediated multi-model validation

Roster: `config/ai_validators.yaml` (Cursor SDK model ids + distinct `model_family` for A/B/C).
Orchestration uses the Cursor SDK; this app does not call Anthropic/OpenAI/xAI HTTP APIs directly.

Secrets (prefer gitignored `.env.validation` mounted on API only):

```bash
AI_VALIDATOR_IDS=aival_a_claude_opus,aival_b_gpt56,aival_c_grok47,aival_d_composer
AI_VALIDATOR_TOKENS=aival_a_claude_opus:…,…
AI_VALIDATION_QUEUE_HMAC_SECRET=…
AI_VALIDATION_ROW_SECRET=…
```

- Endpoints: `/validation/queue`, `/validation/results`, `/validation/consensus/recompute`, `/validation/gates`, `/metrics` (allowed under `REVIEW_UI_LOCKDOWN`).
- Orchestrator: `python -m services.validator.app.orchestrator --backend fake|sdk --sample-batch-id indep_review_2026-09-22`
- `--backend sdk` needs `CURSOR_API_KEY`; never commit it. Fake/synthetic consensus does **not** open `ai_validation_gate_ready`.
- Ad-hoc Task subagents: see `docs/ops/AI_VALIDATION_TASK_ADHOC.md` (same API plane).
- `ml_training_enabled` stays false; do not claim ML GO from AI smoke.

## Human labels / M1 gate

```bash
HUMAN_LABEL_REVIEWER_IDS=alice,bob   # both required with LABEL_WRITE_TOKEN
LABEL_WRITE_TOKEN=replace-me         # required for M1/positive gates; POST /labels needs X-Label-Token
```

Empty either ⇒ `ml_gate_m1_ready` / `ml_gate_positive_ready` stay false. Overwriting another `labeled_by` on the same message → **409** (preserves agent/audit provenance). Shared write token still lets any holder claim any allowlisted `labeled_by` — treat as operator trust, not per-human auth.

Reserved `labeled_by` prefixes are rejected on `POST /labels`. Agent/audit provisional rows continue via DB scripts.

## Commercial honesty

- Scorer HIGH ≠ sales-validated prospect
- `human_labels` agent/audit rows are provisional
- ML stays NO-GO until blind independent *human* reviews clear the independence gate

## Backup (non-destructive)

```bash
df -h
mkdir -p /var/backups/telegram-lead-monitor
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
  > /var/backups/telegram-lead-monitor/pg_$(date -u +%Y%m%dT%H%M%SZ).dump
```

Redis AOF + Docker volumes are the Redis/session recovery path; do not delete volumes to “clean up”.

## Commercial Production Pilot

See also: `docs/business/COMMERCIAL_PILOT.md`

### Policy

- Do **not** wait for `ai_validation_gate_ready` or ML readiness to run CRM.
- AI validation is advisory; human review is commercial truth; customer response is ground truth.
- Never auto-DM. Never fabricate WON. Never loosen 100/30 or scorer thresholds for volume.

### Enable (fail-closed)

```bash
# In .env (do not commit secrets):
COMMERCIAL_OPS_ENABLED=true
LABEL_WRITE_TOKEN=<shared-token>
# Prefer a dedicated commercial operator id (not used for blind independent review):
COMMERCIAL_OPERATOR_IDS=<commercial_ops_pilot>
# Fallback if COMMERCIAL_OPERATOR_IDS empty: HUMAN_LABEL_REVIEWER_IDS
# (must not overlap INDEPENDENT_HUMAN_REVIEWER_IDS — else 409)
HUMAN_LABEL_REVIEWER_IDS=<optional-fallback>
```

Redeploy API after env change. Confirm:

```bash
curl -sS -H "X-Operator-Id: <id>" -H "X-Label-Token: <token>" \
  http://127.0.0.1:8010/ops/health
```

With lockdown on, `/ops/*` remains reachable only when `COMMERCIAL_OPS_ENABLED=true`.

### Daily operator loop

1. `/ops/health` + `/health` + Redis lag
2. `GET /ops/candidates`
3. Human qualify → promote / QUALIFIED or REJECTED events
4. Manual outreach using message/author URLs
5. Record CONTACT_ATTEMPT → RESPONSE → WON/LOST
6. `GET /ops/metrics/funnel` and `/ops/metrics/milestone`
7. Do not retrain ML from small samples

### First-25 + 7-day pilot

Documented in `docs/business/COMMERCIAL_PILOT.md`. Classification A–F is evidence, not marketing.

### Score coverage backfill (optional, same rules)

```bash
docker compose exec -T analyzer \
  python scripts/reprocess_messages.py \
  --since-days 7 --only-unscored --max-messages 1000 --batch-size 250
```

Measure created/updated/deleted between stages. Abort on lead explosion or Redis lag.
