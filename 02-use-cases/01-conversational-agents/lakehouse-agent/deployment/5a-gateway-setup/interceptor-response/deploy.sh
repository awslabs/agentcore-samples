#!/bin/bash
# Deploy Gateway Response Interceptor Lambda Function

set -e

# Resolve the Python interpreter explicitly rather than trusting PATH (finding O4).
# A reader who has not activated the sample venv would otherwise get the system
# interpreter, installing the Lambda dependencies against a different Python than
# the one that runs the rest of the tutorial. Prefer the sample venv, fall back to
# python3, and allow an override for anyone using a different environment.
SAMPLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
if [ -z "${PYTHON_BIN:-}" ]; then
    if [ -x "$SAMPLE_ROOT/.venv/bin/python" ]; then
        PYTHON_BIN="$SAMPLE_ROOT/.venv/bin/python"
    else
        PYTHON_BIN="$(command -v python3 || command -v python)"
    fi
fi
if [ -z "$PYTHON_BIN" ]; then
    echo "❌ Error: no Python interpreter found (set PYTHON_BIN to override)"
    exit 1
fi
echo "   Python: $PYTHON_BIN"

# Ensure common tool paths are available (e.g. when run from a notebook subprocess)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "🚀 Deploying Gateway Response Interceptor Lambda"

# Get AWS region from default configuration
AWS_REGION=$(aws configure get region)
if [ -z "$AWS_REGION" ]; then
    echo "❌ Error: AWS region not configured"
    echo "   Please run: aws configure set region <your-region>"
    exit 1
fi

echo "   Region: $AWS_REGION"

# IdP selector (DR-8 Flag-2, R4/M3): fail-fast SSM read — NO env override, NO
# implicit default (matches utils.idp_config.get_idp_provider). The flag is set
# once in notebook 01 Step-0 (or `python -m utils.idp_config <cognito|okta>`).
# Build the Lambda env var block for the active IdP once, used by BOTH the
# create and update paths below.
IDP_PROVIDER=$(aws ssm get-parameter --name /app/lakehouse-agent/idp-provider --query 'Parameter.Value' --output text 2>/dev/null)
if [ -z "$IDP_PROVIDER" ] || [ "$IDP_PROVIDER" = "None" ]; then
    echo "❌ Error: IDP_PROVIDER not set in SSM (/app/lakehouse-agent/idp-provider)"
    echo "   Run notebook 01 (Step-0) first, or: python -m utils.idp_config <cognito|okta>"
    exit 1
fi
if [ "$IDP_PROVIDER" != "cognito" ] && [ "$IDP_PROVIDER" != "okta" ]; then
    echo "❌ Error: invalid IDP_PROVIDER='$IDP_PROVIDER' (allowed: cognito|okta)"
    exit 1
fi
echo "   IdP Provider: $IDP_PROVIDER"

if [ "$IDP_PROVIDER" = "cognito" ]; then
    COGNITO_USER_POOL_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/cognito-user-pool-id --query 'Parameter.Value' --output text 2>/dev/null)
    COGNITO_APP_CLIENT_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/cognito-app-client-id --query 'Parameter.Value' --output text 2>/dev/null)
    if [ -z "$COGNITO_USER_POOL_ID" ] || [ -z "$COGNITO_APP_CLIENT_ID" ]; then
        echo "❌ Failed to retrieve Cognito configuration from SSM"
        echo "   Please ensure the request interceptor has been deployed first"
        exit 1
    fi
    LAMBDA_ENV_VARS="COGNITO_REGION=$AWS_REGION,COGNITO_USER_POOL_ID=$COGNITO_USER_POOL_ID,COGNITO_APP_CLIENT_ID=$COGNITO_APP_CLIENT_ID,IDP_PROVIDER=$IDP_PROVIDER,TENANT_ROLE_MAPPING_TABLE=lakehouse_tenant_role_map"
else
    OKTA_ORG_URL=$(aws ssm get-parameter --name /app/lakehouse-agent/okta-org-url --query 'Parameter.Value' --output text 2>/dev/null)
    OKTA_AUTH_SERVER_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/okta-auth-server-id --query 'Parameter.Value' --output text 2>/dev/null)
    OKTA_RESOURCE_SERVER_AUDIENCE=$(aws ssm get-parameter --name /app/lakehouse-agent/okta-resource-server-audience --query 'Parameter.Value' --output text 2>/dev/null)
    if [ -z "$OKTA_ORG_URL" ] || [ -z "$OKTA_AUTH_SERVER_ID" ] || [ -z "$OKTA_RESOURCE_SERVER_AUDIENCE" ]; then
        echo "❌ Failed to retrieve Okta configuration from SSM"
        echo "   Please ensure the request interceptor has been deployed first"
        exit 1
    fi
    LAMBDA_ENV_VARS="OKTA_ORG_URL=$OKTA_ORG_URL,OKTA_AUTH_SERVER_ID=$OKTA_AUTH_SERVER_ID,OKTA_RESOURCE_SERVER_AUDIENCE=$OKTA_RESOURCE_SERVER_AUDIENCE,IDP_PROVIDER=$IDP_PROVIDER,TENANT_ROLE_MAPPING_TABLE=lakehouse_tenant_role_map"
fi

# Package Lambda function
echo ""
echo "📦 Packaging Lambda function..."

mkdir -p dist
"$PYTHON_BIN" -m pip install -r requirements.txt -t dist/ --platform manylinux2014_x86_64 --only-binary=:all:
cp lambda_function.py dist/

cd dist
zip -r ../response-interceptor-lambda.zip .
cd ..

