#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

echo "==> Deploying AgentCore Runtime: ${RUNTIME_NAME}"
echo "    Region: ${AWS_REGION} | Account: ${AWS_ACCOUNT_ID}"

# 1. Create ECR repository (idempotent)
echo ""
echo "--- Step 1: ECR Repository ---"
if aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" --region "$AWS_REGION" &>/dev/null; then
  echo "ECR repo '${ECR_REPO_NAME}' already exists."
else
  echo "Creating ECR repo '${ECR_REPO_NAME}'..."
  aws ecr create-repository \
    --repository-name "$ECR_REPO_NAME" \
    --region "$AWS_REGION" \
    --image-scanning-configuration scanOnPush=true \
    --output text --query 'repository.repositoryUri'
fi
state_set "ecr_repo" "$ECR_REPO_NAME"

# 2. Build and push container image
echo ""
echo "--- Step 2: Build & Push Container Image ---"
aws ecr get-login-password --region "$AWS_REGION" | \
  docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

IMAGE_TAG="latest"
IMAGE_URI="${ECR_URI}:${IMAGE_TAG}"

echo "Building image from ${APP_DIR}..."
docker build -t "${ECR_REPO_NAME}:${IMAGE_TAG}" "$APP_DIR"

echo "Tagging and pushing to ${IMAGE_URI}..."
docker tag "${ECR_REPO_NAME}:${IMAGE_TAG}" "$IMAGE_URI"
docker push "$IMAGE_URI"
state_set "image_uri" "$IMAGE_URI"

# 3. Create IAM Role for the runtime
echo ""
echo "--- Step 3: IAM Role ---"

# Trust policy — allows AgentCore to assume this role
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html
TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AssumeRolePolicy",
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

# Execution permissions policy — ECR, CloudWatch Logs, X-Ray, CloudWatch Metrics, Secrets Manager
SECRET_ARN=$(state_get "github_app_secret_arn")
if [[ -z "$SECRET_ARN" ]]; then
  echo "ERROR: No github_app_secret_arn in state. Run deploy-credential.sh first."
  exit 1
fi

EXECUTION_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ECRImageAccess",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": [
        "arn:aws:ecr:'"${AWS_REGION}"':'"${AWS_ACCOUNT_ID}"':repository/'"${ECR_REPO_NAME}"'"
      ]
    },
    {
      "Sid": "ECRTokenAccess",
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchLogsGroup",
      "Effect": "Allow",
      "Action": [
        "logs:DescribeLogStreams",
        "logs:CreateLogGroup"
      ],
      "Resource": [
        "arn:aws:logs:'"${AWS_REGION}"':'"${AWS_ACCOUNT_ID}"':log-group:/aws/bedrock-agentcore/runtimes/*"
      ]
    },
    {
      "Sid": "CloudWatchLogsDescribe",
      "Effect": "Allow",
      "Action": [
        "logs:DescribeLogGroups"
      ],
      "Resource": [
        "arn:aws:logs:'"${AWS_REGION}"':'"${AWS_ACCOUNT_ID}"':log-group:*"
      ]
    },
    {
      "Sid": "CloudWatchLogsWrite",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": [
        "arn:aws:logs:'"${AWS_REGION}"':'"${AWS_ACCOUNT_ID}"':log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
      ]
    },
    {
      "Sid": "XRayAccess",
      "Effect": "Allow",
      "Action": [
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords",
        "xray:GetSamplingRules",
        "xray:GetSamplingTargets"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchMetrics",
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "cloudwatch:namespace": "bedrock-agentcore"
        }
      }
    },
    {
      "Sid": "SecretsManagerAccess",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "'"${SECRET_ARN}"'"
      ]
    }
  ]
}'

if aws iam get-role --role-name "$IAM_ROLE_NAME" &>/dev/null; then
  echo "IAM role '${IAM_ROLE_NAME}' already exists."
  ROLE_ARN=$(aws iam get-role --role-name "$IAM_ROLE_NAME" --query 'Role.Arn' --output text)
