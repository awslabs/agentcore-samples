#!/usr/bin/env bash
# Runs both directions of the isolation test and prints a summary.
# Two calls per user on the SAME session (order matters -- a session
# mounts AT MOST ONCE for its life):
#   1. Try to mount the OTHER user's prefix -- expect mount=DENIED.
#   2. Mount its OWN prefix -- expect mount=mounted, plus a real note
#      written by the Strands harness, read back through the mount.
#
# Usage: ./test.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

[ -f "${FUSE_STATE_FILE}" ] && source "${FUSE_STATE_FILE}"
RUNTIME_ARN="${FUSE_RUNTIME_ARN:?run deploy.sh first}"
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "${TMP_DIR}"' EXIT

invoke() {
  local session_id="$1" payload="$2"
  "${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" invoke --region "${AWS_REGION}" \
    --agent-runtime-arn "${RUNTIME_ARN}" --session-id "${session_id}" --qualifier DEFAULT --payload-file "${payload}"
}

# Writes each raw JSON response to its own file -- never through a shell
# variable, since mount-s3's own error text can contain quotes that break
# any attempt to inline it as a string literal elsewhere.
run_pair() {
  local acting="$1" other="$2"
  local acting_sid other_sid deny_payload empty_payload
  acting_sid="$(session_id_for "${acting}")"
  other_sid="$(session_id_for "${other}")"
  deny_payload="${TMP_DIR}/${acting}-deny.json"
  empty_payload="${TMP_DIR}/${acting}-own.json"
  python3 -c "import json; print(json.dumps({'prefix': 'sessions/${other_sid}/'}))" > "${deny_payload}"
  echo '{}' > "${empty_payload}"

  log "${acting}: deny ${other}'s prefix, then mount + write own"
  invoke "${acting_sid}" "${deny_payload}" > "${TMP_DIR}/${acting}-deny-result.json"
  invoke "${acting_sid}" "${empty_payload}" > "${TMP_DIR}/${acting}-own-result.json"
}

run_pair alice bob
run_pair bob alice

echo
log "=== summary ==="
python3 -c "
import json

def parse(label, deny_file, own_file):
    with open(deny_file) as f:
        deny_body = json.load(f)['body']
    with open(own_file) as f:
        own_body = json.load(f)['body']
    print(f'{label:<6} deny-other: mount={deny_body.get(\"mount\")}')
    print(f'{label:<6} own:        mount={own_body.get(\"mount\")} note={own_body.get(\"note_content\")!r}')

parse('alice', '${TMP_DIR}/alice-deny-result.json', '${TMP_DIR}/alice-own-result.json')
parse('bob', '${TMP_DIR}/bob-deny-result.json', '${TMP_DIR}/bob-own-result.json')
"
echo
log "expected: deny-other -> DENIED; own -> mounted, note contains that session's own id."
log "If own also comes back DENIED, wait ~30s for bucket-policy propagation and retry."
