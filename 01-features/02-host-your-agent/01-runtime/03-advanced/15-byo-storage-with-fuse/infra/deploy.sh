#!/usr/bin/env bash
# Deploys all infra: build+push image -> IAM role -> S3 bucket -> AgentCore
# runtime -> bucket policy (needs the runtime ARN, so it's last).
#
# Usage: ./deploy.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
require aws; require docker
[ -x "${PY}" ] || die "venv python not found at ${PY} -- python3 -m venv ../.venv && ../.venv/bin/pip install -r requirements.txt"
: > "${FUSE_STATE_FILE}"

ACCOUNT_ID="$(account_id)"

# ---- 1. build + push image ----
URI="$(image_uri)"
log "1/4 image: ${URI}"
aws ecr describe-repositories --repository-names "${ECR_REPOSITORY}" --region "${AWS_REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${ECR_REPOSITORY}" --region "${AWS_REGION}" >/dev/null
aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "$(ecr_registry)" >/dev/null
docker buildx inspect fuse-infra-builder >/dev/null 2>&1 || docker buildx create --name fuse-infra-builder --use >/dev/null
docker buildx use fuse-infra-builder
docker buildx build --platform "${IMAGE_PLATFORM}" --tag "${URI}" --push "${BUILD_CONTEXT}"

# ---- 2. IAM role ----
log "2/4 IAM role: ${AGENTCORE_ROLE_NAME}"
ROLE_ARN="$(ensure_role "${AGENTCORE_ROLE_NAME}" "$(agent_trust_policy)" \
  "${AGENTCORE_ROLE_NAME}-policy" "$(agent_permissions_policy "${ACCOUNT_ID}")")"

# ---- 3. bucket (empty -- the harness writes the first object at invoke time) ----
BUCKET="${FUSE_BUCKET_PREFIX}-${ACCOUNT_ID}-${AWS_REGION}"
log "3/4 bucket: ${BUCKET}"
if ! aws s3api head-bucket --bucket "${BUCKET}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  if [ "${AWS_REGION}" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "${BUCKET}" --region "${AWS_REGION}" >/dev/null
  else
    aws s3api create-bucket --bucket "${BUCKET}" --region "${AWS_REGION}" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}" >/dev/null
  fi
fi
aws s3api put-bucket-encryption --bucket "${BUCKET}" --region "${AWS_REGION}" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket "${BUCKET}" --region "${AWS_REGION}" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
# Written as soon as known, not just on full success, so cleanup.sh can
# find and delete it even if a later step fails.
echo "FUSE_BUCKET=${BUCKET}" >> "${FUSE_STATE_FILE}"

# ---- 4. runtime ----
log "4/4 runtime: ${RUNTIME_NAME}"
ARTIFACT="{\"containerConfiguration\":{\"containerUri\":\"${URI}\"}}"
ENV_VARS="{\"FUSE_BUCKET\":\"${BUCKET}\",\"FUSE_MODEL_ID\":\"${FUSE_MODEL_ID}\",\"AWS_REGION\":\"${AWS_REGION}\"}"
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "${TMP_DIR}"' EXIT
BODY_FILE="${TMP_DIR}/body.json"

# agentRuntimeName is CREATE-only -- update_agent_runtime rejects it.
cat > "${BODY_FILE}" <<JSON
{
  "agentRuntimeArtifact": ${ARTIFACT},
  "roleArn": "${ROLE_ARN}",
  "networkConfiguration": {"networkMode": "PUBLIC"},
  "lifecycleConfiguration": $(agentcore_lifecycle_json),
  "platformVersion": "V2",
  "environmentVariables": ${ENV_VARS}
}
JSON

