#!/usr/bin/env bash
# Configuration shared by deploy.sh, invoke.sh and destroy.sh. The only
# setting is REGION (default us-east-1); pass the same one to all three.
set -euo pipefail

REGION="${REGION:-us-east-1}"

ROLE_NAME="decider-hooks-agentcore-role-${REGION}"   # per region: its policies are region-scoped
ECR_REPOSITORY=strands-decider-hooks
IMAGE_TAG=latest
RUNTIME_NAME=strandsDeciderHooks
STATE_FILE="/tmp/strands-decider-hooks-state-${REGION}"

# Committed baseline per session: minimumMemoryInMiB 8192 gives 8 GB and 2 vCPU
# (1 vCPU per 4 GB).
# Used by runtime_body below and by usage.sh's cost estimate.
MIN_MEMORY_MIB=8192
BASELINE_VCPU=2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOTO="${SCRIPT_DIR}/agentcore_boto3.py"

log() { printf '\033[1;36m[decider-hooks]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[decider-hooks]\033[0m %s\n' "$*" >&2; exit 1; }

# The python3 of the active environment (uv, venv, conda...). It needs boto3
# 1.44.0 or later, the first release that models the hook and memory fields.
PY="$(command -v python3)" || die "python3 not found"
"${PY}" -c 'import sys, boto3; v = tuple(map(int, boto3.__version__.split(".")[:2])); sys.exit(v < (1, 44))' 2>/dev/null \
  || die "${PY} needs boto3 >= 1.44.0: pip install -r infra/requirements.txt (or uv pip install -r infra/requirements.txt)"
log "region ${REGION}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPOSITORY}:${IMAGE_TAG}"

# Runtime settings sent on create and update. The warmup hook's timeout isn't
# a field: the platform fixes it at 120 s for both calls together.
runtime_body() {
  local role_arn="$1"
  cat <<JSON
{
  "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": "${IMAGE_URI}"}},
  "roleArn": "${role_arn}",
  "networkConfiguration": {"networkMode": "PUBLIC"},
  "platformVersion": "V2",
  "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 300, "maxLifetime": 900},
  "resourceConfigurations": [{"minimumMemoryInMiB": ${MIN_MEMORY_MIB}}],
  "hooksConfiguration": {
    "build": {"warmup": {"action": {"httpPost": {"path": "/warmup", "port": 8080}}}}
  }
}
JSON
}

# Only AgentCore resources in this account and region can assume the role
# (confused-deputy protection).
trust_policy() {
  cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {"aws:SourceAccount": "${ACCOUNT_ID}"},
      "ArnLike": {"aws:SourceArn": "arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:*"}
    }
  }]
}
JSON
}

# Inference runs inside the container, so the role only needs to pull the
# image and write logs, traces and metrics.
permissions_policy() {
  cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
     "Resource": "arn:aws:ecr:${REGION}:${ACCOUNT_ID}:repository/${ECR_REPOSITORY}"},
    {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
    {"Effect": "Allow", "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
     "Resource": ["arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*",
                  "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"]},
    {"Effect": "Allow", "Action": "logs:DescribeLogGroups", "Resource": "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:*"},
    {"Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
     "Resource": "*"},
    {"Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*",
     "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}}
  ]
}
JSON
}

runtime_status() {
  "${PY}" "${BOTO}" get --region "${REGION}" --agent-runtime-id "$1" 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status","UNKNOWN")); print(d.get("failureReason") or "", file=sys.stderr)'
}

# Per-session CPU and memory usage (USAGE_LOGS), delivered to a log group.
usage_log_group() { echo "/aws/vendedlogs/bedrock-agentcore/runtimes/USAGE_LOGS/$1"; }

enable_usage_logs() {
  local runtime_id="$1"
  local group; group="$(usage_log_group "${runtime_id}")"
  local source="${runtime_id}-usage"
  aws logs create-log-group --region "${REGION}" --log-group-name "${group}" 2>/dev/null || true
  aws logs put-delivery-source --region "${REGION}" --name "${source}" --log-type USAGE_LOGS \
    --resource-arn "arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${runtime_id}" >/dev/null
  local dest_arn
  dest_arn="$(aws logs put-delivery-destination --region "${REGION}" --name "${source}-cwl" \
    --delivery-destination-configuration "destinationResourceArn=arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:${group}" \
    --query 'deliveryDestination.arn' --output text)"
  if ! aws logs describe-deliveries --region "${REGION}" \
      --query "deliveries[?deliverySourceName=='${source}'].id" --output text | grep -q .; then
    aws logs create-delivery --region "${REGION}" --delivery-source-name "${source}" \
      --delivery-destination-arn "${dest_arn}" >/dev/null
  fi
  log "usage logs -> ${group}"
}

disable_usage_logs() {
  local runtime_id="$1" source="$1-usage" id
  for id in $(aws logs describe-deliveries --region "${REGION}" \
      --query "deliveries[?deliverySourceName=='${source}'].id" --output text); do
    aws logs delete-delivery --region "${REGION}" --id "${id}" || true
  done
  aws logs delete-delivery-source --region "${REGION}" --name "${source}" 2>/dev/null || true
  aws logs delete-delivery-destination --region "${REGION}" --name "${source}-cwl" 2>/dev/null || true
  aws logs delete-log-group --region "${REGION}" --log-group-name "$(usage_log_group "${runtime_id}")" 2>/dev/null || true
}
