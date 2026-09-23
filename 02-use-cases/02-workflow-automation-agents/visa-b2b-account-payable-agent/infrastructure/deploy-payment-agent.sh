#!/bin/bash
# Deploy Payment Agent to AgentCore Runtime
set -e

# Activate Python virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "❌ Python virtual environment not found"
    echo "Please run: ./setup-python-env.sh"
    exit 1
fi

echo "Deploying Payment Agent..."

# Fix CodeBuild role permissions if needed
# Find the AgentCore CodeBuild role (auto-created by SDK with random suffix)
ROLE_NAME=$(aws iam list-roles \
  --query "Roles[?starts_with(RoleName, 'AmazonBedrockAgentCoreSDKCodeBuild-us-east-1-')].RoleName" \
  --output text | head -1)

if [ -n "$ROLE_NAME" ]; then
    echo "Found CodeBuild role: $ROLE_NAME"
    echo "Ensuring CodeBuild role has ECR permissions..."
    aws iam put-role-policy \
      --role-name "$ROLE_NAME" \
      --policy-name "ECRPushPermissions" \
      --policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
          "Effect": "Allow",
          "Action": [
            "ecr:GetAuthorizationToken",
            "ecr:BatchCheckLayerAvailability",
            "ecr:GetDownloadUrlForLayer",
            "ecr:BatchGetImage",
            "ecr:PutImage",
            "ecr:InitiateLayerUpload",
            "ecr:UploadLayerPart",
            "ecr:CompleteLayerUpload"
          ],
          "Resource": "*"
        }]
      }' 2>/dev/null || echo "Role permissions already set"
    
    echo "Waiting 10 seconds for IAM policy propagation..."
    sleep 10
else
    echo "⚠️  CodeBuild role not found - will be created on first deployment"
fi

cd lambda/payment-agents

python3 << 'PYTHON_EOF'
import boto3
import json
import time
from pathlib import Path
from bedrock_agentcore_starter_toolkit import Runtime

# Get CloudFormation outputs
cfn = boto3.client('cloudformation', region_name='us-east-1')
sts = boto3.client('sts')
account_id = sts.get_caller_identity()['Account']

response = cfn.describe_stacks(StackName='RtpOverlayStack')
outputs = response['Stacks'][0]['Outputs']
database_secret_arn = next(
    (o['OutputValue'] for o in outputs if o['OutputKey'] == 'DatabaseSecretArn'),
    None
)

# Get Gateway URL
gateway_config = Path("../../.agentcore-gateway-config.json")
with open(gateway_config) as f:
    gateway_url = json.load(f)['gateway_url']

# Environment variables
env_vars = {
    "GATEWAY_URL": gateway_url,
    "DATABASE_SECRET_ARN": database_secret_arn,
    "OUTPUT_BUCKET": f"iso20022-output-bucket-{account_id}",
    "ISO20022_LAMBDA_ARN": f"arn:aws:lambda:us-east-1:{account_id}:function:rtp-overlay-invoice-processor",
    "BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0"
}

print(f"Gateway URL: {gateway_url}")
print(f"Database Secret: {database_secret_arn}")

# Deploy
runtime = Runtime()
runtime.configure(
    entrypoint="payment_agent.py",
    requirements_file="requirements.txt",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    agent_name="visa_b2b_payment_agent",
    region="us-east-1"
)

result = runtime.launch(env_vars=env_vars, auto_update_on_conflict=True)
print(f"Agent ARN: {result.agent_arn}")

# Wait for ready
status = runtime.status()
while status.endpoint['status'] not in ['READY', 'CREATE_FAILED']:
    time.sleep(10)
    status = runtime.status()
    print(f"Status: {status.endpoint['status']}")

print(f"Final status: {status.endpoint['status']}")

# Save agent ARN to SSM Parameter Store for backend Lambda
if status.endpoint['status'] == 'READY':
    ssm = boto3.client('ssm', region_name='us-east-1')
    ssm.put_parameter(
        Name='/rtp-overlay/payment-agent-arn',
        Value=result.agent_arn,
        Type='String',
        Overwrite=True,
        Description='Payment Agent ARN for backend Lambda integration'
    )
    print(f"✓ Saved agent ARN to SSM: /rtp-overlay/payment-agent-arn")
else:
    print("✗ Agent deployment failed, not saving to SSM")
PYTHON_EOF

cd ../..

# ============================================================================
# Update Backend Lambda with Payment Agent ARN
# ============================================================================

