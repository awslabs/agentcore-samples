#!/usr/bin/env bash
# ============================================================
# Kiro CLI launcher for AgentCore Runtime (headless, no browser)
# ============================================================
# This script is baked into the container image and called by connect.py.
# The container already runs as the agent user (USER agent in Dockerfile).
#
# Security model:
#   - The KIRO_API_KEY is fetched ON-DEMAND from Token Vault using the
#     runtime's IAM role (GetWorkloadAccessToken + GetResourceApiKey)
#   - The key never touches disk — it lives only in this shell's memory
#   - Each new PTY session fetches a fresh key (rotation-friendly)
#   - The runtime IAM role is the only principal that can read the key
#
# Authentication methods (tried in order):
#   1. KIRO_API_KEY from AgentCore Identity Token Vault (Pro+ headless)
#   2. Fallback: device-flow login (prints URL + code for browser auth)
#
# Usage (from connect.py):
#   /app/run.sh                         # interactive kiro-cli
#   /app/run.sh chat "fix the bug"      # non-interactive command
#   /app/run.sh login                   # force re-login via device-flow
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
export HOME="/home/agent"

# ── Configure MCP Gateway (needs GATEWAY_URL from runtime env) ──
if [ -n "${GATEWAY_URL:-}" ]; then
  mkdir -p "$HOME/.kiro/settings"
  cat > "$HOME/.kiro/settings/mcp.json" <<MCPEOF
{
  "mcpServers": {
    "gateway": {
      "command": "node",
      "args": ["/mnt/s3files/mcp/index.js", "--gateway-url", "${GATEWAY_URL}", "--region", "${AWS_REGION}"],
      "autoApprove": ["*"]
    }
  }
}
MCPEOF
  echo "[mcp] Gateway configured: ${GATEWAY_URL}"
fi

WORKLOAD_NAME="${AGENTCORE_WORKLOAD_NAME:-kiro-coding-agent}"
CREDENTIAL_PROVIDER="${AGENTCORE_CREDENTIAL_PROVIDER:-kiro-api-key}"

# ── Fetch KIRO_API_KEY from AgentCore Identity Token Vault ───
fetch_api_key() {
  python3 -W ignore -c "
import boto3, sys, warnings
warnings.filterwarnings('ignore')

from botocore.config import Config

config = Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 2})
client = boto3.client('bedrock-agentcore', region_name='${AWS_DEFAULT_REGION}', config=config)

try:
    token = client.get_workload_access_token(workloadName='${WORKLOAD_NAME}')['workloadAccessToken']
    key = client.get_resource_api_key(
        workloadIdentityToken=token,
        resourceCredentialProviderName='${CREDENTIAL_PROVIDER}'
    )['apiKey']
    print(key, end='')
except Exception as e:
    print(f'[identity] Failed to fetch key: {e}', file=sys.stderr)
"
}

echo "[auth] Fetching KIRO_API_KEY from AgentCore Identity Token Vault..."
KIRO_API_KEY="$(fetch_api_key)"
export KIRO_API_KEY

if [ -n "$KIRO_API_KEY" ]; then
  echo "[auth] KIRO_API_KEY retrieved successfully (Pro+ headless mode)"
else
  echo "[auth] WARNING: Could not retrieve KIRO_API_KEY from Token Vault"
  echo "[auth] Falling back to device-flow login..."
fi

# ── Parse --model flag ───────────────────────────────────────
MODEL="auto"
REMAINING_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --model)
      MODEL="$2"
      shift 2
      ;;
    *)
      REMAINING_ARGS+=("$1")
      shift
      ;;
  esac
done
set -- "${REMAINING_ARGS[@]}"

mkdir -p "$HOME/.kiro/settings"
cat > "$HOME/.kiro/settings/cli.json" <<EOF
{
  "chat.defaultModel": "${MODEL}"
}
EOF

# ── Determine the action ─────────────────────────────────────
ACTION="${1:-interactive}"
shift 2>/dev/null || true
PROMPT="$*"

cd "$HOME"

# ── Login flow (explicit or fallback) ────────────────────────
if [ "$ACTION" = "login" ] || [ -z "$KIRO_API_KEY" ]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Starting device-flow login..."
  echo "  A URL and code will appear. Open the URL in your browser."
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  exec kiro-cli login --use-device-flow
fi

# ── Launch kiro-cli ──────────────────────────────────────────
case "$ACTION" in
  interactive)
    exec kiro-cli
    ;;
  chat)
    exec kiro-cli chat --no-interactive --trust-all-tools "$PROMPT"
    ;;
  *)
    exec kiro-cli "$ACTION" "$PROMPT"
    ;;
esac