echo "✅ Package created: response-interceptor-lambda.zip"

# Get Lambda role ARN from SSM Parameter Store (shared with request interceptor)
echo ""
echo "🔍 Retrieving Lambda execution role..."
LAMBDA_ROLE_ARN=$(aws ssm get-parameter --name /app/lakehouse-agent/interceptor-lambda-role-arn --query 'Parameter.Value' --output text 2>/dev/null)

# Fallback to direct IAM query if not in SSM yet
if [ -z "$LAMBDA_ROLE_ARN" ]; then
    echo "   Retrieving role ARN from IAM..."
    LAMBDA_ROLE_ARN=$(aws iam get-role --role-name InsuranceClaimsGatewayInterceptorRole --query 'Role.Arn' --output text 2>/dev/null)
fi

if [ -z "$LAMBDA_ROLE_ARN" ]; then
    echo "❌ Failed to retrieve Lambda role ARN"
    echo "   Please ensure the request interceptor has been deployed first"
    echo "   Run: cd ../interceptor-request && ./deploy.sh"
    exit 1
fi

echo "✅ Lambda role ready: $LAMBDA_ROLE_ARN"

# Check if Lambda function already exists
echo ""
echo "🔍 Checking if Lambda function exists..."
if aws lambda get-function --function-name lakehouse-gateway-response-interceptor --region $AWS_REGION 2>/dev/null; then
    echo "📝 Updating existing Lambda function..."
    aws lambda update-function-code \
        --function-name lakehouse-gateway-response-interceptor \
        --zip-file fileb://response-interceptor-lambda.zip \
        --region $AWS_REGION

    # update-function-code returns while Lambda is still applying the change
    # (LastUpdateStatus=InProgress). Wait for it to settle before issuing
    # update-function-configuration, which otherwise races and fails with
    # ResourceConflictException.
    aws lambda wait function-updated \
        --function-name lakehouse-gateway-response-interceptor \
        --region $AWS_REGION

    echo "⚙️  Updating Lambda configuration..."
    aws lambda update-function-configuration \
        --function-name lakehouse-gateway-response-interceptor \
        --environment "Variables={$LAMBDA_ENV_VARS}" \
        --kms-key-arn "" \
        --region $AWS_REGION

    aws lambda wait function-updated \
        --function-name lakehouse-gateway-response-interceptor \
        --region $AWS_REGION

    echo "✅ Lambda function updated!"
else
    echo "📝 Creating new Lambda function..."

    # (IdP config + LAMBDA_ENV_VARS already resolved near the top of this script.)

    # Retry logic for role propagation
    MAX_RETRIES=3
    RETRY_COUNT=0
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if aws lambda create-function \
            --function-name lakehouse-gateway-response-interceptor \
            --runtime python3.11 \
            --role $LAMBDA_ROLE_ARN \
            --handler lambda_function.lambda_handler \
            --zip-file fileb://response-interceptor-lambda.zip \
            --timeout 30 \
            --memory-size 256 \
            --environment "Variables={$LAMBDA_ENV_VARS}" \
            --tags Application=lakehouse-agent,Purpose=claims-response-interceptor \
            --region $AWS_REGION 2>/dev/null; then
            aws lambda wait function-active \
                --function-name lakehouse-gateway-response-interceptor \
                --region $AWS_REGION
            echo "✅ Lambda function created!"
            break
        else
            RETRY_COUNT=$((RETRY_COUNT + 1))
            if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
                echo "⏳ Role not ready yet, waiting 5 seconds (attempt $RETRY_COUNT/$MAX_RETRIES)..."
                sleep 5
            else
                echo "❌ Failed to create Lambda function after $MAX_RETRIES attempts"
                echo "   The IAM role may need more time to propagate"
                exit 1
            fi
        fi
    done
fi

# Store Lambda function ARN in SSM Parameter Store
echo ""
echo "💾 Storing Lambda function ARN in SSM Parameter Store..."
LAMBDA_FUNCTION_ARN=$(aws lambda get-function --function-name lakehouse-gateway-response-interceptor --region $AWS_REGION --query 'Configuration.FunctionArn' --output text)

aws ssm put-parameter \
    --name /app/lakehouse-agent/response-interceptor-lambda-arn \
    --value "$LAMBDA_FUNCTION_ARN" \
    --type String \
    --overwrite \
    --region $AWS_REGION

echo "✅ Stored parameter: /app/lakehouse-agent/response-interceptor-lambda-arn"

# Configure CloudWatch Logs retention for the Lambda log group.
# create-log-group is idempotent via `|| true`; put-retention-policy is then guaranteed to find it.
echo ""
echo "🪵 Configuring CloudWatch Logs retention for Lambda log group..."
LOG_GROUP_NAME="/aws/lambda/lakehouse-gateway-response-interceptor"
aws logs create-log-group \
    --log-group-name "$LOG_GROUP_NAME" \
    --region "$AWS_REGION" 2>/dev/null || true
aws logs put-retention-policy \
    --log-group-name "$LOG_GROUP_NAME" \
    --retention-in-days 30 \
    --region "$AWS_REGION"
echo "✅ Log group $LOG_GROUP_NAME retention set to 30 days"

echo ""
echo "✨ Deployment complete!"
echo ""
echo "📝 Lambda Function ARN: $LAMBDA_FUNCTION_ARN"
echo ""
echo "💡 Next steps:"
echo "   1. Update your gateway configuration to include this response interceptor"
echo "   2. The interceptor will filter out 'x_amz_bedrock_agentcore_search' from tool lists"
