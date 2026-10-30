#!/usr/bin/env bash
# Invokes the deployed lead agent end-to-end.
#
# Usage: ./invoke.sh <lead-runtime-arn> [prompt] [region]
#
# With no prompt, the built-in incident report below is sent. It carries real
# log lines and metrics on purpose: the log-analyst worker reasons only over
# the data in the message and never invents evidence, so a bare question like
# "why is orders-api slow?" comes back asking for the logs instead of a triage.
#
# Pass the region as $3 only together with a prompt; otherwise set AWS_REGION.
set -euo pipefail

DEFAULT_PROMPT=$(cat <<'EOF'
orders-api latency spiked after the 14:00 deploy. Triage this.

Logs (CloudWatch, orders-api):
2026-09-23T14:02:11Z ERROR [orders-api] timeout connecting to postgres-orders after 5000ms (attempt 1/3)
2026-09-23T14:02:14Z ERROR [orders-api] timeout connecting to postgres-orders after 5000ms (attempt 2/3)
2026-09-23T14:02:19Z WARN  [orders-api] connection pool exhausted: 20/20 in use, 37 waiters
2026-09-23T14:03:02Z ERROR [orders-api] POST /checkout 503 upstream_timeout
  (the timeout/pool-exhausted pair repeats ~40 times through 14:19)

Metrics (5-minute periods):
  p99 latency   180ms at 13:45  ->  2400ms at 14:05  ->  2600ms at 14:20
  error rate    0.2%            ->  6.8%
  requests/sec  340             ->  355            (flat, not a traffic surge)
  DB CPU        41%             ->  44%            (postgres-orders, no saturation)

Deploy timeline:
  14:00  orders-api v2026.09.23-1 released (changelog mentions connection-pool tuning)
  13:10  payments-svc v2026.09.22-4 released (unrelated, healthy)

What happened, which team owns this, and what should we do now?
EOF
)

ARN="$1"
PROMPT="${2:-$DEFAULT_PROMPT}"
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
