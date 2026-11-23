#!/usr/bin/env bash
# Start the loopback client for a single recorded WebSocket Runtime.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
sample=${1:-}
case "$sample" in
  01-bedrock-sonic-ws|02-strands-ws|03-langchain-transcribe-polly-ws|04-pipecat-sonic-ws) ;;
  *) echo 'Pass a WebSocket sample directory (01 through 04).' >&2; exit 2 ;;
esac
receipt="$root/$sample/stream-runtime.json"
if [[ ! -f "$receipt" ]]; then echo 'Missing V2 receipt; see WEBSOCKET_DEPLOYMENT.md.' >&2; exit 1; fi
arn=$(jq -er '.runtime_arn | strings | select(length > 0)' "$receipt")
region=$(jq -er '.region | strings | select(length > 0)' "$receipt")
cd "$root/$sample/client"
if [[ "$sample" == 04-pipecat-sonic-ws ]]; then
  python client.py --runtime-arn "$arn" --region "$region" &
  signer=$!
  trap 'kill "$signer" 2>/dev/null || true; wait "$signer" 2>/dev/null || true' EXIT
  npm run dev -- --host 127.0.0.1
else
  exec python client.py --runtime-arn "$arn" --region "$region"
fi
