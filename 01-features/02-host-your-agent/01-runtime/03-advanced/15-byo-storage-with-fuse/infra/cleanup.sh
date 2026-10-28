#!/usr/bin/env bash
# Deletes the runtime, bucket, and the agent's execution role. Dry run
# by default.
#
# Usage: ./cleanup.sh                  # dry run
#        ./cleanup.sh --yes            # delete everything
#        ./cleanup.sh --yes --purge-ecr   # also delete the ECR repo/image
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
require aws

[ -f "${FUSE_STATE_FILE}" ] && source "${FUSE_STATE_FILE}"
DRY_RUN=1; PURGE_ECR=0
for arg in "$@"; do
  case "${arg}" in
    --yes) DRY_RUN=0 ;;
    --purge-ecr) PURGE_ECR=1 ;;
    *) die "usage: $0 [--yes] [--purge-ecr]" ;;
  esac
done
[ "${DRY_RUN}" -eq 1 ] && log "DRY RUN -- pass --yes to actually delete."

do_or_show() {
  local desc="$1"; shift
  if [ "${DRY_RUN}" -eq 1 ]; then log "would delete: ${desc}"; return; fi
  log "deleting: ${desc}"
  "$@" || warn "  failed (already gone?): ${desc}"
}

if [ -n "${FUSE_RUNTIME_ID:-}" ]; then
  do_or_show "runtime ${FUSE_RUNTIME_ID}" "${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" delete \
    --region "${AWS_REGION}" --agent-runtime-id "${FUSE_RUNTIME_ID}"
fi

if [ -n "${FUSE_BUCKET:-}" ] && aws s3api head-bucket --bucket "${FUSE_BUCKET}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  do_or_show "S3 bucket ${FUSE_BUCKET}" bash -c "
    aws s3 rm 's3://${FUSE_BUCKET}' --recursive --region '${AWS_REGION}' &&
    aws s3api delete-bucket --bucket '${FUSE_BUCKET}' --region '${AWS_REGION}'"
fi

if aws iam get-role --role-name "${AGENTCORE_ROLE_NAME}" >/dev/null 2>&1; then
  if [ "${DRY_RUN}" -eq 1 ]; then
    log "would delete: IAM role ${AGENTCORE_ROLE_NAME}"
  else
    for p in $(aws iam list-role-policies --role-name "${AGENTCORE_ROLE_NAME}" --query 'PolicyNames' --output text 2>/dev/null); do
      aws iam delete-role-policy --role-name "${AGENTCORE_ROLE_NAME}" --policy-name "${p}" || true
    done
    do_or_show "IAM role ${AGENTCORE_ROLE_NAME}" aws iam delete-role --role-name "${AGENTCORE_ROLE_NAME}"
  fi
fi

if [ "${PURGE_ECR}" -eq 1 ] && aws ecr describe-repositories --region "${AWS_REGION}" --repository-names "${ECR_REPOSITORY}" >/dev/null 2>&1; then
  do_or_show "ECR repository ${ECR_REPOSITORY}" aws ecr delete-repository --region "${AWS_REGION}" --repository-name "${ECR_REPOSITORY}" --force
fi

if [ "${DRY_RUN}" -eq 0 ]; then
  log "done. removing state file."
  rm -f "${FUSE_STATE_FILE}"
fi
