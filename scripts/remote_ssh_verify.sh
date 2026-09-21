#!/usr/bin/env bash
# Production / Remote-SSH verification after merging to main.
# Safe defaults: never uses docker compose down -v, never truncates tables.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Load compose credentials when present (do not override already-exported vars).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

MAX_MESSAGES="${MAX_MESSAGES:-1000}"
BATCH_SIZE="${BATCH_SIZE:-250}"
RUN_10K="${RUN_10K:-0}"
RESTART_SCHEDULER="${RESTART_SCHEDULER:-0}"
PG_USER="${POSTGRES_USER:-telegram_leads}"
PG_DB="${POSTGRES_DB:-telegram_leads}"

psql_q() {
  docker compose exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 "$@"
}

dup_count() {
  psql_q -Atc \
    "SELECT COUNT(*) FROM (SELECT message_id FROM leads GROUP BY message_id HAVING COUNT(*) > 1) d;"
}

assert_no_dups() {
  local label="$1"
  local dups
  dups="$(dup_count | tr -d '[:space:]')"
  if [[ -z "$dups" || ! "$dups" =~ ^[0-9]+$ ]]; then
    echo "ERROR: could not read duplicate lead count after ${label}"
    exit 1
  fi
  echo "DUP_CHECK_${label}=${dups}"
  if [[ "$dups" != "0" ]]; then
    echo "ERROR: found ${dups} duplicate message_id groups in leads after ${label}"
    exit 1
  fi
}

run_timed() {
  if command -v /usr/bin/time >/dev/null 2>&1; then
    /usr/bin/time -f 'WALL_SEC=%e' "$@"
  else
    local start end
    start="$(date +%s)"
    "$@"
    end="$(date +%s)"
    echo "WALL_SEC=$((end - start))"
  fi
}

echo "== preflight =="
command -v docker >/dev/null || { echo "ERROR: docker not found on this host"; exit 1; }
docker compose version >/dev/null || { echo "ERROR: docker compose not found"; exit 1; }
test -f docker-compose.yml || { echo "ERROR: run from telegram-lead-monitor repo root"; exit 1; }
# Refuse destructive volume wipe flags if someone wraps this script incorrectly.
if [[ "${ALLOW_COMPOSE_DOWN_V:-0}" == "1" ]]; then
  echo "ERROR: ALLOW_COMPOSE_DOWN_V is set; refusing to run (never wipe volumes)"
  exit 1
fi

echo "== git =="
if [[ "${SKIP_GIT:-0}" == "1" ]]; then
  echo "SKIP_GIT=1 — using current checkout $(git rev-parse --short HEAD)"
  git status -sb
else
  git fetch origin
  git checkout main
  git pull origin main
  git rev-parse --short HEAD
  git status -sb
fi

echo "== disk =="
df -h / || true
docker system df || true

echo "== stop scheduler for isolated smoke =="
docker compose stop scheduler || true

echo "== rebuild images that bake scripts/services =="
docker compose build analyzer collector api
# Never: docker compose down -v
docker compose up -d postgres redis api analyzer collector

echo "== wait for postgres healthy =="
ready=0
for _ in $(seq 1 40); do
  if docker compose exec -T postgres pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "$ready" != "1" ]]; then
  echo "ERROR: postgres not ready"
  exit 1
fi

echo "== ensure schema (never drops) =="
messages_reg="$(psql_q -Atc "SELECT to_regclass('public.messages');" | tr -d '[:space:]')"
alembic_reg="$(psql_q -Atc "SELECT to_regclass('public.alembic_version');" | tr -d '[:space:]')"
if [[ -z "$messages_reg" ]]; then
  echo "messages table missing — create_all + alembic stamp head"
  docker compose exec -T api python -m services.api.app.init_db
  docker compose exec -T api alembic stamp head
elif [[ -z "$alembic_reg" ]]; then
  echo "messages present without alembic_version — stamp 0001_initial then upgrade"
  docker compose exec -T api alembic stamp 0001_initial
  docker compose exec -T api alembic upgrade head
else
  echo "messages + alembic_version present — alembic upgrade head"
  docker compose exec -T api alembic upgrade head
fi

echo "== confirm --max-messages in analyzer image =="
docker compose exec -T analyzer python scripts/reprocess_messages.py --help | grep -F -- '--max-messages'

echo "== redis pending (scan + messages) =="
docker compose exec -T redis redis-cli XINFO GROUPS telegram:scan_requests || true
docker compose exec -T redis redis-cli XINFO GROUPS telegram:messages || true

echo "== DB baseline =="
psql_q -c \
  "SELECT COUNT(*) AS messages FROM messages; SELECT COUNT(*) AS leads FROM leads; SELECT COUNT(*) AS dup FROM (SELECT message_id FROM leads GROUP BY message_id HAVING COUNT(*) > 1) d;"
assert_no_dups baseline

echo "== reprocess smoke max=${MAX_MESSAGES} batch=${BATCH_SIZE} =="
run_timed docker compose exec -T analyzer \
  python scripts/reprocess_messages.py \
  --max-messages "${MAX_MESSAGES}" \
  --batch-size "${BATCH_SIZE}"

echo "== DB after smoke =="
psql_q -c \
  "SELECT COUNT(*) AS leads FROM leads; SELECT COUNT(*) AS dup FROM (SELECT message_id FROM leads GROUP BY message_id HAVING COUNT(*) > 1) d; SELECT status, COUNT(*) FROM leads GROUP BY status ORDER BY 1; SELECT tier, COUNT(*) FROM leads GROUP BY tier ORDER BY 1;"
assert_no_dups smoke_${MAX_MESSAGES}

if [[ "${RUN_10K}" == "1" ]]; then
  echo "== reprocess 10k =="
  run_timed docker compose exec -T analyzer \
    python scripts/reprocess_messages.py \
    --max-messages 10000 \
    --batch-size "${BATCH_SIZE}"
  echo "== DB after 10k =="
  psql_q -c \
    "SELECT COUNT(*) AS leads FROM leads; SELECT COUNT(*) AS dup FROM (SELECT message_id FROM leads GROUP BY message_id HAVING COUNT(*) > 1) d; SELECT status, COUNT(*) FROM leads GROUP BY status ORDER BY 1; SELECT tier, COUNT(*) FROM leads GROUP BY tier ORDER BY 1;"
  assert_no_dups smoke_10000
fi

if [[ "${RESTART_SCHEDULER}" == "1" ]]; then
  echo "== restart scheduler =="
  docker compose start scheduler
else
  echo "NOTE: scheduler left stopped; start with: docker compose start scheduler"
  echo "      or re-run with RESTART_SCHEDULER=1"
fi

echo "REMOTE_SSH_VERIFY_OK"