else
  echo "Creating IAM role '${IAM_ROLE_NAME}'..."
  ROLE_ARN=$(aws iam create-role \
    --role-name "$IAM_ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" \
    --query 'Role.Arn' --output text)

  echo "Attaching execution policy..."
  aws iam put-role-policy \
    --role-name "$IAM_ROLE_NAME" \
    --policy-name "AgentCoreRuntimeExecution" \
    --policy-document "$EXECUTION_POLICY"

  echo "Waiting for role to propagate..."
  sleep 10
fi
state_set "iam_role_arn" "$ROLE_ARN"
state_set "iam_role_name" "$IAM_ROLE_NAME"
echo "Role ARN: ${ROLE_ARN}"

# 4. Create AgentCore Runtime
echo ""
echo "--- Step 4: AgentCore Runtime ---"

# Find existing runtime by state or by listing
RUNTIME_ID=$(state_get "runtime_id")
EXISTING_RUNTIME=""
if [[ -n "$RUNTIME_ID" ]]; then
  EXISTING_RUNTIME=$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" \
    --region "$AWS_REGION" 2>/dev/null || true)
fi
if [[ -z "$EXISTING_RUNTIME" ]]; then
  RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes \
    --region "$AWS_REGION" \
    --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
    --output text 2>/dev/null || true)
  if [[ -n "$RUNTIME_ID" && "$RUNTIME_ID" != "None" ]]; then
    EXISTING_RUNTIME=$(aws bedrock-agentcore-control get-agent-runtime \
      --agent-runtime-id "$RUNTIME_ID" \
      --region "$AWS_REGION" 2>/dev/null || true)
  else
    RUNTIME_ID=""
  fi
fi

if [[ -n "$EXISTING_RUNTIME" ]]; then
  echo "Runtime '${RUNTIME_NAME}' already exists (${RUNTIME_ID}). Updating..."
  aws bedrock-agentcore-control update-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" \
    --region "$AWS_REGION" \
    --agent-runtime-artifact "{\"containerConfiguration\": {\"containerUri\": \"${IMAGE_URI}\"}}" \
    --role-arn "$ROLE_ARN" \
    --network-configuration "{\"networkMode\": \"${RUNTIME_NETWORK_MODE}\"}" \
    --protocol-configuration "{\"serverProtocol\": \"${RUNTIME_PROTOCOL}\"}" \
    --environment-variables "GITHUB_APP_SECRET_ARN=${SECRET_ARN}" \
    --lifecycle-configuration "{\"idleRuntimeSessionTimeout\": ${RUNTIME_IDLE_TIMEOUT}, \"maxLifetime\": ${RUNTIME_MAX_LIFETIME}}"
  RUNTIME_ARN=$(echo "$EXISTING_RUNTIME" | jq -r '.agentRuntimeArn')
else
  echo "Creating runtime '${RUNTIME_NAME}'..."
  RUNTIME_RESPONSE=$(aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name "$RUNTIME_NAME" \
    --region "$AWS_REGION" \
    --agent-runtime-artifact "{\"containerConfiguration\": {\"containerUri\": \"${IMAGE_URI}\"}}" \
    --role-arn "$ROLE_ARN" \
    --network-configuration "{\"networkMode\": \"${RUNTIME_NETWORK_MODE}\"}" \
    --protocol-configuration "{\"serverProtocol\": \"${RUNTIME_PROTOCOL}\"}" \
    --environment-variables "GITHUB_APP_SECRET_ARN=${SECRET_ARN}" \
    --lifecycle-configuration "{\"idleRuntimeSessionTimeout\": ${RUNTIME_IDLE_TIMEOUT}, \"maxLifetime\": ${RUNTIME_MAX_LIFETIME}}" \
    --output json)
  RUNTIME_ID=$(echo "$RUNTIME_RESPONSE" | jq -r '.agentRuntimeId')
  RUNTIME_ARN=$(echo "$RUNTIME_RESPONSE" | jq -r '.agentRuntimeArn')
fi

state_set "runtime_id" "$RUNTIME_ID"
state_set "runtime_arn" "$RUNTIME_ARN"
echo "Runtime ID: ${RUNTIME_ID}"
echo "Runtime ARN: ${RUNTIME_ARN}"

echo ""
echo "==> Runtime deployment complete."
echo "    State saved to: ${STATE_FILE}"
