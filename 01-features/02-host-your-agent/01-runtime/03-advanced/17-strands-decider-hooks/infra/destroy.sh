#!/usr/bin/env bash
# Deletes everything deploy.sh created: the usage log delivery, the runtime,
# the IAM role and the ECR repository.
# Usage: ./destroy.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

ARN="$("${PY}" "${BOTO}" find-arn --region "${REGION}" --name "${RUNTIME_NAME}")"
if [ -n "${ARN}" ]; then
  log "deleting usage log delivery for ${ARN##*/}"
  disable_usage_logs "${ARN##*/}"
  log "deleting runtime ${ARN##*/}"
  "${PY}" "${BOTO}" delete --region "${REGION}" --agent-runtime-id "${ARN##*/}" >/dev/null
fi
if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  log "deleting role ${ROLE_NAME}"
  aws iam delete-role-policy --role-name "${ROLE_NAME}" --policy-name "${ROLE_NAME}-policy" 2>/dev/null || true
  aws iam delete-role --role-name "${ROLE_NAME}"
fi
if aws ecr describe-repositories --repository-names "${ECR_REPOSITORY}" --region "${REGION}" >/dev/null 2>&1; then
  log "deleting ECR repository ${ECR_REPOSITORY}"
  aws ecr delete-repository --repository-name "${ECR_REPOSITORY}" --region "${REGION}" --force >/dev/null
fi
rm -f "${STATE_FILE}"
log "done"
