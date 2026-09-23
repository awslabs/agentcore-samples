#!/usr/bin/env bash
# ============================================================
# Cursor launcher for AgentCore Runtime (headless)
# ============================================================
# Fetches CURSOR_API_KEY from AgentCore Identity Token Vault,
# then runs cursor-agent.
#
# Usage:
#   /app/run.sh "Fix the bug in main.py"       # one-shot headless
#   /app/run.sh                                 # interactive mode
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

REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-west-2}}"

# ── Configure MCP Gateway (needs GATEWAY_URL from runtime env) ──
if [ -n "${GATEWAY_URL:-}" ]; then
  mkdir -p /home/agent/.cursor
  cat > /home/agent/.cursor/mcp.json <<MCPEOF
{
  "mcpServers": {
    "gateway": {
      "command": "node",
      "args": ["/mnt/s3files/mcp/index.js", "--gateway-url", "${GATEWAY_URL}", "--region", "${AWS_REGION:-us-west-2}"]
    }
  }
}
MCPEOF
  echo "[mcp] Gateway configured: ${GATEWAY_URL}"
fi

WORKLOAD_NAME="${AGENTCORE_WORKLOAD_NAME:-cursor-coding-agent}"
CREDENTIAL_PROVIDER="${AGENTCORE_CREDENTIAL_PROVIDER:-cursor-api-key}"

# ── Fetch API key from AgentCore Identity Token Vault ────────
if [ -z "${CURSOR_API_KEY:-}" ]; then
  VAULT_KEY=$(python3 << 'PYEOF'
import boto3, sys, os
from botocore.config import Config
region = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-west-2"))
workload = os.environ.get("AGENTCORE_WORKLOAD_NAME", "cursor-coding-agent")
provider = os.environ.get("AGENTCORE_CREDENTIAL_PROVIDER", "cursor-api-key")
try:
    config = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})
    client = boto3.client("bedrock-agentcore", region_name=region, config=config)
    token = client.get_workload_access_token(workloadName=workload)["workloadAccessToken"]
    key = client.get_resource_api_key(
        workloadIdentityToken=token,
        resourceCredentialProviderName=provider,
    )["apiKey"]
    print(key, end="")
except Exception as e:
    print(f"Vault error: {type(e).__name__}: {e}", file=sys.stderr)
PYEOF
  )

  if [ -n "${VAULT_KEY:-}" ]; then
    export CURSOR_API_KEY="$VAULT_KEY"
    echo "Retrieved CURSOR_API_KEY from AgentCore Identity Token Vault"
  fi
fi

if [ -n "${CURSOR_API_KEY:-}" ]; then
  echo "CURSOR_API_KEY is set"
else
  echo "Error: No CURSOR_API_KEY available"
  exit 1
fi

# ── Ensure cursor-agent is available ─────────────────────────
CURSOR_BIN=/usr/local/bin/cursor-agent
if [ ! -x "$CURSOR_BIN" ]; then
  echo "Error: cursor-agent binary not found at $CURSOR_BIN"
  exit 1
fi

# ── Parse --model flag ───────────────────────────────────────
MODEL="auto"
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
  exec "$CURSOR_BIN" -p --trust --force --model "$MODEL" --workspace /home/agent "$PROMPT"
else
  exec "$CURSOR_BIN" --model "$MODEL" --workspace /home/agent
fi
