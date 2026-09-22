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

Automation prefixes (`agent`, `audit`, `smoke`, …) match as exact id or `prefix_…` only (so `auditor` is allowed).

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
