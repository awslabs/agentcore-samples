#!/usr/bin/env bash
# Builds and pushes the image, then creates the runtime (or updates it).
# Usage: ./deploy.sh [--skip-build]
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

if [ "${1:-}" != "--skip-build" ]; then
  log "1/3 image ${IMAGE_URI}"
  aws ecr describe-repositories --repository-names "${ECR_REPOSITORY}" --region "${REGION}" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "${ECR_REPOSITORY}" --region "${REGION}" >/dev/null
  aws ecr get-login-password --region "${REGION}" \
    | docker login --username AWS --password-stdin "${IMAGE_URI%%/*}" >/dev/null
  docker buildx inspect decider-hooks-builder >/dev/null 2>&1 || docker buildx create --name decider-hooks-builder >/dev/null
  docker buildx inspect --bootstrap decider-hooks-builder >/dev/null \
    || die "couldn't start the buildx builder (it pulls moby/buildkit on first use); check Docker and retry"
  # --provenance/--sbom=false push a single manifest; the default image index
  # with an attestation manifest fails runtime creation (LayersNotFoundException).
  docker buildx build --builder decider-hooks-builder --platform linux/arm64 \
    --provenance=false --sbom=false --tag "${IMAGE_URI}" --push "${SCRIPT_DIR}/../agent"
else
  log "1/3 image ${IMAGE_URI} (skipping build)"
fi

log "2/3 IAM role ${ROLE_NAME}"
if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "${ROLE_NAME}" --policy-document "$(trust_policy)"
else
  aws iam create-role --role-name "${ROLE_NAME}" --assume-role-policy-document "$(trust_policy)" >/dev/null
fi
aws iam put-role-policy --role-name "${ROLE_NAME}" --policy-name "${ROLE_NAME}-policy" --policy-document "$(permissions_policy)"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
sleep 15  # IAM propagation

log "3/3 runtime ${RUNTIME_NAME}"
BODY="$(mktemp)"; trap 'rm -f "${BODY}"' EXIT
runtime_body "${ROLE_ARN}" > "${BODY}"
EXISTING_ARN="$("${PY}" "${BOTO}" find-arn --region "${REGION}" --name "${RUNTIME_NAME}")"
if [ -n "${EXISTING_ARN}" ]; then
  RUNTIME_ID="${EXISTING_ARN##*/}"
  "${PY}" "${BOTO}" update --region "${REGION}" --agent-runtime-id "${RUNTIME_ID}" --body-file "${BODY}" >/dev/null
else
  python3 -c "import json,sys; b=json.load(open(sys.argv[1])); b['agentRuntimeName']=sys.argv[2]; json.dump(b, open(sys.argv[1],'w'))" "${BODY}" "${RUNTIME_NAME}"
  RUNTIME_ID="$("${PY}" "${BOTO}" create --region "${REGION}" --body-file "${BODY}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["agentRuntimeId"])')"
fi
echo "RUNTIME_ID=${RUNTIME_ID}" > "${STATE_FILE}"

for i in $(seq 1 45); do
  STATUS="$(runtime_status "${RUNTIME_ID}" 2>/dev/null)"
  log "[$((i * 20))s] ${STATUS}"
  case "${STATUS}" in CREATING|UPDATING) sleep 20 ;; *) break ;; esac
done

if [ "${STATUS}" != "READY" ]; then
  log "failureReason: $(runtime_status "${RUNTIME_ID}" 2>&1 >/dev/null)"
  log "warmup hook lines from the build logs:"
  aws logs filter-log-events --region "${REGION}" --log-group-name "/aws/bedrock-agentcore/runtimes/${RUNTIME_ID}-DEFAULT" \
    --log-stream-name-prefix build-logs- --filter-pattern '"warmup"' --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n' >&2 || true
  die "runtime ${RUNTIME_ID} is ${STATUS}"
fi
enable_usage_logs "${RUNTIME_ID}"
log "ready: ${RUNTIME_ID}. Try ./invoke.sh, then ./usage.sh"
