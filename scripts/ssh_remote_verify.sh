#!/usr/bin/env bash
# Run production remote-verify over SSH when PRODUCTION_SSH_* secrets are set.
# Never uses docker compose down -v. Prefer My Machines worker when available.
set -euo pipefail

HOST="${PRODUCTION_SSH_HOST:?PRODUCTION_SSH_HOST required}"
USER_NAME="${PRODUCTION_SSH_USER:?PRODUCTION_SSH_USER required}"
PORT="${PRODUCTION_SSH_PORT:-22}"
REPO_PATH="${PRODUCTION_REPO_PATH:-~/telegram-lead-monitor}"
REF="${PRODUCTION_GIT_REF:-cursor/remote-verify-hardening-bf0e}"
RUN_10K="${RUN_10K:-1}"
RESTART_SCHEDULER="${RESTART_SCHEDULER:-1}"

KEY_FILE="$(mktemp)"
cleanup() { rm -f "$KEY_FILE"; }
trap cleanup EXIT

if [[ -n "${PRODUCTION_SSH_PRIVATE_KEY:-}" ]]; then
  printf '%s\n' "$PRODUCTION_SSH_PRIVATE_KEY" >"$KEY_FILE"
  chmod 600 "$KEY_FILE"
  SSH=(ssh -i "$KEY_FILE" -p "$PORT" -o StrictHostKeyChecking=accept-new -o BatchMode=yes)
else
  echo "ERROR: PRODUCTION_SSH_PRIVATE_KEY required"
  exit 1
fi

TARGET="${USER_NAME}@${HOST}"
echo "== ssh remote-verify target=${TARGET} repo=${REPO_PATH} ref=${REF} =="

"${SSH[@]}" "$TARGET" bash -s <<EOF
set -euo pipefail
cd ${REPO_PATH}
git fetch origin
git checkout ${REF}
git pull --ff-only origin ${REF} || git pull --ff-only
export SKIP_GIT=1 REQUIRE_REAL_DATA=1 MAX_MESSAGES=1000 BATCH_SIZE=250
export RUN_10K=0 RESTART_SCHEDULER=0
bash scripts/remote_ssh_verify.sh
if [[ "${RUN_10K}" == "1" ]]; then
  export RUN_10K=1 RESTART_SCHEDULER=${RESTART_SCHEDULER}
  bash scripts/remote_ssh_verify.sh
fi
EOF

echo "SSH_REMOTE_VERIFY_DONE"
