#!/usr/bin/env bash
# ============================================================
# Hermes Agent launcher for AgentCore Runtime (headless)
# ============================================================
# This script is baked into the container image and called by connect.py.
# The container already runs as the agent user (USER agent in Dockerfile).
#
# Authentication:
#   Hermes on AgentCore uses Bedrock (HERMES_INFERENCE_PROVIDER=bedrock).
#   The microVM's IAM role already has bedrock:InvokeModel permissions,
#   so no API key is needed — AWS credentials are provided automatically
#   by the instance metadata service (IMDS v2).
#
# What it does:
#   1. Ensures AWS env vars are set (region, Bedrock provider)
#   2. Launches hermes directly
#
# Usage (from connect.py):
#   /app/run.sh                          # interactive hermes
#   /app/run.sh "fix the bug in main.py" # non-interactive one-shot
# ============================================================
set -euo pipefail

# Inherit env vars from PID 1 (container entrypoint) if not already set
if [ -z "${GATEWAY_URL:-}" ] && [ -r /proc/1/environ ]; then
  GATEWAY_URL=$(cat /proc/1/environ | tr '\0' '\n' | grep ^GATEWAY_URL= | cut -d= -f2- || true)
  export GATEWAY_URL
fi
if [ -z "${AWS_REGION:-}" ] && [ -r /proc/1/environ ]; then
  AWS_REGION=$(cat /proc/1/environ | tr '\0' '\n' | grep ^AWS_REGION= | cut -d= -f2- || true)
  export AWS_REGION
fi

export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-west-2}}"
export AWS_REGION="${AWS_REGION:-$AWS_DEFAULT_REGION}"
export HERMES_INFERENCE_PROVIDER=bedrock
export HERMES_INFERENCE_MODEL="${HERMES_INFERENCE_MODEL:-global.anthropic.claude-sonnet-4-5-20250929-v1:0}"
export HOME="/home/agent"

cd "$HOME"

# ── Configure MCP Gateway + Provider (needs GATEWAY_URL from runtime env) ──
cat > "$HOME/.hermes/config.yaml" <<MCPEOF
provider: bedrock
model: ${HERMES_INFERENCE_MODEL}
region: ${AWS_REGION}
MCPEOF

if [ -n "${GATEWAY_URL:-}" ]; then
  cat >> "$HOME/.hermes/config.yaml" <<MCPEOF

mcp_servers:
  gateway:
    command: "node"
    args: ["/mnt/s3files/mcp/index.js", "--gateway-url", "${GATEWAY_URL}", "--region", "${AWS_REGION}"]
MCPEOF
  echo "[mcp] Gateway configured: ${GATEWAY_URL}"
fi

# ── Parse --model flag ───────────────────────────────────────
MODEL="${HERMES_INFERENCE_MODEL}"
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --model)
      MODEL="$2"
      shift 2
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done
set -- "${ARGS[@]}"

MODEL_FLAG="-m ${MODEL} --provider bedrock"

if [ $# -gt 0 ]; then
  PROMPT="$*"
  exec hermes $MODEL_FLAG --non-interactive "$PROMPT"
else
  exec hermes $MODEL_FLAG
fi
