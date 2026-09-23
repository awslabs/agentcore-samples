#!/usr/bin/env bash
# ============================================================
# Codex launcher for AgentCore Runtime (headless)
# ============================================================
# Routes inference through Amazon Bedrock Mantle (us-east-2).
# Auth: short-term Bedrock API key generated from IAM role creds,
# passed as OPENAI_API_KEY + OPENAI_BASE_URL to the Codex CLI.
#
# Usage:
#   /app/run.sh "Fix the bug in main.py"       # one-shot headless
#   /app/run.sh                                 # interactive TUI
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

# Bedrock Mantle region (GPT-5.5 only available in us-east-2)
BEDROCK_REGION="${BEDROCK_MANTLE_REGION:-us-east-2}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-2}}"

# ── Configure MCP Gateway (needs GATEWAY_URL from runtime env) ──
if [ -n "${GATEWAY_URL:-}" ]; then
  if ! grep -q "mcp_servers.gateway" /home/agent/.codex/config.toml 2>/dev/null; then
    cat >> /home/agent/.codex/config.toml <<MCPEOF

[mcp_servers.gateway]
command = "node"
args = ["/mnt/s3files/mcp/index.js", "--gateway-url", "${GATEWAY_URL}", "--region", "${AWS_REGION:-us-west-2}"]
MCPEOF
  fi
  echo "MCP gateway configured: ${GATEWAY_URL}"
fi

# ── Generate short-term Bedrock API key from IAM role ────────
if [ -z "${OPENAI_API_KEY:-}" ]; then
  BEDROCK_TOKEN=$(python3 << 'PYEOF'
import sys, os
try:
    from aws_bedrock_token_generator import provide_token
    token = provide_token(region=os.environ.get("BEDROCK_REGION", "us-east-2"))
    print(token, end="")
except ImportError:
    import boto3
    from botocore.config import Config
    region = os.environ.get("BEDROCK_REGION", "us-east-2")
    config = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})
    sts = boto3.client("sts", region_name=region, config=config)
    # Fallback: use SigV4 — set a dummy key so Codex doesn't complain
    print("BEDROCK_SIGV4", end="")
PYEOF
  )

  if [ -n "${BEDROCK_TOKEN:-}" ] && [ "$BEDROCK_TOKEN" != "BEDROCK_SIGV4" ]; then
    export OPENAI_API_KEY="$BEDROCK_TOKEN"
    echo "Generated short-term Bedrock API key"
  else
    echo "Warning: Could not generate Bedrock token, falling back to config.toml auth"
  fi
fi

export OPENAI_BASE_URL="https://bedrock-mantle.${BEDROCK_REGION}.api.aws/openai/v1"
echo "Using Bedrock Mantle: ${OPENAI_BASE_URL}"

# ── Parse --model flag ───────────────────────────────────────
MODEL="openai.gpt-5.5"
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

# ── Run ──────────────────────────────────────────────────────
if [ $# -gt 0 ]; then
  PROMPT="$*"
  echo "Running prompt with model: ${MODEL}"
  cd /home/agent
  exec codex exec --model "$MODEL" --yolo --skip-git-repo-check "$PROMPT"
else
  cd /home/agent
  exec codex --model "$MODEL"
fi
