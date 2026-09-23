#!/usr/bin/env bash
# Build and push the Kiro container image to ECR, then setup AgentCore Identity.
#
# AgentCore Identity (Token Vault):
#   Instead of passing KIRO_API_KEY as a plaintext env var on the runtime,
#   we store it encrypted in AWS Secrets Manager via AgentCore Identity.
#   At container startup, entrypoint.py fetches the key using:
#     1. get_workload_access_token(workloadName="kiro-coding-agent")
#     2. get_resource_api_key(token, credentialProviderName="kiro-api-key")
#   This way the API key is never exposed in runtime config or CloudTrail logs.
#
# Usage:
#   ./setup.sh                          # Build image + setup identity (prompts for key)
#   KIRO_API_KEY=xxx ./setup.sh         # Non-interactive
#   ./setup.sh --skip-identity          # Build image only, skip identity setup
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INFRA_CONFIG="${SCRIPT_DIR}/../infra.config"

if [ ! -f "$INFRA_CONFIG" ]; then
  echo "Error: infra.config not found. Run ../infra/setup.sh first."
  exit 1
fi

source "$INFRA_CONFIG"

ECR_REPO="coding-agents-kiro"
IMAGE_TAG="latest"
ECR_URI="${INFRA_ACCOUNT_ID}.dkr.ecr.${INFRA_REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"
AGENT_NAME="kiro"

SKIP_IDENTITY=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --skip-identity) SKIP_IDENTITY=true; shift ;;
    *) echo "Unknown: $1"; exit 1 ;;
  esac
done

echo "=============================================="
echo "  Kiro — Build & Push + Identity Setup"
echo "  Region: ${INFRA_REGION}  Account: ${INFRA_ACCOUNT_ID}"
echo "=============================================="

# ── ECR repo ─────────────────────────────────────────────────────────────────
if aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${INFRA_REGION}" >/dev/null 2>&1; then
  echo "ECR repo exists: ${ECR_REPO}"
else
  echo "Creating ECR repo: ${ECR_REPO}"
  aws ecr create-repository --repository-name "${ECR_REPO}" --region "${INFRA_REGION}" > /dev/null
fi

# ── Build & push ─────────────────────────────────────────────────────────────
echo "Logging into ECR..."
aws ecr get-login-password --region "${INFRA_REGION}" | \
  docker login --username AWS --password-stdin "${INFRA_ACCOUNT_ID}.dkr.ecr.${INFRA_REGION}.amazonaws.com"

echo "Building arm64 image..."
docker buildx build \
  --platform linux/arm64 \
  -t "${ECR_URI}" \
  -f "${SCRIPT_DIR}/Dockerfile" \
  "${SCRIPT_DIR}" \
  --push

echo "Image pushed: ${ECR_URI}"

# ── Save agent config ────────────────────────────────────────────────────────
cat > "${SCRIPT_DIR}/agent.config" <<EOF
AGENT_NAME=${AGENT_NAME}
ECR_REPO=${ECR_REPO}
ECR_URI=${ECR_URI}
EOF

echo ""
echo "Config saved to: agent.config"

# ══════════════════════════════════════════════════════════════════════════════
# AgentCore Identity — Token Vault Setup
# ══════════════════════════════════════════════════════════════════════════════
# This stores the KIRO_API_KEY encrypted in Secrets Manager (via KMS) using
# AgentCore's workload identity system. The runtime never sees the key in
# plaintext config — it fetches it at boot via IAM-authorized API calls.
#
# Resources created:
#   - Workload Identity: "kiro-coding-agent"
#   - API Key Credential Provider: "kiro-api-key" (encrypted in Secrets Manager)
# ══════════════════════════════════════════════════════════════════════════════

if [ "$SKIP_IDENTITY" = true ]; then
  echo ""
  echo "Skipping Identity setup (--skip-identity)."
  echo "Next: python deploy.py"
  exit 0
fi

WORKLOAD_NAME="kiro-coding-agent"
CREDENTIAL_NAME="kiro-api-key"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  AgentCore Identity — Token Vault"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Step 1: Create workload identity (idempotent)
if aws bedrock-agentcore-control get-workload-identity \
    --name "$WORKLOAD_NAME" --region "${INFRA_REGION}" >/dev/null 2>&1; then
  echo "  Workload identity exists: $WORKLOAD_NAME"
else
  echo "  Creating workload identity: $WORKLOAD_NAME"
  aws bedrock-agentcore-control create-workload-identity \
    --name "$WORKLOAD_NAME" --region "${INFRA_REGION}" > /dev/null
fi

# Step 2: Store API key in credential provider (encrypted via KMS)
# Get the key from env var or prompt interactively
if [ -z "${KIRO_API_KEY:-}" ]; then
  echo ""
  echo "  Enter your KIRO_API_KEY (from https://kiro.dev/settings):"
  read -rsp "  > " KIRO_API_KEY
  echo ""
fi

if [ -z "$KIRO_API_KEY" ]; then
  echo "  WARNING: No KIRO_API_KEY provided. Skipping credential provider."
  echo "  Re-run with KIRO_API_KEY=xxx ./setup.sh to store it later."
else
  if aws bedrock-agentcore-control get-api-key-credential-provider \
      --name "$CREDENTIAL_NAME" --region "${INFRA_REGION}" >/dev/null 2>&1; then
    echo "  Updating credential provider: $CREDENTIAL_NAME"
    aws bedrock-agentcore-control update-api-key-credential-provider \
      --name "$CREDENTIAL_NAME" --api-key "$KIRO_API_KEY" --region "${INFRA_REGION}" > /dev/null
  else
    echo "  Creating credential provider: $CREDENTIAL_NAME"
    aws bedrock-agentcore-control create-api-key-credential-provider \
      --name "$CREDENTIAL_NAME" --api-key "$KIRO_API_KEY" --region "${INFRA_REGION}" > /dev/null
  fi
  echo "  API key stored (encrypted in Secrets Manager via KMS)"
fi


echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Identity setup complete."
echo "  The runtime will fetch KIRO_API_KEY at startup via entrypoint.py:"
echo "    1. get_workload_access_token(workloadName='$WORKLOAD_NAME')"
echo "    2. get_resource_api_key(token, credentialProviderName='$CREDENTIAL_NAME')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Next: python deploy.py"