echo ""
echo "📦 Updating Backend Lambda with Payment Agent ARN..."
echo ""

# Get Payment Agent ARN from SSM
PAYMENT_AGENT_ARN=$(aws ssm get-parameter \
    --name "/rtp-overlay/payment-agent-arn" \
    --query 'Parameter.Value' \
    --output text \
    --region us-east-1 2>/dev/null || echo "")

if [ -z "$PAYMENT_AGENT_ARN" ]; then
    echo "⚠️  Payment Agent ARN not found in SSM - skipping Backend Lambda update"
else
    # Find Backend Lambda
    BACKEND_LAMBDA_NAME=$(aws lambda list-functions \
        --query "Functions[?contains(FunctionName, 'RtpOverlayLambdaStack-ApiLambda')].FunctionName" \
        --output text \
        --region us-east-1 | head -1)
    
    if [ -n "$BACKEND_LAMBDA_NAME" ]; then
        echo "Found Backend Lambda: $BACKEND_LAMBDA_NAME"
        
        # Get current environment variables
        CURRENT_ENV=$(aws lambda get-function-configuration \
            --function-name $BACKEND_LAMBDA_NAME \
            --query 'Environment.Variables' \
            --output json \
            --region us-east-1)
        
        # Add PAYMENT_AGENT_ARN
        UPDATED_ENV=$(echo $CURRENT_ENV | jq -c ". + {\"PAYMENT_AGENT_ARN\": \"$PAYMENT_AGENT_ARN\"}")
        
        # Update Lambda
        aws lambda update-function-configuration \
            --function-name $BACKEND_LAMBDA_NAME \
            --environment "{\"Variables\":$UPDATED_ENV}" \
            --region us-east-1 \
            --output json > /dev/null
        
        echo "✓ Backend Lambda environment updated with Payment Agent ARN"
        echo ""
        echo "⚠️  NOTE: You need to redeploy Backend Lambda to pick up the new environment variable:"
        echo "   ./deploy-lambda.sh"
    else
        echo "⚠️  Backend Lambda not found - will need to add PAYMENT_AGENT_ARN manually"
    fi
fi

echo ""
echo "🔐 Granting Payment Agent permission to invoke ISO20022 Lambda..."

# Get AWS Account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Find the AgentCore Runtime role (auto-created by SDK with random suffix)
RUNTIME_ROLE_NAME=$(aws iam list-roles \
  --query "Roles[?starts_with(RoleName, 'AmazonBedrockAgentCoreSDKRuntime-us-east-1-')].RoleName" \
  --output text | head -1)

if [ -n "$RUNTIME_ROLE_NAME" ]; then
    echo "Found Runtime role: $RUNTIME_ROLE_NAME"
    ISO20022_LAMBDA_ARN="arn:aws:lambda:us-east-1:${ACCOUNT_ID}:function:rtp-overlay-invoice-processor"
    
    aws iam put-role-policy \
        --role-name "$RUNTIME_ROLE_NAME" \
        --policy-name "InvokeISO20022Lambda" \
        --policy-document "{
            \"Version\": \"2012-10-17\",
            \"Statement\": [{
                \"Effect\": \"Allow\",
                \"Action\": \"lambda:InvokeFunction\",
                \"Resource\": \"$ISO20022_LAMBDA_ARN\"
            }]
        }" 2>/dev/null || echo "✓ Policy already exists"
    
    echo "✓ Payment Agent granted permission to invoke ISO20022 Lambda"
else
    echo "⚠️  Runtime role not found - will need to grant permissions after first agent deployment"
fi
echo ""
echo "✅ Payment Agent Deployment Complete!"
echo ""

# ============================================================================
# Automatically redeploy AgentCore Invoker with Payment Agent ARN
# ============================================================================

echo "📦 Redeploying AgentCore Invoker to pick up Payment Agent ARN..."
echo ""

cd "$SCRIPT_DIR"
./deploy-agentcore-invoker.sh

echo ""
echo "🔐 Redeploying AgentCore Gateway to grant Runtime role permissions..."
echo ""

cd "$SCRIPT_DIR"
./deploy-agentcore-gateway.sh

echo ""
echo "✅ Complete Payment Agent Integration Finished!"
echo ""
echo "📝 Next steps:"
echo "1. Deploy GR Processing: ./deploy-gr-processing.sh"
echo "2. Configure S3 notifications: ./configure-s3-notifications.sh"
echo "3. Test payment processing from the frontend"
