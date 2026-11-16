#!/usr/bin/env bash
# Invokes the runtime on a new session and prints the answer and the time it took.
# Usage: ./invoke.sh [payload.json]
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
source "${STATE_FILE}" 2>/dev/null || die "no runtime in ${REGION}: run ./deploy.sh with the same REGION"

PAYLOAD="${1:-${SCRIPT_DIR}/payload.json}"
SESSION_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
START="$(date +%s)"
OUT="$("${PY}" "${BOTO}" invoke --region "${REGION}" \
  --agent-runtime-arn "arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${RUNTIME_ID}" \
  --session-id "${SESSION_ID}" --payload-file "${PAYLOAD}")" \
  || die "session ${SESSION_ID} failed after $(( $(date +%s) - START ))s (error above)"
echo "${OUT}" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["body"], indent=2))'
log "session ${SESSION_ID}: $(( $(date +%s) - START ))s"
