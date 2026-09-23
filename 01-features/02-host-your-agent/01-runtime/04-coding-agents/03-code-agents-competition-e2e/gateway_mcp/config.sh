#!/usr/bin/env bash
# Shared configuration for AgentCore Gateway MCP deployment

# AWS — derived from CLI; region can be overridden via env or --region flag
export AWS_REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || echo "us-west-2")}"
export AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"

# Naming
export PROJECT_NAME="github_mcp"
export RUNTIME_NAME="${PROJECT_NAME}_runtime"
export GATEWAY_NAME="github-mcp-gateway"
export ECR_REPO_NAME="github-mcp"
export IAM_ROLE_NAME="agentcore-github-mcp-role"

# Container
export APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/app" && pwd)"
export ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"

# AgentCore Runtime config
export RUNTIME_PROTOCOL="MCP"
export RUNTIME_NETWORK_MODE="PUBLIC"
export RUNTIME_PYTHON_VERSION="PYTHON_3_13"
export RUNTIME_IDLE_TIMEOUT=600
export RUNTIME_MAX_LIFETIME=3300

# Gateway config (inbound = IAM)
export GATEWAY_AUTH_TYPE="AWS_IAM"

# GitHub App credentials (stored in Secrets Manager)
# ARN is set by deploy-credential.sh and saved to state
export GITHUB_APP_SECRET_ARN="${GITHUB_APP_SECRET_ARN:-}"

# State file (tracks deployed resource IDs for teardown)
export STATE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.deployed-state.json"

# Helper: read a value from state file
state_get() {
  local key="$1"
  if [[ -f "$STATE_FILE" ]]; then
    jq -r ".${key} // empty" "$STATE_FILE"
  fi
}

# Helper: write a value to state file
state_set() {
  local key="$1" value="$2"
  if [[ ! -f "$STATE_FILE" ]]; then
    echo '{}' > "$STATE_FILE"
  fi
  local tmp=$(jq --arg k "$key" --arg v "$value" '.[$k] = $v' "$STATE_FILE")
  echo "$tmp" > "$STATE_FILE"
}
