#!/usr/bin/env bash
# Deploy shared infrastructure: S3 bucket + VPC + S3 Files + upload skills.
# This is deployed ONCE and reused by all coding agents (claude-code, codex, cursor, etc.)
set -euo pipefail

REGION="${1:-us-west-2}"
STACK_NAME="coding-agents-infra"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="coding-agents-${ACCOUNT_ID}-${REGION}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=============================================="
echo "  Shared Infrastructure for Coding Agents"
echo "  Region: $REGION  Account: $ACCOUNT_ID"
echo "=============================================="

# ── S3 Bucket ────────────────────────────────────────────────────────────────
if aws s3api head-bucket --bucket "${BUCKET_NAME}" 2>/dev/null; then
  echo "Bucket exists: ${BUCKET_NAME}"
else
  echo "Creating bucket: ${BUCKET_NAME}"
  if [ "${REGION}" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "${BUCKET_NAME}" --region "${REGION}"
  else
    aws s3api create-bucket --bucket "${BUCKET_NAME}" --region "${REGION}" \
      --create-bucket-configuration LocationConstraint="${REGION}"
  fi
fi

aws s3api put-bucket-versioning \
  --bucket "${BUCKET_NAME}" \
  --versioning-configuration Status=Enabled \
  --region "${REGION}"
echo "Bucket versioning enabled"

# ── CloudFormation (VPC + S3 Files) ──────────────────────────────────────────
echo ""
echo "Deploying CloudFormation stack: ${STACK_NAME}..."
aws cloudformation deploy \
  --template-file "${SCRIPT_DIR}/cfn-vpc.yaml" \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --parameter-overrides BucketName="${BUCKET_NAME}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset

echo "Reading stack outputs..."
CFN_OUTPUTS=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs" \
  --output json)

get_output() {
  echo "${CFN_OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == '$1':
        print(o['OutputValue'])
        break
"
}

VPC_ID=$(get_output VpcId)
PRIVATE_SUBNET_1=$(get_output PrivateSubnet1Id)
PRIVATE_SUBNET_2=$(get_output PrivateSubnet2Id)
SECURITY_GROUP_ID=$(get_output SecurityGroupId)
S3FILES_FS_ID=$(get_output S3FilesFileSystemId)
S3FILES_AP_ID=$(get_output S3FilesAccessPointId)
S3FILES_AP_ARN=$(get_output S3FilesAccessPointArn)

# ── Save shared config ───────────────────────────────────────────────────────
cat > "${SCRIPT_DIR}/../infra.config" <<EOF
INFRA_REGION=${REGION}
INFRA_ACCOUNT_ID=${ACCOUNT_ID}
INFRA_BUCKET=${BUCKET_NAME}
INFRA_STACK_NAME=${STACK_NAME}
INFRA_VPC_ID=${VPC_ID}
INFRA_SUBNET_1=${PRIVATE_SUBNET_1}
INFRA_SUBNET_2=${PRIVATE_SUBNET_2}
INFRA_SECURITY_GROUP=${SECURITY_GROUP_ID}
INFRA_S3FILES_FS_ID=${S3FILES_FS_ID}
INFRA_S3FILES_AP_ID=${S3FILES_AP_ID}
INFRA_S3FILES_AP_ARN=${S3FILES_AP_ARN}
EOF

echo ""
echo "  VPC:             ${VPC_ID}"
echo "  Private Subnet1: ${PRIVATE_SUBNET_1}"
echo "  Private Subnet2: ${PRIVATE_SUBNET_2}"
echo "  Security Group:  ${SECURITY_GROUP_ID}"
echo "  S3 Files FS:     ${S3FILES_FS_ID}"
echo "  S3 Files AP ARN: ${S3FILES_AP_ARN}"

# ── Upload skills to S3 ─────────────────────────────────────────────────────
echo ""
echo "Uploading skills to S3..."
SKILLS_S3_PREFIX="s3://${BUCKET_NAME}/agents/mnt/s3files/skills"

SKILL_FILE="${SCRIPT_DIR}/../../git_mcp_skill/github-mcp.md"
if [ -f "$SKILL_FILE" ]; then
  aws s3 cp "$SKILL_FILE" "${SKILLS_S3_PREFIX}/github-mcp.md" --region "${REGION}"
  echo "  Uploaded: github-mcp.md"
else
  echo "  WARNING: git_mcp_skill/github-mcp.md not found, skipping"
fi

for f in "${SCRIPT_DIR}"/skills/*.md; do
  [ -f "$f" ] || continue
  BASENAME=$(basename "$f")
  aws s3 cp "$f" "${SKILLS_S3_PREFIX}/${BASENAME}" --region "${REGION}"
  echo "  Uploaded: ${BASENAME}"
done

# ── Upload MCP Gateway Proxy to S3 ─────────────────────────────────────────
echo ""
echo "Uploading MCP Gateway Proxy to S3..."
MCP_S3_PREFIX="s3://${BUCKET_NAME}/agents/mnt/s3files/mcp"
MCP_DIR="${SCRIPT_DIR}/../../git_mcp_skill"

if [ -f "${MCP_DIR}/index.js" ] && [ -f "${MCP_DIR}/package.json" ]; then
  # Install node_modules if not present
  if [ ! -d "${MCP_DIR}/node_modules" ]; then
    echo "  Installing MCP proxy dependencies..."
    (cd "${MCP_DIR}" && npm install --omit=dev)
  fi

  aws s3 cp "${MCP_DIR}/index.js" "${MCP_S3_PREFIX}/index.js" --region "${REGION}"
  aws s3 cp "${MCP_DIR}/package.json" "${MCP_S3_PREFIX}/package.json" --region "${REGION}"
  aws s3 sync "${MCP_DIR}/node_modules" "${MCP_S3_PREFIX}/node_modules" --region "${REGION}"
  echo "  Uploaded: index.js, package.json, node_modules/"
else
  echo "  WARNING: git_mcp_skill/index.js or package.json not found, skipping MCP upload"
fi

echo ""
echo "Config saved to: ../infra.config"
echo "Skills at: ${SKILLS_S3_PREFIX}/ -> /mnt/s3files/skills/ in runtime"
echo "MCP at:    ${MCP_S3_PREFIX}/ -> /mnt/s3files/mcp/ in runtime"
echo "Next: cd .. && ./deploy_all.sh"
