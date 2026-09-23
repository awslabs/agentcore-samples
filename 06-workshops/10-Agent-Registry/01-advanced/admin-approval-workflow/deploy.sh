#!/usr/bin/env bash
set -euo pipefail

# -------------------------------------------------------
# deploy.sh
# Deploys the CloudFormation stack.
# -------------------------------------------------------

usage() {
  cat <<EOF
Usage: $(basename "$0") \\
  --stack-name <name> \\
  --prefix <prefix> \\
  --registry-id <id> \\
  --slack-hook-url <url> \\
  --slack-channel <#channel> \\
  [--s3-bucket <bucket>] \\
  [--skip-layer-build --layer-key <key>] \\
  --region <aws-region>

Options:
  --stack-name        CloudFormation stack name
  --prefix            Prefix applied to all resource names (CFN: Prefix)
  --registry-id       AWS Agent Registry ID (CFN: RegistryId)
  --slack-hook-url    Slack incoming webhook URL (CFN: SlackIncomingHookUrl)
  --slack-channel     Slack channel name, e.g. #ops (CFN: SlackChannelName)
  --s3-bucket         S3 bucket for staging the Lambda layer zip (auto-created if omitted)
  --skip-layer-build  Skip building and uploading the Lambda layer (requires --layer-key)
  --layer-key         S3 key of an existing layer zip to use (required with --skip-layer-build)
  --region            AWS region (required)
  -h, --help          Show this help message
EOF
  exit 1
}

# --- Defaults ---
AWS_REGION=""
STACK_NAME=""
PREFIX=""
REGISTRY_ID=""
SLACK_HOOK_URL=""
SLACK_CHANNEL=""
S3_BUCKET=""
LAYER_KEY=""
SKIP_LAYER_BUILD=false

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stack-name)     STACK_NAME="$2";     shift 2 ;;
    --prefix)         PREFIX="$2";         shift 2 ;;
    --registry-id)    REGISTRY_ID="$2";    shift 2 ;;
    --slack-hook-url) SLACK_HOOK_URL="$2"; shift 2 ;;
    --slack-channel)  SLACK_CHANNEL="$2";  shift 2 ;;
    --s3-bucket)      S3_BUCKET="$2";      shift 2 ;;
    --layer-key)      LAYER_KEY="$2";      shift 2 ;;
    --region)         AWS_REGION="$2";     shift 2 ;;
    --skip-layer-build) SKIP_LAYER_BUILD=true; shift ;;
    -h|--help)        usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

# --- Validate required arguments ---
missing=()
[[ -z "${STACK_NAME}" ]]     && missing+=("--stack-name")
[[ -z "${PREFIX}" ]]         && missing+=("--prefix")
[[ -z "${REGISTRY_ID}" ]]    && missing+=("--registry-id")
[[ -z "${SLACK_HOOK_URL}" ]] && missing+=("--slack-hook-url")
[[ -z "${SLACK_CHANNEL}" ]]  && missing+=("--slack-channel")
[[ -z "${AWS_REGION}" ]]     && missing+=("--region")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Error: missing required arguments: ${missing[*]}"
  echo ""
  usage
fi

# --- Resolve layer key ---
if [[ "${SKIP_LAYER_BUILD}" == true ]]; then
  if [[ -z "${LAYER_KEY}" ]]; then
    echo "Error: --layer-key is required when --skip-layer-build is set"
    usage
  fi
else
  LAYER_KEY="cicd_dependencies_layer-$(date +%Y%m%d%H%M%S).zip"
fi

# --- Resolve S3 bucket (auto-create if not provided) ---
if [[ -z "${S3_BUCKET}" ]]; then
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "${AWS_REGION}")
  S3_BUCKET="${PREFIX}-deploy-artifacts-${ACCOUNT_ID}-${AWS_REGION}"
  echo "No --s3-bucket provided. Using auto-generated name: ${S3_BUCKET}"
fi

if aws s3api head-bucket --bucket "${S3_BUCKET}" --region "${AWS_REGION}" 2>/dev/null; then
  echo "S3 bucket already exists: ${S3_BUCKET}"
else
  echo "Creating S3 bucket: ${S3_BUCKET}..."
  if [[ "${AWS_REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket \
      --bucket "${S3_BUCKET}" \
      --region "${AWS_REGION}"
  else
    aws s3api create-bucket \
      --bucket "${S3_BUCKET}" \
      --region "${AWS_REGION}" \
      --create-bucket-configuration LocationConstraint="${AWS_REGION}"
  fi
  aws s3api put-public-access-block \
    --bucket "${S3_BUCKET}" \
    --public-access-block-configuration \
      "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
  echo "S3 bucket created: ${S3_BUCKET}"
fi

# --- Paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${SCRIPT_DIR}/cfn_eventbridge.yaml"

# --- Ensure zip is installed ---
if ! command -v zip &>/dev/null; then
  echo "zip not found, installing..."
  if command -v apt-get &>/dev/null; then
    sudo apt-get install -y zip
  elif command -v yum &>/dev/null; then
    sudo yum install -y zip
  elif command -v brew &>/dev/null; then
    brew install zip
  else
    echo "Error: could not install zip — no supported package manager found (apt-get, yum, brew)"
    exit 1
  fi
fi

# --- Build and upload Lambda layer ---
if [[ "${SKIP_LAYER_BUILD}" == true ]]; then
  echo "Skipping Lambda layer build and upload (--skip-layer-build set)."
else
  echo "Building CICD dependencies Lambda layer..."
  LAYER_BUILD_DIR="${SCRIPT_DIR}/layer_build"
  LAYER_ZIP="${SCRIPT_DIR}/${LAYER_KEY}"

  [[ -d "${LAYER_BUILD_DIR}" ]] && rm -rf "${LAYER_BUILD_DIR}"
  mkdir -p "${LAYER_BUILD_DIR}/python"
  WHEELS_DIR="${SCRIPT_DIR}/../../python_wheels"
  pip install cisco-ai-a2a-scanner==1.0.1 -t "${LAYER_BUILD_DIR}/python/" --quiet
  pip install boto3 -t "${LAYER_BUILD_DIR}/python/" --ignore-installed --upgrade

  cd "${LAYER_BUILD_DIR}"
  zip -r "${LAYER_ZIP}" python/ > /dev/null 2>&1
  cd "${SCRIPT_DIR}"

  echo "Uploading layer zip to s3://${S3_BUCKET}/${LAYER_KEY}..."
  aws s3 cp "${LAYER_ZIP}" "s3://${S3_BUCKET}/${LAYER_KEY}" --region "${AWS_REGION}" --no-progress
  rm -f "${LAYER_ZIP}"
fi

# --- Deploy CloudFormation stack ---
echo "Deploying CloudFormation stack: ${STACK_NAME}..."
aws cloudformation deploy \
  --template-file "${TEMPLATE}" \
  --stack-name "${STACK_NAME}" \
  --region "${AWS_REGION}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    Prefix="${PREFIX}" \
    RegistryId="${REGISTRY_ID}" \
    SlackIncomingHookUrl="${SLACK_HOOK_URL}" \
    SlackChannelName="${SLACK_CHANNEL}" \
    LambdaLayerBucket="${S3_BUCKET}" \
    LambdaLayerKey="${LAYER_KEY}"

echo "Done. Stack outputs:"
aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs" \
  --output table

# --- Cleanup ---
echo "Deployment complete."
