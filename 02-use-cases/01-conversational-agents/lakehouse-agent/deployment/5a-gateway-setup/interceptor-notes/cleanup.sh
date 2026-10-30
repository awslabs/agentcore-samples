#!/bin/bash
# Cleanup the Notes (OpenSearch) Gateway REQUEST Interceptor Lambda — Cognito GW2 path.
# Mirrors interceptor-notes/deploy.sh: deletes exactly what deploy.sh creates
# (Lambda, dedicated least-priv role, CloudWatch log group, SSM ARN key, local
# build artifacts). Idempotent — every resource is safe-if-absent and the script
# exits 0 even on a second run (logs ⏭️ skips, not errors).
#
# Only relevant on the Cognito path (the notes interceptor is Cognito-only); on
# Okta these resources never existed, so every step is a ⏭️ no-op.
#
# Usage:
#   ./cleanup.sh [--keep-ssm]

set -e

# Ensure common tool paths are available (e.g. when run from a notebook subprocess)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

KEEP_SSM=false
for arg in "$@"; do
    case "$arg" in
        --keep-ssm) KEEP_SSM=true ;;
        *) echo "⚠️  Unknown argument: $arg (supported: --keep-ssm)" ;;
    esac
done

echo "🧹 Cleaning up Notes Gateway REQUEST Interceptor Lambda"

# Region resolution mirrors deploy.sh.
AWS_REGION=$(aws configure get region)
if [ -z "$AWS_REGION" ]; then
    echo "❌ Error: AWS region not configured"
    echo "   Please run: aws configure set region <your-region>"
    exit 1
fi
echo "   Region: $AWS_REGION"

FUNCTION_NAME="lakehouse-notes-interceptor"
ROLE_NAME="lakehouse-notes-interceptor-role"
LOG_GROUP_NAME="/aws/lambda/lakehouse-notes-interceptor"
SSM_PARAM="/app/lakehouse-agent/notes-interceptor-lambda-arn"
BASIC_EXEC_POLICY_ARN="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

# ─── 1: Lambda function ───────────────────────────────────────────────
echo ""
echo "🗑️  Deleting Lambda function: $FUNCTION_NAME"
if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
    aws lambda delete-function --function-name "$FUNCTION_NAME" --region "$AWS_REGION"
    echo "   ✅ Deleted Lambda function: $FUNCTION_NAME"
else
    echo "   ⏭️  Lambda function not found: $FUNCTION_NAME"
fi

# ─── 2: Dedicated least-privilege execution role ──────────────────────
echo ""
echo "🗑️  Deleting IAM role: $ROLE_NAME"
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    # Detach managed policies (deploy.sh attaches AWSLambdaBasicExecutionRole;
    # detach whatever is attached to be safe), then remove any inline policies.
    for arn in $(aws iam list-attached-role-policies --role-name "$ROLE_NAME" \
        --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null); do
        aws iam detach-role-policy --role-name "$ROLE_NAME" --policy-arn "$arn" 2>/dev/null || true
        echo "   ✅ Detached managed policy: $arn"
    done
    for pol in $(aws iam list-role-policies --role-name "$ROLE_NAME" \
        --query 'PolicyNames[]' --output text 2>/dev/null); do
        aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "$pol" 2>/dev/null || true
        echo "   ✅ Deleted inline policy: $pol"
    done
    aws iam delete-role --role-name "$ROLE_NAME"
    echo "   ✅ Deleted role: $ROLE_NAME"
else
    echo "   ⏭️  Role not found: $ROLE_NAME"
fi

# ─── 3: CloudWatch Logs log group ─────────────────────────────────────
echo ""
echo "🗑️  Deleting CloudWatch log group: $LOG_GROUP_NAME"
if aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP_NAME" --region "$AWS_REGION" \
    --query 'logGroups[?logGroupName==`'"$LOG_GROUP_NAME"'`].logGroupName' --output text 2>/dev/null | grep -q "$LOG_GROUP_NAME"; then
    aws logs delete-log-group --log-group-name "$LOG_GROUP_NAME" --region "$AWS_REGION"
    echo "   ✅ Deleted log group: $LOG_GROUP_NAME"
else
    echo "   ⏭️  Log group not found: $LOG_GROUP_NAME"
fi

# ─── 4: SSM parameter ─────────────────────────────────────────────────
echo ""
if [ "$KEEP_SSM" = true ]; then
    echo "⏭️  Keeping SSM parameter (--keep-ssm): $SSM_PARAM"
else
    echo "🗑️  Deleting SSM parameter: $SSM_PARAM"
    if aws ssm delete-parameter --name "$SSM_PARAM" --region "$AWS_REGION" >/dev/null 2>&1; then
        echo "   ✅ Deleted: $SSM_PARAM"
    else
        echo "   ⏭️  Parameter not found: $SSM_PARAM"
    fi
fi

# ─── 5: Local build artifacts (created by deploy.sh) ──────────────────
echo ""
echo "🗑️  Removing local build artifacts..."
rm -rf dist
rm -f notes-interceptor-lambda.zip
echo "   ✅ Removed dist/ and notes-interceptor-lambda.zip (if present)"

echo ""
echo "✨ Notes interceptor cleanup complete!"
