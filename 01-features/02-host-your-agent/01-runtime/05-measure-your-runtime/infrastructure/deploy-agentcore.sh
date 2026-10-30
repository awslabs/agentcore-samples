#!/usr/bin/env bash
# Deploy the base-agent image to Amazon Bedrock AgentCore Runtime. Prod only.
#
# Creates (or updates, on re-run) a least-privilege execution role, then
# creates/updates the AgentCore Runtime from the image in ECR at the selected
# IMAGE_SIZE tag.
#
# Prerequisite: build & push that size first:
#   IMAGE_SIZE=750mb ./build-and-push.sh
#
# This is the CONTAINER (ECR image) deployment. See deploy-agentcore-zip.sh for
# the direct-code (zip) variant.
#
# Optional environment:
#   AGENT_RUNTIME_NAME   default: ac_ctn_x_lambda_agent  (ctn = container)
#   AGENTCORE_ROLE_NAME  default: base-agent-agentcore-role
#   IMAGE_SIZE           default: 200mb  (200mb | 500mb | 750mb | 1gb | 2gb —
#                        must already be pushed under this tag, see
#                        build-and-push.sh)
#   AGENTCORE_PLATFORM_VERSION  default: unset (V1 = Original Runtime, V2 = New Runtime;
#                        AGENTCORE_MANAGED_COMPUTE_VERSION still works as a
#                        deprecated alias)
#   AGENTCORE_NEW_DEPLOY default: unset (set to any value to force a brand-new
#                        runtime every run instead of updating the existing
#                        one with the same name)
#
# Usage: ./deploy-agentcore.sh
#        IMAGE_SIZE=1gb AGENTCORE_PLATFORM_VERSION=V2 ./deploy-agentcore.sh
#
# IMAGE_SIZE and AGENTCORE_PLATFORM_VERSION both drive naming: the
# default AGENT_RUNTIME_NAME gets "_<size>" and "_V1"/"_V2" appended
# automatically, e.g. ac_ctn_x_lambda_agent_750mb_V2, so running this once per
# size/version combination creates distinctly named runtimes ready to compare
# side by side without hand-managing AGENT_RUNTIME_NAME.
#
# AGENTCORE_PLATFORM_VERSION selects the API's own `platformVersion` field
# (V1 = Original Runtime, V2 = New Runtime) on create/update-agent-runtime. The
# `aws` CLI ships its own bundled botocore, frozen at CLI-release time, which
# can lag behind a freshly `pip install`-ed one — so create/update calls go
# through agentcore_boto3.py (plain boto3, using this repo's own venv) instead
# of the `aws` CLI, which also sidesteps a real gotcha: botocore always checks
# ~/.aws/models before its own bundled model data, so a stale custom model
# left there by an earlier preview workflow can silently shadow a
# newly-released field with no error beyond a confusing "Unknown parameter"
# — see agentcore_boto3.py's docstring. IAM role setup below is unaffected by
# any of this and still uses the `aws` CLI.
set -euo pipefail

# Captured BEFORE common.sh runs — see build-and-push.sh for why: common.sh
# defaults IMAGE_TAG to "latest" itself, which would make this script's own
# fallback to IMAGE_SIZE below never fire otherwise.
USER_IMAGE_TAG="${IMAGE_TAG:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require aws

# AGENTCORE_MANAGED_COMPUTE_VERSION is a deprecated alias for
# AGENTCORE_PLATFORM_VERSION (the name now matches the API's own
# `platformVersion` field) — honored for one release, with a warning.
if [ -n "${AGENTCORE_MANAGED_COMPUTE_VERSION:-}" ] && [ -z "${AGENTCORE_PLATFORM_VERSION:-}" ]; then
  warn "AGENTCORE_MANAGED_COMPUTE_VERSION is deprecated; use AGENTCORE_PLATFORM_VERSION instead"
  AGENTCORE_PLATFORM_VERSION="${AGENTCORE_MANAGED_COMPUTE_VERSION}"
fi

IMAGE_SIZE="${IMAGE_SIZE:-200mb}"
case "${IMAGE_SIZE}" in
  200mb|500mb|750mb|1gb|2gb) ;;
  *) die "unknown IMAGE_SIZE '${IMAGE_SIZE}' (expected 200mb|500mb|750mb|1gb|2gb)" ;;
esac
IMAGE_TAG="${USER_IMAGE_TAG:-${IMAGE_SIZE}}"

# Runtime names allow [a-zA-Z0-9_] only (no hyphens), so underscores are used.
AGENT_RUNTIME_NAME="${AGENT_RUNTIME_NAME:-ac_ctn_x_lambda_agent}"
AGENT_RUNTIME_NAME="${AGENT_RUNTIME_NAME}_${IMAGE_SIZE}"
if [ -n "${AGENTCORE_PLATFORM_VERSION:-}" ]; then
  AGENT_RUNTIME_NAME="${AGENT_RUNTIME_NAME}_${AGENTCORE_PLATFORM_VERSION}"
fi
if [ -n "${AGENTCORE_NEW_DEPLOY:-}" ]; then
  AGENT_RUNTIME_NAME="${AGENT_RUNTIME_NAME}_$(date +%s)"