EXISTING_ARN="$("${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" find-arn --region "${AWS_REGION}" --name "${RUNTIME_NAME}")"

# A runtime stuck in *_FAILED never finished creating -- delete and
# recreate rather than risk a confusing update error.
if [ -n "${EXISTING_ARN}" ]; then
  EXISTING_ID="${EXISTING_ARN##*/}"
  EXISTING_STATUS="$("${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" get --region "${AWS_REGION}" \
    --agent-runtime-id "${EXISTING_ID}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","UNKNOWN"))')"
  case "${EXISTING_STATUS}" in
    *_FAILED)
      log "existing runtime ${EXISTING_ID} is ${EXISTING_STATUS} -- deleting and recreating"
      "${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" delete --region "${AWS_REGION}" --agent-runtime-id "${EXISTING_ID}" >/dev/null
      sleep 5
      EXISTING_ARN=""
      ;;
  esac
fi

if [ -n "${EXISTING_ARN}" ]; then
  RUNTIME_ID="${EXISTING_ARN##*/}"
  "${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" update --region "${AWS_REGION}" \
    --agent-runtime-id "${RUNTIME_ID}" --body-file "${BODY_FILE}" >/dev/null
  RUNTIME_ARN="${EXISTING_ARN}"
else
  CREATE_BODY_FILE="${TMP_DIR}/create-body.json"
  python3 -c "
import json
with open('${BODY_FILE}') as f: body = json.load(f)
body['agentRuntimeName'] = '${RUNTIME_NAME}'
with open('${CREATE_BODY_FILE}', 'w') as f: json.dump(body, f)
"
  RESP="$("${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" create --region "${AWS_REGION}" --body-file "${CREATE_BODY_FILE}")"
  RUNTIME_ID="$(echo "${RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["agentRuntimeId"])')"
  RUNTIME_ARN="$(echo "${RESP}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["agentRuntimeArn"])')"
fi

# Recorded right after create/update, before polling for READY, so
# cleanup.sh can find it even if the runtime lands in *_FAILED below.
{
  echo "FUSE_RUNTIME_ID=${RUNTIME_ID}"
  echo "FUSE_RUNTIME_ARN=${RUNTIME_ARN}"
} >> "${FUSE_STATE_FILE}"

STATUS="UNKNOWN"
for i in $(seq 1 30); do
  STATUS="$("${PY}" "${SCRIPT_DIR}/agentcore_boto3.py" get --region "${AWS_REGION}" \
    --agent-runtime-id "${RUNTIME_ID}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","UNKNOWN"))')"
  log "[$((i*20))s] status=${STATUS}"
  case "${STATUS}" in CREATING|UPDATING) sleep 20 ;; *) break ;; esac
done
[ "${STATUS}" = "READY" ] || die "runtime ended in status=${STATUS}, not READY (state recorded -- cleanup.sh can delete it)"

# ---- 5. bucket policy (needs the runtime ARN, so it's last) ----
# Second layer on top of common.sh's identity-side role policy: own-session
# Allow (pinned to this runtime) + two Deny statements for defense in depth.
PRINCIPAL_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${AGENTCORE_ROLE_NAME}"
POLICY_FILE="${TMP_DIR}/bucket-policy.json"
cat > "${POLICY_FILE}" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "AllowOwnSessionFromThisRuntime", "Effect": "Allow",
      "Principal": { "AWS": "${PRINCIPAL_ARN}" },
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
      "Resource": "arn:aws:s3:::${BUCKET}/sessions/\${${CTXKEY_SESSION_KEY_NAME}}/*",
      "Condition": { "StringEquals": { "${CTXKEY_RUNTIME_KEY_NAME}": "${RUNTIME_ARN}" } } },
    { "Sid": "AllowListOwnSession", "Effect": "Allow",
      "Principal": { "AWS": "${PRINCIPAL_ARN}" }, "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${BUCKET}",
      "Condition": { "StringLike": { "s3:prefix": "sessions/\${${CTXKEY_SESSION_KEY_NAME}}/*" },
                      "StringEquals": { "${CTXKEY_RUNTIME_KEY_NAME}": "${RUNTIME_ARN}" } } },
    { "Sid": "DenyCrossSessionWhenSessionPresent", "Effect": "Deny", "Principal": "*",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "NotResource": "arn:aws:s3:::${BUCKET}/sessions/\${${CTXKEY_SESSION_KEY_NAME}}/*",
      "Condition": { "Null": { "${CTXKEY_SESSION_KEY_NAME}": "false" } } },
    { "Sid": "DenyWhenForeignRuntime", "Effect": "Deny", "Principal": "*",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::${BUCKET}/*",
      "Condition": { "Null": { "${CTXKEY_RUNTIME_KEY_NAME}": "false" },
                      "StringNotEquals": { "${CTXKEY_RUNTIME_KEY_NAME}": "${RUNTIME_ARN}" } } }
  ]
}
JSON
python3 -m json.tool "${POLICY_FILE}" >/dev/null || die "rendered bucket policy is not valid JSON"
aws s3api put-bucket-policy --bucket "${BUCKET}" --policy "file://${POLICY_FILE}" --region "${AWS_REGION}"

log "done. runtime=${RUNTIME_ARN} bucket=${BUCKET}"
log "S3 bucket-policy changes can take ~30s to propagate -- a DENIED right after this isn't necessarily broken."
