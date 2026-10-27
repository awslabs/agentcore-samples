#!/usr/bin/env bash
# Shared config + helpers for deploy.sh, test.sh, exec-cmd.sh, cleanup.sh.
set -euo pipefail

# ---- Config (override via env) ----
AWS_REGION="${AWS_REGION:-us-east-1}"
IMAGE_PLATFORM="${IMAGE_PLATFORM:-linux/arm64}"
AGENTCORE_ROLE_NAME="${AGENTCORE_ROLE_NAME:-fuse-example-agentcore-role}"
AGENTCORE_IDLE_TIMEOUT="${AGENTCORE_IDLE_TIMEOUT:-300}"   # seconds, 60..28800
AGENTCORE_MAX_LIFETIME="${AGENTCORE_MAX_LIFETIME:-900}"   # seconds, 60..28800
FUSE_BUCKET_PREFIX="${FUSE_BUCKET_PREFIX:-fuse-per-user}"
ECR_REPOSITORY="${ECR_REPOSITORY:-fuse-example-agent}"
IMAGE_TAG="${IMAGE_TAG:-fuse-example}"
RUNTIME_NAME="${RUNTIME_NAME:-fusePerUserIsolation}"
FUSE_STATE_FILE="${FUSE_STATE_FILE:-/tmp/fuse-per-user-isolation-state}"
FUSE_MODEL_ID="${FUSE_MODEL_ID:-us.anthropic.claude-haiku-5-5}"   # --effort off always goes with it, see server.js

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_CONTEXT="${BUILD_CONTEXT:-${SCRIPT_DIR}/../agent}"
PY="${SCRIPT_DIR}/../.venv/bin/python3"

# Session context keys the role + bucket policies key on -- see ../design-docs.
CTXKEY_SESSION_KEY_NAME="aws:AgentCoreRuntimeSourceSid"
CTXKEY_RUNTIME_KEY_NAME="aws:AgentCoreRuntimeSourceArn"

log()  { printf '\033[1;35m[fuse]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[fuse]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[fuse]\033[0m %s\n' "$*" >&2; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

account_id() { aws sts get-caller-identity --query Account --output text; }
ecr_registry() { echo "$(account_id).dkr.ecr.${AWS_REGION}.amazonaws.com"; }
image_uri() { echo "$(ecr_registry)/${ECR_REPOSITORY}:${IMAGE_TAG}"; }

# Deterministic session id from a username -- the mount prefix IS the
# session id (sessions/<id>/), so there's nothing to provision or look up.
session_id_for() {
  python3 -c "
import hashlib, sys
u = sys.argv[1]
print(f'user-{u}-{hashlib.sha256(u.encode()).hexdigest()}')
" "$1"
}

agentcore_lifecycle_json() {
  echo "{\"idleRuntimeSessionTimeout\":${AGENTCORE_IDLE_TIMEOUT},\"maxLifetime\":${AGENTCORE_MAX_LIFETIME}}"
}

# Create or update an IAM role + its one inline policy. Echoes the role ARN.
# Args: <role-name> <trust-policy-json> <inline-policy-name> <inline-policy-json>
ensure_role() {
  local role_name="$1" trust_policy="$2" policy_name="$3" policy_doc="$4" is_new=0
  if aws iam get-role --role-name "${role_name}" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "${role_name}" --policy-document "${trust_policy}" >/dev/null
  else
    aws iam create-role --role-name "${role_name}" --assume-role-policy-document "${trust_policy}" >/dev/null
    is_new=1
  fi
  aws iam put-role-policy --role-name "${role_name}" --policy-name "${policy_name}" --policy-document "${policy_doc}" >/dev/null
  log "role ${role_name}: waiting for propagation..."
  sleep $([ "${is_new}" -eq 1 ] && echo 15 || echo 10)
  aws iam get-role --role-name "${role_name}" --query 'Role.Arn' --output text
}

agent_trust_policy() {
  cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
JSON
}

# ECR/logs/X-Ray/metrics + bedrock:InvokeModel for the Strands harness,
# plus the identity-side half of the per-session S3 scoping. deploy.sh's
# bucket policy is the second, defense-in-depth layer on top of this.
agent_permissions_policy() {
  local account_id="$1"
  local bucket="${FUSE_BUCKET_PREFIX}-${account_id}-${AWS_REGION}"
  # Cross-region inference profiles (FUSE_MODEL_ID's "us." prefix) can
  # dispatch the actual InvokeModel call to the underlying foundation
  # model in any region in that set, so the foundation-model Resource
  # below still wildcards region -- but both entries are scoped to the
  # one configured model, not every Anthropic model/profile in the account.
  local model_id="${FUSE_MODEL_ID#*.}"
  cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
      "Resource": "arn:aws:ecr:${AWS_REGION}:${account_id}:repository/${ECR_REPOSITORY}" },
    { "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
    { "Effect": "Allow", "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
      "Resource": "arn:aws:logs:${AWS_REGION}:${account_id}:log-group:/aws/bedrock-agentcore/runtimes/*" },
    { "Effect": "Allow", "Action": "logs:DescribeLogGroups",
      "Resource": "arn:aws:logs:${AWS_REGION}:${account_id}:log-group:*" },
    { "Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:${AWS_REGION}:${account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*" },
    { "Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
      "Resource": "*" },
    { "Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*",
      "Condition": { "StringEquals": { "cloudwatch:namespace": "bedrock-agentcore" } } },
    { "Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:${AWS_REGION}:${account_id}:inference-profile/${FUSE_MODEL_ID}",
        "arn:aws:bedrock:*::foundation-model/${model_id}"
      ] },
    { "Sid": "AllowListOwnSessionIdentitySide", "Effect": "Allow", "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${bucket}",
      "Condition": { "StringLike": { "s3:prefix": "sessions/\${${CTXKEY_SESSION_KEY_NAME}}/*" } } },
    { "Sid": "AllowOwnSessionObjectsIdentitySide", "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"],
      "Resource": "arn:aws:s3:::${bucket}/sessions/\${${CTXKEY_SESSION_KEY_NAME}}/*" }
  ]
}
JSON
}
