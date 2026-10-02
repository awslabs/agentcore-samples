#!/usr/bin/env bash
# Deploy the base-agent to Amazon Bedrock AgentCore Runtime via DIRECT CODE
# (zip) deployment — the higher-scaling alternative to the container variant.
# Prod only.
#
# Per the AWS docs, zip deployment sustains ~25 new sessions/sec vs ~1.6/sec for
# container deployment, and updates are much faster. Package limit: 250 MB
# zipped / 750 MB unzipped, arm64 only.
#
# Flow (per the AWS AgentCore direct-code-deployment docs):
#   1. Create/update a least-privilege execution role (+ S3 read on the code bucket).
#   2. Install arm64 deps into a build dir, add main.py, zip it.
#   3. Upload the zip to S3.
#   4. create/update_agent_runtime with codeConfiguration (PYTHON_3_13, main.py).
#
# The same agent (agent.py, copied to main.py) is used — it exposes /invocations
# and /ping, which satisfies the runtime contract for zip deployment too.
#
# Optional environment:
#   AGENT_RUNTIME_NAME    default: ac_zip_x_lambda_bench_agent  (zip variant)
#   AGENTCORE_ZIP_ROLE_NAME  default: base-agent-agentcore-zip-role
#   CODE_BUCKET           default: bedrock-agentcore-code-<account>-<region>
#   PY_RUNTIME            default: PYTHON_3_13
#   AGENTCORE_PLATFORM_VERSION  default: unset (V1 = Original Runtime, V2 = New Runtime;
#                         AGENTCORE_MANAGED_COMPUTE_VERSION still works as a
#                         deprecated alias)
#   AGENTCORE_NEW_DEPLOY  default: unset (set to any value to force a brand-new
#                         runtime every run instead of updating the existing one
#                         with the same name)
#
# Usage: ./deploy-agentcore-zip.sh
#        AGENTCORE_PLATFORM_VERSION=V2 ./deploy-agentcore-zip.sh
#        AGENTCORE_NEW_DEPLOY=1 ./deploy-agentcore-zip.sh  # always create, never update
#
# AGENTCORE_PLATFORM_VERSION also drives naming: when set, "_V1" or
# "_V2" is appended to AGENT_RUNTIME_NAME automatically, so running this once
# with V1 and once with V2 creates two distinctly named runtimes ready to
# compare, without having to set AGENT_RUNTIME_NAME by hand each time.
#
# AGENTCORE_NEW_DEPLOY: the create-vs-update decision below is driven purely by
# whether an existing runtime is found with the same agentRuntimeName. Runtime
# names must be unique per account/region, so the only way to force a genuinely
# new runtime (new agentRuntimeId, fresh cold state) is to give it a name that
# has never been used before. When set, a short unique suffix is appended to
# AGENT_RUNTIME_NAME so the existing-runtime lookup below always misses.
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
# — see agentcore_boto3.py's docstring. IAM role / S3 bucket setup below is
# unaffected by any of this and still uses the `aws` CLI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require aws
require zip

# AGENTCORE_MANAGED_COMPUTE_VERSION is a deprecated alias for
# AGENTCORE_PLATFORM_VERSION (the name now matches the API's own
# `platformVersion` field) — honored for one release, with a warning.
if [ -n "${AGENTCORE_MANAGED_COMPUTE_VERSION:-}" ] && [ -z "${AGENTCORE_PLATFORM_VERSION:-}" ]; then
  warn "AGENTCORE_MANAGED_COMPUTE_VERSION is deprecated; use AGENTCORE_PLATFORM_VERSION instead"
  AGENTCORE_PLATFORM_VERSION="${AGENTCORE_MANAGED_COMPUTE_VERSION}"
fi

# Runtime names allow [a-zA-Z0-9_] only (no hyphens).
AGENT_RUNTIME_NAME="${AGENT_RUNTIME_NAME:-ac_zip_x_lambda_bench_agent}"
if [ -n "${AGENTCORE_PLATFORM_VERSION:-}" ]; then
  AGENT_RUNTIME_NAME="${AGENT_RUNTIME_NAME}_${AGENTCORE_PLATFORM_VERSION}"
fi
if [ -n "${AGENTCORE_NEW_DEPLOY:-}" ]; then
  AGENT_RUNTIME_NAME="${AGENT_RUNTIME_NAME}_$(date +%s)"
fi
AGENTCORE_ZIP_ROLE_NAME="${AGENTCORE_ZIP_ROLE_NAME:-base-agent-agentcore-zip-role}"
PY_RUNTIME="${PY_RUNTIME:-PYTHON_3_13}"
PY_VERSION="${PY_VERSION:-3.13}"

ACCOUNT_ID="$(account_id)"
CODE_BUCKET="${CODE_BUCKET:-bedrock-agentcore-code-${ACCOUNT_ID}-${AWS_REGION}}"
ARTIFACT_KEY="${AGENT_RUNTIME_NAME}/deployment_package.zip"

