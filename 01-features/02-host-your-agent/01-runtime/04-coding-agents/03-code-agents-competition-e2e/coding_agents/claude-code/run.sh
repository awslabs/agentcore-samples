#!/usr/bin/env bash
# ============================================================
# Claude Code launcher for AgentCore Runtime (headless)
# ============================================================
# This script is baked into the container image and called by connect.py.
# The container already runs as the agent user (USER agent in Dockerfile).
#
# Authentication:
#   Claude Code on AgentCore uses Bedrock (CLAUDE_CODE_USE_BEDROCK=1).
#   The microVM's IAM role already has bedrock:InvokeModel permissions,
#   so no API key is needed — AWS credentials are provided automatically
#   by the instance metadata service.
#
# MCP:
#   ~/.mcp.json is generated at startup from GATEWAY_URL env var.
#   permissionMode=dontAsk in settings.json auto-accepts the MCP server.
#
# Usage (from connect.py):
#   /app/run.sh                          # interactive claude (--continue)
#   /app/run.sh --print "fix the bug"   # non-interactive one-shot
#   /app/run.sh <any claude args>        # pass-through
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
export CLAUDE_CODE_USE_BEDROCK=1
export HOME="/home/agent"

cd "$HOME"

# ── Configure MCP Gateway (needs GATEWAY_URL from runtime env) ──
if [ -n "${GATEWAY_URL:-}" ]; then
  cat > "$HOME/.mcp.json" <<MCPEOF
{
  "mcpServers": {
    "gateway": {
      "type": "stdio",
      "command": "node",
      "args": ["/mnt/s3files/mcp/index.js", "--gateway-url", "${GATEWAY_URL}", "--region", "${AWS_REGION}"]
    }
  }
}
MCPEOF
fi

# ── Parse --model flag ───────────────────────────────────────
MODEL="us.anthropic.claude-opus-4-6-v1"
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
  exec claude --dangerously-skip-permissions --print --max-turns 50 --model "$MODEL" "$@"
else
  exec claude --dangerously-skip-permissions --model "$MODEL"
fi
