#!/usr/bin/env bash
set -euo pipefail

ROLE_NAME="TypescriptExecutionRole"
POLICY_NAME="TypescriptExecutionPolicy"

TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "bedrock-agentcore.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}'

PERMISSIONS_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockInvokeModel",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "*"
    },
    {
      "Sid": "ECRPull",
      "Effect": "Allow",
      "Action": [
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetAuthorizationToken"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EcrPublicPull",
      "Effect": "Allow",
      "Action": ["ecr-public:GetAuthorizationToken"],
      "Resource": "*"
    },
    {
      "Sid": "StsForEcrPublicPull",
      "Effect": "Allow",
      "Action": ["sts:GetServiceBearerToken"],
      "Resource": "*"
    },
    {
      "Sid": "XRay",
      "Effect": "Allow",
      "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "*"
    },
    {
      "Sid": "AgentCore",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:*Memory*",
        "bedrock-agentcore:*Browser*",
        "bedrock-agentcore:*Gateway*",
        "bedrock-agentcore:*CodeInterpreter*",
        "bedrock-agentcore:RetrieveMemoryRecords",
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore:GetEvent"
      ],
      "Resource": "*"
    },
    {
      "Sid": "GetAgentCoreApiKeys",
      "Effect": "Allow",
      "Action": ["bedrock-agentcore:GetResourceApiKey"],
      "Resource": "*"
    }
  ]
}'

create_role() {
  local role_arn
  role_arn=$(aws iam get-role \
    --role-name "$ROLE_NAME" \
    --query 'Role.Arn' \
    --output text 2>/dev/null || true)

  if [ -n "$role_arn" ] && [ "$role_arn" != "None" ]; then
    echo "Role $ROLE_NAME already exists: $role_arn" >&2
    echo "$role_arn"
    return 0
  fi

  role_arn=$(aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" \
    --description "Execution role for Amazon Bedrock AgentCore Runtime with TypeScript" \
    --query 'Role.Arn' \
    --output text)
  echo "Created role: $role_arn" >&2

  aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "$POLICY_NAME" \
    --policy-document "$PERMISSIONS_POLICY"
  echo "Attached policy: $POLICY_NAME" >&2

  echo "$role_arn"
}

delete_role() {
  aws iam delete-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "$POLICY_NAME" 2>/dev/null && \
    echo "Deleted inline policy: $POLICY_NAME" || true

  aws iam delete-role \
    --role-name "$ROLE_NAME" 2>/dev/null && \
    echo "Deleted role: $ROLE_NAME" || \
    echo "Role $ROLE_NAME not found"
}

case "${1:-}" in
  create) create_role ;;
  delete) delete_role ;;
  *)
    echo "Usage: ./iam.sh <create|delete>"
    exit 1
    ;;
esac