# --- Least-privilege execution role (per AWS AgentCore Runtime docs) ---
# Same as the container role but with S3 read on the code bucket instead of ECR.
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

PERMISSIONS_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CodeArtifactAccess",
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::${CODE_BUCKET}/*"]
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

ROLE_ARN="$(ensure_role \
  "${AGENTCORE_ZIP_ROLE_NAME}" "${TRUST_POLICY}" \
  "${AGENTCORE_ZIP_ROLE_NAME}-policy" "${PERMISSIONS_POLICY}")"

log "Runtime name: ${AGENT_RUNTIME_NAME}"
log "Runtime:      ${PY_RUNTIME}"
log "Code bucket:  ${CODE_BUCKET}"
log "Role:         ${ROLE_ARN}"

# --- Ensure the S3 code bucket exists (idempotent) ---
if ! aws s3api head-bucket --bucket "${CODE_BUCKET}" >/dev/null 2>&1; then
  log "Creating S3 bucket '${CODE_BUCKET}'..."
  if [ "${AWS_REGION}" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "${CODE_BUCKET}" --region "${AWS_REGION}" >/dev/null
  else
    aws s3api create-bucket --bucket "${CODE_BUCKET}" --region "${AWS_REGION}" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}" >/dev/null
  fi
fi

# --- Build the zip: arm64 deps + main.py (== agent.py) ---
# AgentCore Runtime is arm64/Linux only, so install wheels for that platform.
log "Building deployment package (arm64 wheels + main.py)..."
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
PKG_DIR="${TMP_DIR}/deployment_package"
mkdir -p "${PKG_DIR}"

uv pip install \
  --python-platform aarch64-manylinux2014 \
  --python-version "${PY_VERSION}" \
  --target="${PKG_DIR}" \
  --only-binary=:all: \
  -r "${BUILD_CONTEXT}/pyproject.toml" >/dev/null

# The entrypoint file must be at the zip root; reuse the same agent as main.py.
cp "${BUILD_CONTEXT}/agent.py" "${PKG_DIR}/main.py"

# POSIX perms the runtime requires: 644 files, 755 dirs.
find "${PKG_DIR}" -type d -exec chmod 755 {} +
find "${PKG_DIR}" -type f -exec chmod 644 {} +

( cd "${PKG_DIR}" && zip -qr "${TMP_DIR}/deployment_package.zip" . )
ZIP_SIZE=$(du -m "${TMP_DIR}/deployment_package.zip" | cut -f1)
log "Package size: ${ZIP_SIZE} MB (limit 250 MB zipped)"
if [ "${ZIP_SIZE}" -gt 250 ]; then
  die "deployment package exceeds the 250 MB zipped limit for direct code deploy"
fi

log "Uploading to s3://${CODE_BUCKET}/${ARTIFACT_KEY}..."
aws s3 cp "${TMP_DIR}/deployment_package.zip" \
  "s3://${CODE_BUCKET}/${ARTIFACT_KEY}" >/dev/null

# --- Create or update the runtime (code deployment) ---
ARTIFACT="$(cat <<JSON
{"codeConfiguration": {
  "code": {"s3": {"bucket": "${CODE_BUCKET}", "prefix": "${ARTIFACT_KEY}"}},
  "runtime": "${PY_RUNTIME}",
  "entryPoint": ["main.py"]
}}
JSON
)"

PY="$(agentcore_python)"

# find-arn uses boto3's own paginator (client.get_paginator("list_agent_runtimes")),
# so it never hits the aws-CLI page-size/--query-per-page bug this project hit
# earlier (a >10-runtime account silently corrupting the create-vs-update
# decision). Prints nothing (empty string) when no runtime with this name
# exists yet -- no "None" sentinel needed.
EXISTING_ARN="$("${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" find-arn \
  --region "${AWS_REGION}" --name "${AGENT_RUNTIME_NAME}")"

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
  "roleArn": "${ROLE_ARN}",
  "networkConfiguration": {"networkMode": "PUBLIC"},
  "lifecycleConfiguration": ${LIFECYCLE}${PLATFORM_VERSION_JSON}
}
JSON
  "${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" update \
    --region "${AWS_REGION}" \
    --agent-runtime-id "${RUNTIME_ID}" --body-file "${BODY_FILE}"
else
  log "Creating new runtime (code deployment)..."
  cat > "${BODY_FILE}" <<JSON
{
  "agentRuntimeName": "${AGENT_RUNTIME_NAME}",
  "agentRuntimeArtifact": ${ARTIFACT},
  "roleArn": "${ROLE_ARN}",
  "networkConfiguration": {"networkMode": "PUBLIC"},
  "lifecycleConfiguration": ${LIFECYCLE}${PLATFORM_VERSION_JSON}
}
JSON
  "${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" create \
    --region "${AWS_REGION}" \
    --body-file "${BODY_FILE}"
fi

log "Done."
