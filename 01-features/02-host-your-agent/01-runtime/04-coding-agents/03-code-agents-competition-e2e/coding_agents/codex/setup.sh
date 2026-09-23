#!/usr/bin/env bash
# Build and push the Codex (PTY) container image to ECR.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INFRA_CONFIG="${SCRIPT_DIR}/../infra.config"

if [ ! -f "$INFRA_CONFIG" ]; then
  echo "Error: infra.config not found. Run ../infra/setup.sh first."
  exit 1
fi

source "$INFRA_CONFIG"

ECR_REPO="coding-agents-codex"
IMAGE_TAG="latest"
ECR_URI="${INFRA_ACCOUNT_ID}.dkr.ecr.${INFRA_REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"
AGENT_NAME="codex"

echo "=============================================="
echo "  Codex (PTY) — Build & Push"
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
echo "Next: python deploy.py"
