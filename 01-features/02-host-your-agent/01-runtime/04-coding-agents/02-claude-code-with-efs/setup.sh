#!/usr/bin/env bash
set -euo pipefail

REGION="${1:-us-west-2}"
STACK_NAME="agentcore-claude-code-demo"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AGENT_NAME="claude_code_$(date +%s | tail -c 6)"
ECR_REPO="agentcore-claude-code"
IMAGE_TAG="${AGENT_NAME}"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"

echo "Region:     ${REGION}"
echo "Account:    ${ACCOUNT_ID}"
echo "Agent:      ${AGENT_NAME}"
echo "Stack:      ${STACK_NAME}"

# ── Deploy CloudFormation stack ──────────────────────────────────────────────

echo ""
echo "Deploying CloudFormation stack: ${STACK_NAME}..."
aws cloudformation deploy \
    --template-file cfn-vpc.yaml \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
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
EFS_FS_ID=$(get_output EFSFileSystemId)
EFS_AP_ARN=$(get_output EFSAccessPointArn)

echo "  VPC:             ${VPC_ID}"
echo "  Private Subnet1: ${PRIVATE_SUBNET_1}"
echo "  Private Subnet2: ${PRIVATE_SUBNET_2}"
echo "  Security Group:  ${SECURITY_GROUP_ID}"
echo "  EFS FS:          ${EFS_FS_ID}"
echo "  EFS AP ARN:      ${EFS_AP_ARN}"

# ── Create ECR repository (skip if it already exists) ────────────────────────

if aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${REGION}" >/dev/null 2>&1; then
    echo "ECR repo already exists: ${ECR_REPO}"
else
    echo "Creating ECR repo: ${ECR_REPO}"
    aws ecr create-repository --repository-name "${ECR_REPO}" --region "${REGION}"
    echo "ECR repo created"
fi

# ── Build arm64 Docker image and push to ECR ─────────────────────────────────

echo "Logging into ECR..."
aws ecr get-login-password --region "${REGION}" | \
    docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"


echo "Building arm64 Docker image..."
docker buildx build \
    --platform linux/arm64 \
    -t "${ECR_URI}" \
    -f Dockerfile \
    --push \
    .

echo "Image pushed: ${ECR_URI}"

# ── Save config ──────────────────────────────────────────────────────────────

cat > envvars.config <<CFGEOF
AGENTCORE_AGENT_NAME=${AGENT_NAME}
AGENTCORE_REGION=${REGION}
AGENTCORE_ECR_URI=${ECR_URI}
AGENTCORE_STACK_NAME=${STACK_NAME}
AGENTCORE_VPC_ID=${VPC_ID}
AGENTCORE_SUBNET_1=${PRIVATE_SUBNET_1}
AGENTCORE_SUBNET_2=${PRIVATE_SUBNET_2}
AGENTCORE_SECURITY_GROUP=${SECURITY_GROUP_ID}
AGENTCORE_EFS_FS_ID=${EFS_FS_ID}
AGENTCORE_EFS_AP_ARN=${EFS_AP_ARN}
CFGEOF

echo ""
echo "Config saved to envvars.config:"
cat envvars.config
echo ""
echo "Run the deploy script next:"
echo "  python deploy.py"
