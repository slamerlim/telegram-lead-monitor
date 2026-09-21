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

echo "== preflight =="
command -v docker >/dev/null || { echo "ERROR: docker not found on this host"; exit 1; }
docker compose version >/dev/null || { echo "ERROR: docker compose not found"; exit 1; }
test -f docker-compose.yml || { echo "ERROR: run from telegram-lead-monitor repo root"; exit 1; }

echo "== git =="
git fetch origin
git checkout main
git pull origin main
git rev-parse --short HEAD
git status -sb

echo "== disk =="
df -h / || true
docker system df || true

echo "== stop scheduler for isolated smoke =="
docker compose stop scheduler || true

echo "== rebuild images that bake scripts/services =="
docker compose build analyzer collector api
docker compose up -d postgres redis api analyzer collector

echo "== wait for postgres healthy =="
for _ in $(seq 1 40); do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-telegram_leads}" -d "${POSTGRES_DB:-telegram_leads}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "== confirm --max-messages in analyzer image =="
docker compose exec -T analyzer python scripts/reprocess_messages.py --help | grep -F -- '--max-messages'

echo "== redis pending (scan + messages) =="
docker compose exec -T redis redis-cli XINFO GROUPS telegram:scan_requests || true
docker compose exec -T redis redis-cli XINFO GROUPS telegram:messages || true

echo "== DB baseline =="
docker compose exec -T postgres \
  psql -U "${POSTGRES_USER:-telegram_leads}" -d "${POSTGRES_DB:-telegram_leads}" -c \
  "SELECT COUNT(*) AS messages FROM messages; SELECT COUNT(*) AS leads FROM leads; SELECT COUNT(*) AS dup FROM (SELECT message_id FROM leads GROUP BY message_id HAVING COUNT(*) > 1) d;"

echo "== reprocess smoke max=${MAX_MESSAGES} batch=${BATCH_SIZE} =="
/usr/bin/time -f 'WALL_SEC=%e' docker compose exec -T analyzer \
  python scripts/reprocess_messages.py \
  --max-messages "${MAX_MESSAGES}" \
  --batch-size "${BATCH_SIZE}"

echo "== DB after smoke =="
docker compose exec -T postgres \
  psql -U "${POSTGRES_USER:-telegram_leads}" -d "${POSTGRES_DB:-telegram_leads}" -c \
  "SELECT COUNT(*) AS leads FROM leads; SELECT COUNT(*) AS dup FROM (SELECT message_id FROM leads GROUP BY message_id HAVING COUNT(*) > 1) d; SELECT status, COUNT(*) FROM leads GROUP BY status ORDER BY 1; SELECT tier, COUNT(*) FROM leads GROUP BY tier ORDER BY 1;"

if [[ "${RUN_10K}" == "1" ]]; then
  echo "== reprocess 10k =="
  /usr/bin/time -f 'WALL_SEC=%e' docker compose exec -T analyzer \
    python scripts/reprocess_messages.py \
    --max-messages 10000 \
    --batch-size "${BATCH_SIZE}"
fi

echo "REMOTE_SSH_VERIFY_OK"
echo "When ready: docker compose start scheduler"
