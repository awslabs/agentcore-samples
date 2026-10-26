#!/usr/bin/env bash
# Delete everything this sample's deploy scripts can create: the AgentCore
# runtimes (both legs, every size/version you deployed), the ECR repository
# and its images, the S3 code bucket, and the two IAM roles.
#
# Every session this sample creates bills for its FULL lifecycle (boot,
# idle, until AGENTCORE_IDLE_TIMEOUT reaps it or you delete the runtime) —
# see the top-level README's Cost section. Nothing else in this sample
# deletes these resources for you.
#
# Defaults to a DRY RUN (lists what would be deleted, deletes nothing).
# Pass --yes to actually delete.
#
# Usage: ./cleanup.sh          # dry run
#        ./cleanup.sh --yes    # actually delete
#
# Scope: only matches the DEFAULT resource names this sample's scripts use
# (AGENT_RUNTIME_NAME prefixes ac_ctn_x_lambda_agent* / ac_zip_x_lambda_bench_agent*,
# ECR_REPOSITORY, the bedrock-agentcore-code-<account>-<region> bucket, and
# the two default role names). If you overrode any of those env vars when
# deploying, override them the same way here, or delete that resource by hand.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require aws

DRY_RUN=1
case "${1:-}" in
  --yes) DRY_RUN=0 ;;
  "") ;;
  *) die "usage: $0 [--yes]" ;;
esac

ACCOUNT_ID="$(account_id)"
CODE_BUCKET="${CODE_BUCKET:-bedrock-agentcore-code-${ACCOUNT_ID}-${AWS_REGION}}"
AGENTCORE_ROLE_NAME="${AGENTCORE_ROLE_NAME:-base-agent-agentcore-role}"
AGENTCORE_ZIP_ROLE_NAME="${AGENTCORE_ZIP_ROLE_NAME:-base-agent-agentcore-zip-role}"

if [ "${DRY_RUN}" -eq 1 ]; then
  log "DRY RUN — nothing will be deleted. Re-run with --yes to actually delete."
fi

do_or_show() {
  # $1 = human description, rest = the actual command to run when not a dry run.
  local desc="$1"; shift
  if [ "${DRY_RUN}" -eq 1 ]; then
    log "would delete: ${desc}"
  else
    log "deleting: ${desc}"
    "$@" || warn "  failed (already gone? continuing): ${desc}"
  fi
}

# --- AgentCore runtimes (both legs, every size/version) ---
log "Scanning for AgentCore runtimes (prefixes: ac_ctn_x_lambda_agent*, ac_zip_x_lambda_bench_agent*)..."
RUNTIMES="$(aws bedrock-agentcore-control list-agent-runtimes \
  --region "${AWS_REGION}" --page-size 100 \
  --query "agentRuntimes[?starts_with(agentRuntimeName, 'ac_ctn_x_lambda_agent') || starts_with(agentRuntimeName, 'ac_zip_x_lambda_bench_agent')].[agentRuntimeId,agentRuntimeName]" \
  --output text 2>/dev/null || true)"

if [ -z "${RUNTIMES}" ]; then
  log "no matching runtimes found."
else
  while IFS=$'\t' read -r runtime_id runtime_name; do
    [ -z "${runtime_id}" ] && continue
    do_or_show "runtime ${runtime_name} (${runtime_id})" \
      aws bedrock-agentcore-control delete-agent-runtime \
      --region "${AWS_REGION}" --agent-runtime-id "${runtime_id}"
  done <<< "${RUNTIMES}"
fi

# --- ECR repository (image(s) included via --force) ---
if aws ecr describe-repositories --region "${AWS_REGION}" \
    --repository-names "${ECR_REPOSITORY}" >/dev/null 2>&1; then
  do_or_show "ECR repository ${ECR_REPOSITORY} (and its images)" \
    aws ecr delete-repository --region "${AWS_REGION}" \
    --repository-name "${ECR_REPOSITORY}" --force
else
  log "ECR repository ${ECR_REPOSITORY} not found, skipping."
fi

# --- S3 code bucket (zip leg) ---
if aws s3api head-bucket --bucket "${CODE_BUCKET}" >/dev/null 2>&1; then
  do_or_show "S3 bucket ${CODE_BUCKET} (and its objects)" \
    aws s3 rb "s3://${CODE_BUCKET}" --force
else
  log "S3 bucket ${CODE_BUCKET} not found, skipping."
fi

# --- IAM roles ---
delete_role() {
  local role_name="$1"
  if ! aws iam get-role --role-name "${role_name}" >/dev/null 2>&1; then
    log "IAM role ${role_name} not found, skipping."
    return
  fi
  if [ "${DRY_RUN}" -eq 1 ]; then
    log "would delete: IAM role ${role_name} (and its inline policies)"
    return
  fi
  log "deleting: IAM role ${role_name} (and its inline policies)"
  local policy_name
  for policy_name in $(aws iam list-role-policies --role-name "${role_name}" \
      --query 'PolicyNames' --output text 2>/dev/null); do
    aws iam delete-role-policy --role-name "${role_name}" --policy-name "${policy_name}" \
      || warn "  failed to delete inline policy ${policy_name} on ${role_name}"
  done
  aws iam delete-role --role-name "${role_name}" \
    || warn "  failed to delete role ${role_name}"
}

delete_role "${AGENTCORE_ROLE_NAME}"
delete_role "${AGENTCORE_ZIP_ROLE_NAME}"

if [ "${DRY_RUN}" -eq 1 ]; then
  log "Dry run done. Re-run with --yes to actually delete the above."
else
  log "Done."
fi
