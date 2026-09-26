#!/usr/bin/env bash
# Event-only OBSERVE watcher for commercial discovery (post Evidence 117).
# Wakes the agent ONLY on Trigger A/B/C. Does not remesure, rewrite evidence,
# or alter discovery semantics.
#
# Usage (host, repo root):
#   INTERVAL_SEC=5400 ./scripts/observe_abc_watch.sh
#   ./scripts/observe_abc_watch.sh --once   # single check; exit 0 quiet / 10 on wake
#
# Wake line (stdout): AGENT_LOOP_WAKE_observe_abc {...json...}
# Quiet once line:    OBSERVE_OK {...json...}
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ONCE=0
if [[ "${1:-}" == "--once" ]]; then
  ONCE=1
fi

INTERVAL_SEC="${INTERVAL_SEC:-5400}"
PATH_B_AT="${PATH_B_AT:-2026-09-26T06:53:16Z}"
EXPECT_HL="${EXPECT_HL:-1524}"
EXPECT_AC="${EXPECT_AC:-4}"
PENDING_WARN="${PENDING_WARN:-50}"
LAG_WARN="${LAG_WARN:-100}"
# Score stall: messages in 1h above floor with zero scores → Trigger B.
MSG_1H_STALL_FLOOR="${MSG_1H_STALL_FLOOR:-20}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8010/health}"
STREAM="${STREAM:-telegram:commercial_discovery}"

psql_at() {
  docker compose exec -T postgres sh -c \
    "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -At -F, -c \"$1\""
}

redis_xlen() {
  docker compose exec -T redis redis-cli XLEN "$STREAM" | tr -d '\r'
}

redis_group_field() {
  # XINFO GROUPS prints alternating keys/values; pull one field for first group.
  local field="$1"
  docker compose exec -T redis redis-cli XINFO GROUPS "$STREAM" 2>/dev/null \
    | awk -v f="$field" '$0==f{getline; print; exit}' \
    | tr -d '\r'
}

evaluate_once() {
  local NOW TRIG HEALTH ROW HL AC NEW MSG1H SCORE1H XLEN CONSUMERS PENDING LAG
  NOW="$(date -u -Iseconds)"
  TRIG=""

  HEALTH="$(curl -sS -m 10 "$HEALTH_URL" 2>/dev/null || echo '{"status":"fail"}')"
  if ! echo "$HEALTH" | grep -q '"status":"ok"'; then
    TRIG="${TRIG}B_HEALTH "
  fi

  ROW="$(
    psql_at "SELECT
      (SELECT COUNT(*) FROM human_labels),
      (SELECT COUNT(*) FROM leads WHERE status='AI_CONFIRMED'),
      (SELECT COUNT(*) FROM commercial_discovery_candidates
         WHERE discovery_version='disc_v6' AND created_at >= '${PATH_B_AT}'),
      (SELECT COUNT(*) FROM messages WHERE created_at > now() - interval '1 hour'),
      (SELECT COUNT(*) FROM message_scores WHERE scored_at > now() - interval '1 hour')" \
      2>/dev/null || echo 'err,err,err,err,err'
  )"
  HL="$(echo "$ROW" | cut -d, -f1)"
  AC="$(echo "$ROW" | cut -d, -f2)"
  NEW="$(echo "$ROW" | cut -d, -f3)"
  MSG1H="$(echo "$ROW" | cut -d, -f4)"
  SCORE1H="$(echo "$ROW" | cut -d, -f5)"

  if [[ "$HL" != "$EXPECT_HL" || "$AC" != "$EXPECT_AC" ]]; then
    TRIG="${TRIG}C_ISOLATION "
  fi
  if [[ "$NEW" != "0" && "$NEW" != "err" ]]; then
    TRIG="${TRIG}A_NEW "
  fi
  if [[ "$MSG1H" =~ ^[0-9]+$ && "$SCORE1H" =~ ^[0-9]+$ ]]; then
    if (( MSG1H >= MSG_1H_STALL_FLOOR && SCORE1H == 0 )); then
      TRIG="${TRIG}B_SCORE_STALL "
    fi
  elif [[ "$ROW" == err,* ]]; then
    TRIG="${TRIG}B_QUERY "
  fi

  XLEN="$(redis_xlen 2>/dev/null || echo err)"
  CONSUMERS="$(redis_group_field consumers 2>/dev/null || echo err)"
  PENDING="$(redis_group_field pending 2>/dev/null || echo 0)"
  LAG="$(redis_group_field lag 2>/dev/null || echo 0)"

  if [[ "$CONSUMERS" == "0" ]]; then
    TRIG="${TRIG}B_NO_CONSUMER "
  fi
  if [[ "$PENDING" =~ ^[0-9]+$ ]] && (( PENDING > PENDING_WARN )); then
    TRIG="${TRIG}B_PENDING "
  fi
  if [[ "$LAG" =~ ^[0-9]+$ ]] && (( LAG > LAG_WARN )); then
    TRIG="${TRIG}B_LAG "
  fi

  if [[ -n "$TRIG" ]]; then
    printf '%s\n' "AGENT_LOOP_WAKE_observe_abc {\"at\":\"${NOW}\",\"triggers\":\"${TRIG}\",\"new\":${NEW},\"hl\":${HL},\"ac\":${AC},\"msg_1h\":${MSG1H},\"score_1h\":${SCORE1H},\"xlen\":${XLEN},\"pending\":${PENDING},\"lag\":${LAG},\"consumers\":${CONSUMERS},\"prompt\":\"OBSERVE trigger. Repo ${ROOT}. Follow docs/ops/COMMERCIAL_DISCOVERY_OBSERVE_RUNBOOK.md. A=freeze+quality NEW (no path reopen). B=correctness/observability. C=halt isolation. Evidence+commit-push if justified.\"}"
    return 10
  fi

  printf '%s\n' "OBSERVE_OK {\"at\":\"${NOW}\",\"new\":${NEW},\"hl\":${HL},\"ac\":${AC},\"msg_1h\":${MSG1H},\"score_1h\":${SCORE1H},\"xlen\":${XLEN},\"pending\":${PENDING},\"lag\":${LAG},\"consumers\":${CONSUMERS}}"
  return 0
}

if [[ "$ONCE" -eq 1 ]]; then
  set +e
  evaluate_once
  rc=$?
  set -e
  exit "$rc"
fi

echo "observe_abc_watch start interval=${INTERVAL_SEC}s path_b=${PATH_B_AT} expect=${EXPECT_HL}/${EXPECT_AC}" >&2

while true; do
  sleep "$INTERVAL_SEC"
  set +e
  evaluate_once
  rc=$?
  set -e
  # Loop continues whether quiet (0) or wake printed (10).
  if [[ "$rc" -ne 0 && "$rc" -ne 10 ]]; then
    echo "observe_abc_watch: unexpected evaluate_once exit ${rc}" >&2
  fi
done
