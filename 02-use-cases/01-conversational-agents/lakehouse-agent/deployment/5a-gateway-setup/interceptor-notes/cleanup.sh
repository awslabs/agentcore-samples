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

# aws_capture <aws args...> runs an aws command and keeps its output in AWS_OUT and its error
# text in AWS_ERR (leading blank lines removed), so the caller can tell one expected error code
# from any other failure. Returns the command's exit status.
aws_capture() {
    local err_file status
    err_file=$(mktemp)
    if AWS_OUT=$(aws "$@" 2>"$err_file"); then status=0; else status=$?; fi
    AWS_ERR=$(cat "$err_file")
    rm -f "$err_file"
    AWS_ERR="${AWS_ERR#"${AWS_ERR%%[![:space:]]*}"}"
    return $status
}

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
# deploy.sh records the role's id in SSM when it creates the role. The role is deleted
# only if its id still matches that record and it carries nothing beyond what deploy.sh
# attaches, so a same-name role this sample did not create is never deleted.
ROLE_RECORD="/app/lakehouse-agent/role-ids/$ROLE_NAME"
ROLE_LEFT=false
echo ""
echo "🗑️  Deleting IAM role: $ROLE_NAME"
RECORD_READ=true
RECORDED_ID=""
if aws_capture ssm get-parameter --name "$ROLE_RECORD" --query Parameter.Value --output text --region "$AWS_REGION"; then
    RECORDED_ID="$AWS_OUT"
elif [[ "$AWS_ERR" != *"(ParameterNotFound)"* ]]; then
    RECORD_READ=false
    echo "   ❌ Could not read the ownership record for IAM role $ROLE_NAME: $AWS_ERR. Nothing was changed."
    ERRORS+=("IAM role $ROLE_NAME: could not read the ownership record: $AWS_ERR")
fi
if [ "$RECORD_READ" = false ]; then
    :
elif aws_capture iam get-role --role-name "$ROLE_NAME" --query Role.RoleId --output text; then
    ROLE_ID="$AWS_OUT"
    if [ -z "$RECORDED_ID" ]; then
        echo "   ⚠  IAM role $ROLE_NAME exists but this sample has no ownership record for it, so it was left in place. If it is yours, remove its policies and run: aws iam delete-role --role-name $ROLE_NAME"
        ROLE_LEFT=true
    elif [ "$RECORDED_ID" != "$ROLE_ID" ]; then
        echo "   ❌ IAM role $ROLE_NAME is not the role this sample created (its role id does not match the ownership record). Nothing was deleted."
        ERRORS+=("IAM role $ROLE_NAME: role id does not match the ownership record")
    elif ! aws_capture iam list-role-policies --role-name "$ROLE_NAME" --query 'PolicyNames[]' --output text; then
        echo "   ❌ Error deleting $ROLE_NAME: $AWS_ERR"
        ERRORS+=("IAM role $ROLE_NAME: $AWS_ERR")
    else
        INLINE_POLICIES="$AWS_OUT"
        if ! aws_capture iam list-attached-role-policies --role-name "$ROLE_NAME" --query 'AttachedPolicies[].PolicyArn' --output text; then
            echo "   ❌ Error deleting $ROLE_NAME: $AWS_ERR"
            ERRORS+=("IAM role $ROLE_NAME: $AWS_ERR")
        else
            MANAGED_POLICIES="$AWS_OUT"
            if ! aws_capture iam list-instance-profiles-for-role --role-name "$ROLE_NAME" --query 'InstanceProfiles[].InstanceProfileName' --output text; then
                echo "   ❌ Error deleting $ROLE_NAME: $AWS_ERR"
                ERRORS+=("IAM role $ROLE_NAME: $AWS_ERR")
            else
                # deploy.sh adds no inline policy and no instance profile, and attaches only
                # AWSLambdaBasicExecutionRole; anything else was added by someone else.
                UNEXPECTED=()
                for pol in $INLINE_POLICIES; do
                    UNEXPECTED+=("$pol")
                done
                for arn in $MANAGED_POLICIES; do
                    if [ "$arn" != "$BASIC_EXEC_POLICY_ARN" ]; then
                        UNEXPECTED+=("$arn")
                    fi
                done
                for profile in $AWS_OUT; do
                    UNEXPECTED+=("$profile")
                done
                if [ ${#UNEXPECTED[@]} -gt 0 ]; then
                    UNEXPECTED_LIST=""
                    while IFS= read -r item; do
                        UNEXPECTED_LIST="${UNEXPECTED_LIST:+$UNEXPECTED_LIST, }$item"
                    done < <(printf '%s\n' "${UNEXPECTED[@]}" | LC_ALL=C sort)
                    echo "   ❌ IAM role $ROLE_NAME has attachments this sample did not add: $UNEXPECTED_LIST. Nothing was deleted. Remove them, or delete the role by hand."
                    ERRORS+=("IAM role $ROLE_NAME: attachments this sample did not add: $UNEXPECTED_LIST")
                else
                    # A failed detach or delete stops the script (set -e) before its ✅ line.
                    for arn in $MANAGED_POLICIES; do
                        aws iam detach-role-policy --role-name "$ROLE_NAME" --policy-arn "$arn"
                        echo "   ✅ Detached managed policy: $arn"
                    done
                    aws iam delete-role --role-name "$ROLE_NAME"
                    if aws_capture ssm delete-parameter --name "$ROLE_RECORD" --region "$AWS_REGION" || [[ "$AWS_ERR" == *"(ParameterNotFound)"* ]]; then
                        echo "   ✅ Deleted role: $ROLE_NAME"
                    else
                        echo "   ❌ Error deleting $ROLE_NAME: $AWS_ERR"
                        ERRORS+=("IAM role $ROLE_NAME: $AWS_ERR")
                    fi
                fi
            fi
        fi
    fi
elif [[ "$AWS_ERR" == *"(NoSuchEntity)"* ]]; then
    if [ -z "$RECORDED_ID" ]; then
        echo "   ⏭  Role not found: $ROLE_NAME"
    elif aws_capture ssm delete-parameter --name "$ROLE_RECORD" --region "$AWS_REGION" || [[ "$AWS_ERR" == *"(ParameterNotFound)"* ]]; then
        echo "   ⏭  Role already deleted: $ROLE_NAME (ownership record cleared)"
    else
        echo "   ❌ Error deleting $ROLE_NAME: $AWS_ERR"
        ERRORS+=("IAM role $ROLE_NAME: $AWS_ERR")
    fi
else
    echo "   ❌ Could not check IAM role $ROLE_NAME: $AWS_ERR"
    ERRORS+=("IAM role $ROLE_NAME: $AWS_ERR")
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
# Exit 3: nothing failed, but the role was left in place because this sample could not
# prove it created it (see step 2).
if [ "$ROLE_LEFT" = true ]; then
    echo "⚠  interceptor-notes/cleanup.sh finished; IAM role $ROLE_NAME was left in place (see above)."
    exit 3
fi
echo "✨ Notes interceptor cleanup complete!"
