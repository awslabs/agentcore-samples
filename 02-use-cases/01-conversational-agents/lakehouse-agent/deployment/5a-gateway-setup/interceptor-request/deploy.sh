#!/bin/bash
# Deploy Gateway Interceptor Lambda Function

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

echo "🚀 Deploying Gateway Interceptor Lambda"

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

# Read configuration from SSM Parameter Store, and build the Lambda env var block
# for the active IdP. The Lambda code reads IDP_PROVIDER + the IdP-specific keys
# (falling back to SSM if an env var is absent).
echo ""
echo "🔍 Loading configuration from SSM Parameter Store..."

if [ "$IDP_PROVIDER" = "cognito" ]; then
    # [COGNITO] upstream verbatim param loads
    set +e
    COGNITO_USER_POOL_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/cognito-user-pool-id --query 'Parameter.Value' --output text 2>&1)
    COGNITO_RESULT=$?
    COGNITO_APP_CLIENT_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/cognito-app-client-id --query 'Parameter.Value' --output text 2>&1)
    CLIENT_RESULT=$?
    set -e

    if [ $COGNITO_RESULT -ne 0 ] || [ $CLIENT_RESULT -ne 0 ]; then
        echo "❌ Error: Required SSM parameters not found"
        [ $COGNITO_RESULT -ne 0 ] && echo "   Missing: /app/lakehouse-agent/cognito-user-pool-id ($COGNITO_USER_POOL_ID)"
        [ $CLIENT_RESULT -ne 0 ] && echo "   Missing: /app/lakehouse-agent/cognito-app-client-id ($COGNITO_APP_CLIENT_ID)"
        echo "   Please run notebook 01 (Cognito setup) first."
        exit 1
    fi

    echo "✅ Configuration loaded from SSM"
    echo "   Cognito User Pool ID: $COGNITO_USER_POOL_ID"
    echo "   Cognito App Client ID: $COGNITO_APP_CLIENT_ID"
    LAMBDA_ENV_VARS="COGNITO_REGION=$AWS_REGION,COGNITO_USER_POOL_ID=$COGNITO_USER_POOL_ID,COGNITO_APP_CLIENT_ID=$COGNITO_APP_CLIENT_ID,IDP_PROVIDER=$IDP_PROVIDER,TENANT_ROLE_MAPPING_TABLE=lakehouse_tenant_role_map"
else
    # [OKTA] custom-auth-server param loads (canonical §6 keys)
    set +e
    OKTA_ORG_URL=$(aws ssm get-parameter --name /app/lakehouse-agent/okta-org-url --query 'Parameter.Value' --output text 2>&1)
    ORG_RESULT=$?
    OKTA_AUTH_SERVER_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/okta-auth-server-id --query 'Parameter.Value' --output text 2>&1)
    AUTH_SERVER_RESULT=$?
    OKTA_RESOURCE_SERVER_AUDIENCE=$(aws ssm get-parameter --name /app/lakehouse-agent/okta-resource-server-audience --query 'Parameter.Value' --output text 2>&1)
    AUDIENCE_RESULT=$?
    set -e

    if [ $ORG_RESULT -ne 0 ] || [ $AUTH_SERVER_RESULT -ne 0 ] || [ $AUDIENCE_RESULT -ne 0 ]; then
        echo "❌ Error: Required SSM parameters not found"
        [ $ORG_RESULT -ne 0 ] && echo "   Missing: /app/lakehouse-agent/okta-org-url ($OKTA_ORG_URL)"
        [ $AUTH_SERVER_RESULT -ne 0 ] && echo "   Missing: /app/lakehouse-agent/okta-auth-server-id ($OKTA_AUTH_SERVER_ID)"
        [ $AUDIENCE_RESULT -ne 0 ] && echo "   Missing: /app/lakehouse-agent/okta-resource-server-audience ($OKTA_RESOURCE_SERVER_AUDIENCE)"
        echo "   Please run notebook 01 (Okta setup) first."
        exit 1
    fi

    echo "✅ Configuration loaded from SSM"
    echo "   Okta Org URL: $OKTA_ORG_URL"
    echo "   Okta Auth Server ID: $OKTA_AUTH_SERVER_ID"
    echo "   Okta Resource Server Audience: $OKTA_RESOURCE_SERVER_AUDIENCE"
    LAMBDA_ENV_VARS="OKTA_ORG_URL=$OKTA_ORG_URL,OKTA_AUTH_SERVER_ID=$OKTA_AUTH_SERVER_ID,OKTA_RESOURCE_SERVER_AUDIENCE=$OKTA_RESOURCE_SERVER_AUDIENCE,IDP_PROVIDER=$IDP_PROVIDER,TENANT_ROLE_MAPPING_TABLE=lakehouse_tenant_role_map"
fi

# Package Lambda function
echo ""
echo "📦 Packaging Lambda function..."

mkdir -p dist
"$PYTHON_BIN" -m pip install -r requirements.txt -t dist/ --platform manylinux2014_x86_64 --only-binary=:all:
cp lambda_function.py dist/
cp token_exchange.py dist/
cp tool_validation.py dist/

