#!/bin/bash
# Cleanup the Notes (OpenSearch) Gateway REQUEST Interceptor Lambda — Cognito path.
# Deletes what interceptor-notes/deploy.sh creates: the Lambda
# lakehouse-notes-interceptor, its role lakehouse-notes-interceptor-role, its
# CloudWatch log group, the SSM key /app/lakehouse-agent/notes-interceptor-lambda-arn
# (unless --keep-ssm) and the local dist/ and zip. Each step checks for the
# resource first and logs a skip if it is absent, so a second run is a no-op.
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

# A probe that fails for any reason other than the resource being absent is an
# error, not a skip. The AWS CLI prints the service's error code in parentheses,
# e.g. "An error occurred (NoSuchEntity) when calling the GetRole operation".
# Errors are collected here and reported at the end.
ERRORS=()

# ─── 1: Lambda function ───────────────────────────────────────────────
echo ""
echo "🗑️  Deleting Lambda function: $FUNCTION_NAME"
if PROBE_ERR=$(aws lambda get-function --function-name "$FUNCTION_NAME" --region "$AWS_REGION" 2>&1 >/dev/null); then
    aws lambda delete-function --function-name "$FUNCTION_NAME" --region "$AWS_REGION"
    echo "   ✅ Deleted Lambda function: $FUNCTION_NAME"
elif [[ "$PROBE_ERR" == *"(ResourceNotFoundException)"* ]]; then
    echo "   ⏭️  Lambda function not found: $FUNCTION_NAME"
else
    echo "   ❌ Could not check Lambda function $FUNCTION_NAME: $PROBE_ERR"
    ERRORS+=("Lambda function $FUNCTION_NAME: $PROBE_ERR")
fi

# ─── 2: Dedicated least-privilege execution role ──────────────────────
echo ""
echo "🗑️  Deleting IAM role: $ROLE_NAME"
if PROBE_ERR=$(aws iam get-role --role-name "$ROLE_NAME" 2>&1 >/dev/null); then
    # Detach managed policies (deploy.sh attaches AWSLambdaBasicExecutionRole;
    # detach whatever is attached to be safe), then remove any inline policies.
    # A failed detach or delete stops the script (set -e) before its ✅ line.
    for arn in $(aws iam list-attached-role-policies --role-name "$ROLE_NAME" \
        --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null); do
        aws iam detach-role-policy --role-name "$ROLE_NAME" --policy-arn "$arn"
        echo "   ✅ Detached managed policy: $arn"
    done
    for pol in $(aws iam list-role-policies --role-name "$ROLE_NAME" \
        --query 'PolicyNames[]' --output text 2>/dev/null); do
        aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "$pol"
        echo "   ✅ Deleted inline policy: $pol"
    done
    aws iam delete-role --role-name "$ROLE_NAME"
    echo "   ✅ Deleted role: $ROLE_NAME"
elif [[ "$PROBE_ERR" == *"(NoSuchEntity)"* ]]; then
    echo "   ⏭️  Role not found: $ROLE_NAME"
else
    echo "   ❌ Could not check IAM role $ROLE_NAME: $PROBE_ERR"
    ERRORS+=("IAM role $ROLE_NAME: $PROBE_ERR")
fi

# ─── 3: CloudWatch Logs log group ─────────────────────────────────────
echo ""
echo "🗑️  Deleting CloudWatch log group: $LOG_GROUP_NAME"
# An absent log group is an empty list, not an error, so a failed list is checked separately.
if ! LOG_LOOKUP=$(aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP_NAME" --region "$AWS_REGION" \
    --query 'logGroups[?logGroupName==`'"$LOG_GROUP_NAME"'`].logGroupName' --output text 2>&1); then
    echo "   ❌ Could not list log groups: $LOG_LOOKUP"
    ERRORS+=("log group $LOG_GROUP_NAME: $LOG_LOOKUP")
elif printf '%s\n' "$LOG_LOOKUP" | grep -q "$LOG_GROUP_NAME"; then
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
    if PROBE_ERR=$(aws ssm delete-parameter --name "$SSM_PARAM" --region "$AWS_REGION" 2>&1 >/dev/null); then
        echo "   ✅ Deleted: $SSM_PARAM"
    elif [[ "$PROBE_ERR" == *"(ParameterNotFound)"* ]]; then
        echo "   ⏭️  Parameter not found: $SSM_PARAM"
    else
        echo "   ❌ Could not delete SSM parameter $SSM_PARAM: $PROBE_ERR"
        ERRORS+=("SSM parameter $SSM_PARAM: $PROBE_ERR")
    fi
fi

# ─── 5: Local build artifacts (created by deploy.sh) ──────────────────
echo ""
echo "🗑️  Removing local build artifacts..."
rm -rf dist
rm -f notes-interceptor-lambda.zip
echo "   ✅ Removed dist/ and notes-interceptor-lambda.zip (if present)"

echo ""
if [ ${#ERRORS[@]} -gt 0 ]; then
    echo "❌ interceptor-notes/cleanup.sh finished with ${#ERRORS[@]} error(s):"
    for err in "${ERRORS[@]}"; do
        echo "   • $err"
    done
    exit 1
fi
echo "✨ Notes interceptor cleanup complete!"
