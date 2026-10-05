#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

echo "==> Deploying AgentCore Gateway: ${GATEWAY_NAME}"

# Read runtime ARN from state
RUNTIME_ARN=$(state_get "runtime_arn")

if [[ -z "$RUNTIME_ARN" ]]; then
  echo "ERROR: No runtime ARN in state. Run deploy-runtime.sh first."
  exit 1
fi

# 1. Create IAM role for the gateway
echo ""
echo "--- Step 1: Gateway IAM Role ---"
GW_ROLE_NAME="${GATEWAY_NAME}-role"

GW_TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "bedrock-agentcore.amazonaws.com"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "'"${AWS_ACCOUNT_ID}"'"
        },
        "ArnLike": {
          "aws:SourceArn": "arn:aws:bedrock-agentcore:'"${AWS_REGION}"':'"${AWS_ACCOUNT_ID}"':*"
        }
      }
    }
  ]
}'

GW_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeRuntime",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:InvokeAgentRuntime"
      ],
      "Resource": [
        "'"${RUNTIME_ARN}"'",
        "'"${RUNTIME_ARN}"'/*"
      ]
    }
  ]
}'

if aws iam get-role --role-name "$GW_ROLE_NAME" &>/dev/null; then
  echo "Gateway IAM role '${GW_ROLE_NAME}' already exists."
  GW_ROLE_ARN=$(aws iam get-role --role-name "$GW_ROLE_NAME" --query 'Role.Arn' --output text)
else
  echo "Creating gateway IAM role '${GW_ROLE_NAME}'..."
  GW_ROLE_ARN=$(aws iam create-role \
    --role-name "$GW_ROLE_NAME" \
    --assume-role-policy-document "$GW_TRUST_POLICY" \
    --query 'Role.Arn' --output text)

  aws iam put-role-policy \
    --role-name "$GW_ROLE_NAME" \
    --policy-name "AgentCoreGatewayExecution" \
    --policy-document "$GW_POLICY"

  echo "Waiting for role to propagate..."
  sleep 10
fi
state_set "gateway_role_arn" "$GW_ROLE_ARN"
state_set "gateway_role_name" "$GW_ROLE_NAME"
echo "Gateway Role ARN: ${GW_ROLE_ARN}"

# 2. Create the gateway
echo ""
echo "--- Step 2: Create Gateway ---"

# Try state first, then list to find by name
GATEWAY_ID=$(state_get "gateway_id")
EXISTING_GW=""
if [[ -n "$GATEWAY_ID" ]]; then
  EXISTING_GW=$(aws bedrock-agentcore-control get-gateway \
    --gateway-identifier "$GATEWAY_ID" \
    --region "$AWS_REGION" 2>/dev/null || true)
fi
if [[ -z "$EXISTING_GW" ]]; then
  # Search by name in the list
  GATEWAY_ID=$(aws bedrock-agentcore-control list-gateways \
    --region "$AWS_REGION" \
    --query "items[?name=='${GATEWAY_NAME}'].gatewayId | [0]" \
    --output text 2>/dev/null || true)
  if [[ -n "$GATEWAY_ID" && "$GATEWAY_ID" != "None" ]]; then
    EXISTING_GW=$(aws bedrock-agentcore-control get-gateway \
      --gateway-identifier "$GATEWAY_ID" \
      --region "$AWS_REGION" 2>/dev/null || true)
  else
    GATEWAY_ID=""
  fi
fi

if [[ -n "$EXISTING_GW" ]]; then
  echo "Gateway '${GATEWAY_NAME}' already exists (${GATEWAY_ID})."
  GATEWAY_URL=$(echo "$EXISTING_GW" | jq -r '.gatewayUrl // empty')
else
  echo "Creating gateway '${GATEWAY_NAME}'..."
  GW_RESPONSE=$(aws bedrock-agentcore-control create-gateway \
    --name "$GATEWAY_NAME" \
    --region "$AWS_REGION" \
    --description "MCP Gateway for GitHub tools (IAM auth)" \
    --role-arn "$GW_ROLE_ARN" \
    --protocol-type "MCP" \
    --authorizer-type "AWS_IAM" \
    --exception-level "DEBUG" \
    --output json)
  GATEWAY_ID=$(echo "$GW_RESPONSE" | jq -r '.gatewayId')
  GATEWAY_URL=$(echo "$GW_RESPONSE" | jq -r '.gatewayUrl // empty')
fi

state_set "gateway_id" "$GATEWAY_ID"
if [[ -n "$GATEWAY_URL" ]]; then
  state_set "gateway_url" "$GATEWAY_URL"
fi
echo "Gateway ID: ${GATEWAY_ID}"

# 3. Create the gateway target (runtime as MCP server)
echo ""
echo "--- Step 3: Create Gateway Target ---"
TARGET_NAME="GitHubMCP"

EXISTING_TARGET=$(aws bedrock-agentcore-control get-gateway-target \
  --gateway-identifier "$GATEWAY_ID" \
  --name "$TARGET_NAME" \
  --region "$AWS_REGION" 2>/dev/null || true)

RUNTIME_ID=$(state_get "runtime_id")
RUNTIME_ENDPOINT="https://bedrock-agentcore.${AWS_REGION}.amazonaws.com/runtimes/${RUNTIME_ID}/invocations?qualifier=DEFAULT&accountId=${AWS_ACCOUNT_ID}"

TARGET_CONFIG='{
  "mcp": {
    "mcpServer": {
      "endpoint": "'"${RUNTIME_ENDPOINT}"'",
      "listingMode": "DYNAMIC"
    }
  }
}'

CRED_CONFIG='[
  {
    "credentialProviderType": "GATEWAY_IAM_ROLE",
    "credentialProvider": {
      "iamCredentialProvider": {
        "service": "bedrock-agentcore",
        "region": "'"${AWS_REGION}"'"
      }
    }
  }
]'

if [[ -n "$EXISTING_TARGET" ]]; then
  echo "Gateway target '${TARGET_NAME}' already exists. Updating..."
  aws bedrock-agentcore-control update-gateway-target \
    --gateway-identifier "$GATEWAY_ID" \
    --name "$TARGET_NAME" \
    --region "$AWS_REGION" \
    --target-configuration "$TARGET_CONFIG" \
    --credential-provider-configurations "$CRED_CONFIG"
else
  echo "Creating gateway target '${TARGET_NAME}'..."
  aws bedrock-agentcore-control create-gateway-target \
    --gateway-identifier "$GATEWAY_ID" \
    --name "$TARGET_NAME" \
    --region "$AWS_REGION" \
    --description "GitHub MCP Server on AgentCore Runtime" \
    --target-configuration "$TARGET_CONFIG" \
    --credential-provider-configurations "$CRED_CONFIG"
fi

state_set "gateway_target_name" "$TARGET_NAME"

# Fetch final gateway URL if not set yet
if [[ -z "$(state_get 'gateway_url')" ]]; then
  GW_INFO=$(aws bedrock-agentcore-control get-gateway \
    --gateway-identifier "$GATEWAY_ID" \
    --region "$AWS_REGION" 2>/dev/null || true)
  GATEWAY_URL=$(echo "$GW_INFO" | jq -r '.gatewayUrl // empty')
  if [[ -n "$GATEWAY_URL" ]]; then
    state_set "gateway_url" "$GATEWAY_URL"
  fi
fi

echo ""
echo "==> Gateway deployment complete."
echo "    Gateway ID: ${GATEWAY_ID}"
echo "    Gateway URL: $(state_get 'gateway_url')"
echo "    Auth: AWS IAM (SigV4)"