cd dist
zip -r ../interceptor-lambda.zip .
cd ..

echo "✅ Package created: interceptor-lambda.zip"

# Create Lambda role using Python script
echo ""
echo "🔑 Creating Lambda execution role..."
cd ..
"$PYTHON_BIN" create_lambda_role.py
cd interceptor-request

# Get the role ARN from SSM Parameter Store (stored by create_lambda_role.py)
LAMBDA_ROLE_ARN=$(aws ssm get-parameter --name /app/lakehouse-agent/interceptor-lambda-role-arn --query 'Parameter.Value' --output text 2>/dev/null)

# Fallback to direct IAM query if not in SSM yet
if [ -z "$LAMBDA_ROLE_ARN" ]; then
    echo "   Retrieving role ARN from IAM..."
    LAMBDA_ROLE_ARN=$(aws iam get-role --role-name InsuranceClaimsGatewayInterceptorRole --query 'Role.Arn' --output text 2>/dev/null)
fi

if [ -z "$LAMBDA_ROLE_ARN" ]; then
    echo "❌ Failed to retrieve Lambda role ARN"
    exit 1
fi

echo "✅ Lambda role ready: $LAMBDA_ROLE_ARN"

# Wait for IAM role to propagate (required for new roles)
echo "⏳ Waiting for IAM role to propagate (10 seconds)..."
sleep 10

# Check if Lambda function already exists
echo ""
echo "🔍 Checking if Lambda function exists..."
if aws lambda get-function --function-name lakehouse-gateway-interceptor --region $AWS_REGION 2>/dev/null; then
    echo "📝 Updating existing Lambda function..."
    aws lambda update-function-code \
        --function-name lakehouse-gateway-interceptor \
        --zip-file fileb://interceptor-lambda.zip \
        --region $AWS_REGION

    # update-function-code returns while Lambda is still applying the change
    # (LastUpdateStatus=InProgress). Wait for it to settle before issuing
    # update-function-configuration, which otherwise races and fails with
    # ResourceConflictException.
    aws lambda wait function-updated \
        --function-name lakehouse-gateway-interceptor \
        --region $AWS_REGION

    echo "⚙️  Updating Lambda configuration..."
    aws lambda update-function-configuration \
        --function-name lakehouse-gateway-interceptor \
        --environment "Variables={$LAMBDA_ENV_VARS}" \
        --kms-key-arn "" \
        --region $AWS_REGION

    aws lambda wait function-updated \
        --function-name lakehouse-gateway-interceptor \
        --region $AWS_REGION

    echo "✅ Lambda function updated!"
else
    echo "📝 Creating new Lambda function..."
    
    # Retry logic for role propagation
    MAX_RETRIES=3
    RETRY_COUNT=0
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if aws lambda create-function \
            --function-name lakehouse-gateway-interceptor \
            --runtime python3.11 \
            --role $LAMBDA_ROLE_ARN \
            --handler lambda_function.lambda_handler \
            --zip-file fileb://interceptor-lambda.zip \
            --timeout 30 \
            --memory-size 256 \
            --environment "Variables={$LAMBDA_ENV_VARS}" \
            --tags Application=lakehouse-agent,Purpose=claims-request-interceptor \
            --region $AWS_REGION 2>/dev/null; then
            aws lambda wait function-active \
                --function-name lakehouse-gateway-interceptor \
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
LAMBDA_FUNCTION_ARN=$(aws lambda get-function --function-name lakehouse-gateway-interceptor --region $AWS_REGION --query 'Configuration.FunctionArn' --output text)

aws ssm put-parameter \
    --name /app/lakehouse-agent/interceptor-lambda-arn \
    --value "$LAMBDA_FUNCTION_ARN" \
    --type String \
    --overwrite \
    --region $AWS_REGION

echo "✅ Stored parameter: /app/lakehouse-agent/interceptor-lambda-arn"

# Configure CloudWatch Logs retention for the Lambda log group.
# create-log-group is idempotent via `|| true`; put-retention-policy is then guaranteed to find it.
echo ""
echo "🪵 Configuring CloudWatch Logs retention for Lambda log group..."
LOG_GROUP_NAME="/aws/lambda/lakehouse-gateway-interceptor"
aws logs create-log-group \
    --log-group-name "$LOG_GROUP_NAME" \
    --region "$AWS_REGION" 2>/dev/null || true
aws logs put-retention-policy \
    --log-group-name "$LOG_GROUP_NAME" \
    --retention-in-days 30 \
    --region "$AWS_REGION"
echo "✅ Log group $LOG_GROUP_NAME retention set to 30 days"

# Setup DynamoDB tenant-role mapping table
echo ""
echo "📊 Setting up DynamoDB tenant-role mapping table..."
"$PYTHON_BIN" setup_dynamodb_tenant_role_maps.py

if [ $? -ne 0 ]; then
    echo "❌ Failed to setup DynamoDB table"
    exit 1
fi

echo ""
echo "✨ Deployment complete!"
echo ""
echo "📝 Lambda Function ARN: $LAMBDA_FUNCTION_ARN"
