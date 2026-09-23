#!/usr/bin/env bash
#
# Deploy the LTM record-streaming replication path (Kinesis -> consumer Lambda).
#
# The stack is deployed in the SOURCE region (where the Kinesis stream lives and
# the source memory streams its records). The consumer Lambda writes cross-region
# into the TARGET memory. After deploying, this script also enables streaming on
# the source memory and points it at the new stream.
#
# STM replication is handled by the application at write time (dual-write
# CreateEvent with extractionMode=SKIP) — see agentcore_replication.dual_writer.
#
# Usage:
#   scripts/deploy_streaming.sh \
#     --source-memory-id mem-aaaaaaaaaa \
#     --target-memory-id mem-bbbbbbbbbb \
#     --source-region us-east-1 \
#     --target-region us-west-2 \
#     [--memory-execution-role arn:aws:iam::...:role/...] \
#     [--bucket my-deploy-bucket] \
#     [--stream-name agentcore-ltm-stream]
#
set -euo pipefail

SOURCE_MEMORY_ID="" TARGET_MEMORY_ID=""
SOURCE_REGION="" TARGET_REGION=""
BUCKET="" STREAM_NAME="agentcore-ltm-stream"
MEMORY_EXEC_ROLE=""
STACK_NAME="agentcore-ltm-stream-replicator"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-memory-id)      SOURCE_MEMORY_ID="$2"; shift 2 ;;
    --target-memory-id)      TARGET_MEMORY_ID="$2"; shift 2 ;;
    --source-region)         SOURCE_REGION="$2"; shift 2 ;;
    --target-region)         TARGET_REGION="$2"; shift 2 ;;
    --bucket)                BUCKET="$2"; shift 2 ;;
    --stream-name)           STREAM_NAME="$2"; shift 2 ;;
    --memory-execution-role) MEMORY_EXEC_ROLE="$2"; shift 2 ;;
    --stack-name)            STACK_NAME="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

for v in SOURCE_MEMORY_ID TARGET_MEMORY_ID SOURCE_REGION TARGET_REGION; do
  if [[ -z "${!v}" ]]; then echo "Missing --${v,,} (replace _ with -)" >&2; exit 1; fi
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
[[ -z "$BUCKET" ]] && BUCKET="agentcore-replicator-${ACCOUNT_ID}-${SOURCE_REGION}"

echo "==> Account:        $ACCOUNT_ID"
echo "==> Source:         $SOURCE_MEMORY_ID @ $SOURCE_REGION (stream lives here)"
echo "==> Target:         $TARGET_MEMORY_ID @ $TARGET_REGION (replica)"
echo "==> Kinesis stream: $STREAM_NAME"
echo "==> Deploy bucket:  $BUCKET"

# 1. Deploy bucket in the SOURCE region.
if ! aws s3api head-bucket --bucket "$BUCKET" --region "$SOURCE_REGION" 2>/dev/null; then
  echo "==> Creating deploy bucket $BUCKET"
  if [[ "$SOURCE_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$SOURCE_REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$SOURCE_REGION" \
      --create-bucket-configuration "LocationConstraint=$SOURCE_REGION"
  fi
fi

# 2. Package the consumer Lambda (stream_handler.py + the package).
BUILD="$(mktemp -d)"; trap 'rm -rf "$BUILD"' EXIT
cp "$ROOT/lambda/stream_handler.py" "$BUILD/"
cp -r "$ROOT/agentcore_replication" "$BUILD/agentcore_replication"
ZIP="$BUILD/agentcore-stream-replicator.zip"
( cd "$BUILD" && zip -qr "$ZIP" stream_handler.py agentcore_replication )
echo "==> Built $(du -h "$ZIP" | cut -f1) package"
CODE_KEY="agentcore-stream-replicator-$(date +%s).zip"
aws s3 cp "$ZIP" "s3://$BUCKET/$CODE_KEY" --region "$SOURCE_REGION"

# 3. Deploy the stack in the SOURCE region.
echo "==> Deploying stack $STACK_NAME in $SOURCE_REGION"
aws cloudformation deploy \
  --region "$SOURCE_REGION" \
  --stack-name "$STACK_NAME" \
  --template-file "$ROOT/infra/streaming-stack.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    TargetMemoryId="$TARGET_MEMORY_ID" \
    TargetRegion="$TARGET_REGION" \
    CodeS3Bucket="$BUCKET" \
    CodeS3Key="$CODE_KEY" \
    KinesisStreamName="$STREAM_NAME"

# 4. Read stack outputs.
get_out() {
  aws cloudformation describe-stacks --region "$SOURCE_REGION" \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
STREAM_ARN="$(get_out MemoryStreamArn)"
STREAM_ROLE_ARN="$(get_out MemoryStreamingRoleArn)"
echo "==> Stream ARN:        $STREAM_ARN"
echo "==> Streaming role:    $STREAM_ROLE_ARN"

# 5. Ensure the source memory has an execution role that can write to Kinesis.
EXEC_ROLE="${MEMORY_EXEC_ROLE:-$STREAM_ROLE_ARN}"
echo "==> Setting source memory execution role -> $EXEC_ROLE"
aws bedrock-agentcore-control update-memory --region "$SOURCE_REGION" \
  --memory-id "$SOURCE_MEMORY_ID" \
  --memory-execution-role-arn "$EXEC_ROLE" >/dev/null

# 6. Enable record streaming on the source memory -> Kinesis.
echo "==> Enabling record streaming on $SOURCE_MEMORY_ID"
python "$ROOT/scripts/enable_streaming.py" \
  --memory-id "$SOURCE_MEMORY_ID" \
  --region "$SOURCE_REGION" \
  --stream-arn "$STREAM_ARN"

echo "==> Done."
echo "    LTM: source memory -> Kinesis -> consumer Lambda -> target memory."
echo "    STM: have your app dual-write via agentcore_replication.DualRegionEventWriter."
