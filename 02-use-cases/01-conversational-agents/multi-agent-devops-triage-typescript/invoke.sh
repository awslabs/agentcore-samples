#!/usr/bin/env bash
# Invokes the deployed lead agent end-to-end.
# Usage: ./invoke.sh <lead-runtime-arn> <prompt> [region]
set -euo pipefail

ARN="$1"
PROMPT="$2"
REGION="${3:-${AWS_REGION:-us-east-1}}"
SESSION_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')

payload=$(python3 -c "import json,sys; print(json.dumps({'prompt': sys.argv[1]}))" "$PROMPT")

# The CLI writes the response body to the outfile and its own metadata JSON
# to stdout — keep them separate.
outfile=$(mktemp)
trap 'rm -f "$outfile"' EXIT

aws bedrock-agentcore invoke-agent-runtime \
  --region "$REGION" \
  --agent-runtime-arn "$ARN" \
  --runtime-session-id "$SESSION_ID" \
  --content-type application/json \
  --accept application/json \
  --payload "$(printf '%s' "$payload" | base64)" \
  "$outfile" > /dev/null

python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('answer', d))" "$outfile"
