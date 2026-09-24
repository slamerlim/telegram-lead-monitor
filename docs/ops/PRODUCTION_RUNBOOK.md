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

- Commercial CRM is **AI-agentic**: AI qualifies candidates; humans handle external outreach and real outcomes.
- Do **not** wait for `ai_validation_gate_ready` or ML readiness.
- Never auto-DM. Never fabricate WON. Never loosen 100/30 or scorer thresholds.

### Enable commercial AI worker

```bash
# .env (do not commit secrets)
COMMERCIAL_AI_ENABLED=true
COMMERCIAL_AI_AUTO_PROMOTE=false   # shadow first
COMMERCIAL_AI_BACKEND=fake         # use sdk in production with CURSOR_API_KEY
COMMERCIAL_OPS_ENABLED=true
COMMERCIAL_OPERATOR_IDS=<commercial_ops_pilot>
LABEL_WRITE_TOKEN=<token>
```

```bash
docker compose up -d commercial-ai-worker api analyzer
docker compose exec -T api alembic upgrade head   # expect 0010_commercial_episodes (or 0009 if not yet migrated)
```

Operator queue: `GET /ops/queue` (AI_CONFIRMED first). Milestone: `first_25_ai_confirmed`.

### Commercial episode shadow experiment (NO OUTREACH)

Separate Redis streams: `telegram:commercial_discovery`, `telegram:commercial_episode_shadow`.

```bash
# .env — do not commit; defaults are false
COMMERCIAL_DISCOVERY_ENABLED=true
COMMERCIAL_EPISODE_SHADOW_ENABLED=true
COMMERCIAL_DISCOVERY_MAX_CANDIDATES_PER_HOUR=120
COMMERCIAL_EPISODE_MAX_REVIEWS_PER_HOUR=40
```

```bash
docker compose up -d commercial-discovery-worker commercial-episode-shadow-worker
docker compose exec -T api alembic upgrade head   # 0010_commercial_episodes
# Bounded paired A/B (fake first):
docker compose exec -T api python -m scripts.run_commercial_episode_shadow --limit 5 --backend fake
```

**NO OUTREACH IN THIS PHASE.** Do not enable CONTACT_ATTEMPT / RESPONSE / auto-DM for
episode confirmations. Do not promote episode reviews into production CRM leads.
Verify 0007 gates and `human_labels` unchanged after every stage.
