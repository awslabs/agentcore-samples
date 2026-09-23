#!/usr/bin/env bash
# ============================================================
# OpenCode launcher for AgentCore Runtime (headless)
# ============================================================
# This script is baked into the container image and called by connect.py.
# The container already runs as the agent user (USER agent in Dockerfile).
#
# Authentication:
#   OpenCode on AgentCore uses Bedrock via the AWS credential chain.
#   The microVM's IAM role already has bedrock:InvokeModel permissions,
#   so no API key is needed — AWS credentials are provided automatically
#   by the instance metadata service (IMDS v2).
#
# What it does:
#   1. Ensures AWS env vars are set (region)
#   2. Launches opencode with Bedrock provider
#
# Usage (from connect.py):
#   /app/run.sh                          # interactive opencode TUI
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
export HOME="/home/agent"

cd "$HOME"

# ── Fetch AWS credentials from IMDS v2 ──────────────────────────────────────
# OpenCode's Bedrock provider requires explicit AWS_ACCESS_KEY_ID/SECRET/TOKEN
# env vars — it does not read IMDS directly. We fetch them here.
if [ -z "${AWS_ACCESS_KEY_ID:-}" ]; then
  IMDS_TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 300" 2>/dev/null || true)

  if [ -n "$IMDS_TOKEN" ]; then
    ROLE_NAME=$(curl -s -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
      "http://169.254.169.254/latest/meta-data/iam/security-credentials/" 2>/dev/null || true)

    if [ -n "$ROLE_NAME" ]; then
      CREDS_JSON=$(curl -s -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/${ROLE_NAME}" 2>/dev/null || true)

      if [ -n "$CREDS_JSON" ]; then
        export AWS_ACCESS_KEY_ID=$(echo "$CREDS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKeyId'])")
        export AWS_SECRET_ACCESS_KEY=$(echo "$CREDS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['SecretAccessKey'])")
        export AWS_SESSION_TOKEN=$(echo "$CREDS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['Token'])")
      fi
    fi
  fi
fi

# Generate opencode config with current region + MCP gateway
mkdir -p "$HOME/.config/opencode"

MCP_BLOCK=""
if [ -n "${GATEWAY_URL:-}" ]; then
  MCP_BLOCK=$(cat <<MCPEOF
  "mcp": {
    "gateway": {
      "type": "local",
      "command": ["node", "/mnt/s3files/mcp/index.js", "--gateway-url", "${GATEWAY_URL}", "--region", "${AWS_REGION}"]
    }
  },
MCPEOF
  )
  echo "[mcp] Gateway configured: ${GATEWAY_URL}"
fi

cat > "$HOME/.config/opencode/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
${MCP_BLOCK}
  "provider": {
    "amazon-bedrock": {
      "options": {
        "region": "${AWS_REGION}"
      }
    }
  },
  "model": "amazon-bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0",
  "small_model": "amazon-bedrock/anthropic.claude-haiku-4-5-20251001-v1:0"
}
EOF

# ── Parse --model flag ───────────────────────────────────────
MODEL="amazon-bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0"
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

if [ $# -gt 0 ]; then
  PROMPT="$*"
  exec opencode run --dangerously-skip-permissions -m "$MODEL" "$PROMPT"
else
  exec opencode
fi
