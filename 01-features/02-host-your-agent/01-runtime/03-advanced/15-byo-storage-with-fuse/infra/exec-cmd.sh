#!/usr/bin/env bash
# Runs a raw shell command inside an already-invoked session's running
# container, via ExecuteCommand -- bypasses server.js and the Strands
# harness entirely. `--shell` instead opens a real interactive PTY
# (InvokeAgentRuntimeCommandShell, see shell_session.py) on that session.
#
# Usage: ./exec-cmd.sh alice 'ls -la /mnt/s3'
#        ./exec-cmd.sh alice --shell
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

USAGE="usage: $0 <username> '<command>' | $0 <username> --shell"
USERNAME="${1:?${USAGE}}"; shift

[ -f "${FUSE_STATE_FILE}" ] && source "${FUSE_STATE_FILE}"
RUNTIME_ARN="${FUSE_RUNTIME_ARN:?run deploy.sh first}"
SESSION_ID="$(session_id_for "${USERNAME}")"

if [ "${1:-}" = "--shell" ]; then
  log "${USERNAME} (${SESSION_ID}): opening interactive shell"
  exec "${PY}" "${SCRIPT_DIR}/shell_session.py" --region "${AWS_REGION}" \
    --agent-runtime-arn "${RUNTIME_ARN}" --session-id "${SESSION_ID}" --shell-id "${USERNAME}-shell"
fi

COMMAND="$*"
[ -n "${COMMAND}" ] || die "${USAGE}"
log "${USERNAME} (${SESSION_ID}): ${COMMAND}"

"${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" exec --region "${AWS_REGION}" \
  --agent-runtime-arn "${RUNTIME_ARN}" --session-id "${SESSION_ID}" --command "${COMMAND}"