fi
AGENTCORE_ROLE_NAME="${AGENTCORE_ROLE_NAME:-base-agent-agentcore-role}"

URI="$(image_uri)"
ACCOUNT_ID="$(account_id)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

# --- Least-privilege execution role (per AWS AgentCore Runtime docs) ---
TRUST_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "AssumeRolePolicy",
    "Effect": "Allow",
    "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
JSON
)

# Permissions policy: ECR image pull, CloudWatch Logs, X-Ray, metrics,
# workload access tokens, and Bedrock model invocation.
PERMISSIONS_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ECRImageAccess",
      "Effect": "Allow",
      "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
      "Resource": ["arn:aws:ecr:${AWS_REGION}:${ACCOUNT_ID}:repository/${ECR_REPOSITORY}"]
    },
    {
      "Sid": "ECRTokenAccess",
      "Effect": "Allow",
      "Action": ["ecr:GetAuthorizationToken"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
      "Resource": ["arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*"]
    },
    {
      "Effect": "Allow",
      "Action": ["logs:DescribeLogGroups"],
      "Resource": ["arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:*"]
    },
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": ["arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"]
    },
    {
      "Effect": "Allow",
      "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
      "Resource": ["*"]
    },
    {
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*",
      "Condition": { "StringEquals": { "cloudwatch:namespace": "bedrock-agentcore" } }
    },
    {
      "Sid": "GetAgentAccessToken",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:GetWorkloadAccessToken",
        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
        "bedrock-agentcore:GetWorkloadAccessTokenForUserId"
      ],
      "Resource": [
        "arn:aws:bedrock-agentcore:${AWS_REGION}:${ACCOUNT_ID}:workload-identity-directory/default",
        "arn:aws:bedrock-agentcore:${AWS_REGION}:${ACCOUNT_ID}:workload-identity-directory/default/workload-identity/${AGENT_RUNTIME_NAME}-*"
      ]
    },
    {
      "Sid": "BedrockModelInvocation",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:${AWS_REGION}:${ACCOUNT_ID}:*"
      ]
    }
  ]
}
JSON
)

AGENTCORE_ROLE_ARN="$(ensure_role \
  "${AGENTCORE_ROLE_NAME}" "${TRUST_POLICY}" \
  "${AGENTCORE_ROLE_NAME}-policy" "${PERMISSIONS_POLICY}")"

log "Runtime name: ${AGENT_RUNTIME_NAME}"
log "Image size:   ${IMAGE_SIZE}"
log "Container:    ${URI}"
log "Role:         ${AGENTCORE_ROLE_ARN}"

PY="$(agentcore_python)"

# find-arn uses boto3's own paginator (client.get_paginator("list_agent_runtimes")),
# so it never hits the aws-CLI page-size/--query-per-page bug this project hit
# earlier (a >10-runtime account silently corrupting the create-vs-update
# decision). Prints nothing (empty string) when no runtime with this name
# exists yet -- no "None" sentinel needed.
EXISTING_ARN="$("${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" find-arn \
  --region "${AWS_REGION}" --name "${AGENT_RUNTIME_NAME}")"

ARTIFACT="{\"containerConfiguration\":{\"containerUri\":\"${URI}\"}}"
LIFECYCLE="$(agentcore_lifecycle_json)"
log "Lifecycle:    ${LIFECYCLE}"

PLATFORM_VERSION_JSON=""
if [ -n "${AGENTCORE_PLATFORM_VERSION:-}" ]; then
  log "Platform version: ${AGENTCORE_PLATFORM_VERSION}"
  PLATFORM_VERSION_JSON=",\"platformVersion\":\"${AGENTCORE_PLATFORM_VERSION}\""
fi

BODY_FILE="${TMP_DIR}/agent-runtime-body.json"
if [ -n "${EXISTING_ARN}" ]; then
  RUNTIME_ID="${EXISTING_ARN##*/}"
  log "Updating existing runtime ${EXISTING_ARN}..."
  cat > "${BODY_FILE}" <<JSON
{
  "agentRuntimeArtifact": ${ARTIFACT},
  "roleArn": "${AGENTCORE_ROLE_ARN}",
  "networkConfiguration": {"networkMode": "PUBLIC"},
  "lifecycleConfiguration": ${LIFECYCLE}${PLATFORM_VERSION_JSON}
}
JSON
  "${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" update \
    --region "${AWS_REGION}" \
    --agent-runtime-id "${RUNTIME_ID}" --body-file "${BODY_FILE}"
else
  log "Creating new runtime..."
  cat > "${BODY_FILE}" <<JSON
{
  "agentRuntimeName": "${AGENT_RUNTIME_NAME}",
  "agentRuntimeArtifact": ${ARTIFACT},
  "roleArn": "${AGENTCORE_ROLE_ARN}",
  "networkConfiguration": {"networkMode": "PUBLIC"},
  "lifecycleConfiguration": ${LIFECYCLE}${PLATFORM_VERSION_JSON}
}
JSON
  "${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" create \
    --region "${AWS_REGION}" \
    --body-file "${BODY_FILE}"
fi

log "Done."
