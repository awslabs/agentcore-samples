# RTP Overlay - Quick Start Deployment

## Prerequisites

Before starting deployment, ensure you have:

- AWS CLI configured with valid credentials
- Node.js 18+ installed
- **CDK CLI 2.102.0+** installed: `npm install -g aws-cdk@latest`
- Docker installed and running
- **Python 3.8+** installed

**Verify CDK version:**
```bash
cdk --version
# Should show 2.102.0 or higher
```

**If you see "Cloud assembly schema version mismatch" error:**
```bash
npm install -g aws-cdk@latest
```

---

## One-Command Deployment (if everything is ready)

**First time only - setup Python environment:**
```bash
cd infrastructure
./setup-python-env.sh
cd ../
```

**Then deploy everything:**
```bash
cd infrastructure && \
./deploy.sh && \
./rebuild-lambda-layer.sh && \
./deploy-lambda.sh && \
./run-migrations.sh && \
./setup-kms-encryption.sh && \
./deploy-visa-stub.sh && \
./deploy-agentcore-gateway.sh && \
./deploy-invoice-processing.sh && \
./deploy-agentcore-invoker.sh && \
./deploy-payment-agent.sh && \
./deploy-gr-processing.sh && \
./configure-s3-notifications.sh
```

**What happens:**
- `deploy-agentcore-invoker.sh` automatically redeploys Backend Lambda to import the invoker function name
- `deploy-payment-agent.sh` automatically redeploys AgentCore Invoker (which redeploys Backend Lambda) and redeploys Gateway to grant permissions

---

## Step-by-Step (Recommended for first time)

```bash
# 0. Setup Python Environment (one-time) - 30 sec
cd infrastructure
./setup-python-env.sh

# 1. Infrastructure (VPC, RDS) - 15 min
./deploy.sh

# 2. Lambda Layer - 3 min
./rebuild-lambda-layer.sh

# 3. API Lambda - 5-10 min
./deploy-lambda.sh

# 4. Database - 30 sec
./run-migrations.sh

# 5. KMS Encryption Setup - 1-2 min
# Creates KMS key for encrypting virtual card numbers
./setup-kms-encryption.sh

# 6. Visa B2B Stub APIs - 3-5 min
./deploy-visa-stub.sh

# 7. AgentCore Gateway (Visa B2B Payment Integration) - 1-2 min
# Note: This automatically generates OpenAPI spec from stub API URL
# Saves gateway info to SSM Parameter Store for Payment Agent
./deploy-agentcore-gateway.sh

# 8. Invoice Processing - 3-5 min
# ⚠️ REQUIRED BEFORE Payment Agent (provides ISO20022 Lambda ARN)
# Creates invoice input bucket and ISO20022 output bucket
./deploy-invoice-processing.sh

# 9. AgentCore Invoker (Python Bridge) - 3-5 min
# Creates Python Lambda to bridge TypeScript backend with Bedrock AgentCore SDK
# Automatically redeploys Backend Lambda to import the invoker function name
./deploy-agentcore-invoker.sh

# 10. Payment Agent - 5-10 min
# ⚠️ Requires: Gateway (step 7) + Invoice Processing (step 8) + AgentCore Invoker (step 9)
# Reads gateway info from local JSON file
# Automatically redeploys AgentCore Invoker with Payment Agent ARN (which redeploys Backend Lambda)
# Automatically redeploys Gateway to grant Runtime role permissions
./deploy-payment-agent.sh

# 11. GR Processing - 3-5 min
# Creates GR input bucket
./deploy-gr-processing.sh

# 12. Configure S3 Notifications - 1 min
./configure-s3-notifications.sh
```

**Total Time:** ~40-50 minutes

---

## What Does AgentCore Gateway Deployment Do?

The `deploy-agentcore-gateway.sh` script performs these steps automatically:

1. **Generates OpenAPI Spec** - Gets stub API URL from CloudFormation and creates minimal OpenAPI spec
2. **Creates S3 Bucket** - `agentcore-gateway-specs-{account-id}` for storing specs
3. **Uploads Spec to S3** - Makes it available for gateway configuration
4. **Creates IAM Role** - `AgentCoreGatewayRole` with S3 and Secrets Manager permissions
5. **Creates Gateway** - `visa-b2b-payment-gateway` with IAM authorizer
6. **Creates OpenAPI Target** - Converts 3 Visa B2B APIs into MCP tools
7. **Saves Configuration** - `.agentcore-gateway-config.json` for agent use

**MCP Tools Generated:**
- `VirtualCardRequisition` - Create virtual payment card
- `ProcessPayments` - Process payment transaction
- `GetPaymentDetails` - Get payment status

---

## What Does Payment Agent Deployment Do?

The `deploy-payment-agent.sh` script fully automates deployment of the AI-powered Payment Agent to AgentCore Runtime.

**What the Payment Agent Does:**

- **AI Decision Making** - Uses Bedrock (Claude) to decide: Visa B2B or ISO20022
- **Visa B2B Payments** - Calls Gateway MCP tools to create virtual cards and process payments
- **ISO20022 Fallback** - Generates bank transfer files for large amounts or when Visa B2B isn't suitable
- **Audit Trail** - Writes to database BEFORE executing payments (tracks agent reasoning)

**Automated Deployment:**

The script automatically:

1. Fixes CodeBuild IAM permissions for ECR push
2. Retrieves environment variables from CloudFormation and Gateway config
3. Configures AgentCore Runtime with agent code and dependencies
4. Launches agent to AgentCore Runtime with environment variables
5. Waits for deployment to complete (status: READY)
6. Returns agent ARN for backend integration

Run the deployment:

```bash
cd infrastructure
./deploy-payment-agent.sh
```

**Environment Variables (auto-configured):**
- `GATEWAY_URL` - From `.agentcore-gateway-config.json`
- `DATABASE_SECRET_ARN` - From CloudFormation RtpOverlayStack
- `OUTPUT_BUCKET` - Constructed from AWS account ID
- `ISO20022_LAMBDA_ARN` - Constructed from AWS account ID
- `RTP_API_URL` - From CloudFormation RtpOverlayLambdaStack
- `BEDROCK_MODEL_ID` - Claude Sonnet 4 model ID

**Prerequisites:**

- Gateway deployed (step 9)
- Invoice Processing deployed (step 10) - provides ISO20022 Lambda ARN
- Python package: `pip3 install bedrock-agentcore-starter-toolkit`

---

## Get API URL

```bash
aws cloudformation describe-stacks \
  --stack-name RtpOverlayLambdaStack \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text
```

---

## Configure Frontend

```bash
cd rtp-overlay
echo "VITE_API_BASE_URL=$(aws cloudformation describe-stacks --stack-name RtpOverlayLambdaStack --region us-east-1 --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' --output text)" > .env
npm install
```

### Local Development

```bash
npm run dev
```

Open: <http://localhost:5173>  

### Test Both Portals

**Buyer Portal** (Main Application):

- URL: <http://localhost:5173>
- Login: admin / admin123
- Features: Invoice management, payment processing, virtual card display

**Supplier Portal** (External Access):

- URL: <http://localhost:5173/supplier/login>
- Login: Use Payment ID or Tracking Number from processed payment
- Features: View virtual card details, copy card information

### Deploy to Amplify (Optional)

```bash
cd rtp-overlay
./build-and-zip.sh
```

Then upload `rtp-overlay-deployment.zip` to [AWS Amplify Console](https://console.aws.amazon.com/amplify/)

---

## Test End-to-End Payment Flow

### 1. Upload Invoice

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 cp test-invoice.pdf s3://rtp-overlay-invoices-input-${ACCOUNT_ID}/
```

### 2. Process Payment (Buyer Portal)

1. Open buyer portal: <http://localhost:5173>
2. Login: admin / admin123
3. Navigate to Invoices
4. Click on an invoice
5. Click "Process Payment" button
6. View virtual card details (masked by default)
7. Click "Show" to reveal full card number
8. Copy Payment ID or Tracking Number

### 3. Access Card (Supplier Portal)

1. Open supplier portal: <http://localhost:5173/supplier/login>
2. Enter Payment ID or Tracking Number from step 2
3. View unmasked virtual card details
4. Copy card information

### 4. Test Goods Receipt (Optional)

```bash
aws s3 cp test-receipt.jpg s3://rtp-overlay-receipts-input-${ACCOUNT_ID}/receipts/
```

---

## Cleanup - Remove All Resources

To avoid accumulating AWS costs after testing, follow these steps to remove all deployed resources:

### Quick Cleanup (One Command)

```bash
cd infrastructure

# Delete all CloudFormation stacks
aws cloudformation delete-stack --stack-name GRProcessingStack --region us-east-1 && \
aws cloudformation delete-stack --stack-name InvoiceProcessingStack --region us-east-1 && \
aws cloudformation delete-stack --stack-name AgentCoreInvokerStack --region us-east-1 && \
aws cloudformation delete-stack --stack-name VisaStubStack --region us-east-1 && \
aws cloudformation delete-stack --stack-name RtpOverlayLambdaStack --region us-east-1 && \
aws cloudformation delete-stack --stack-name RtpOverlayStack --region us-east-1

# Wait for stacks to delete (this may take 10-15 minutes)
echo "Waiting for stacks to delete..."
aws cloudformation wait stack-delete-complete --stack-name GRProcessingStack --region us-east-1 2>/dev/null
aws cloudformation wait stack-delete-complete --stack-name InvoiceProcessingStack --region us-east-1 2>/dev/null
aws cloudformation wait stack-delete-complete --stack-name AgentCoreInvokerStack --region us-east-1 2>/dev/null
aws cloudformation wait stack-delete-complete --stack-name VisaStubStack --region us-east-1 2>/dev/null
aws cloudformation wait stack-delete-complete --stack-name RtpOverlayLambdaStack --region us-east-1 2>/dev/null
aws cloudformation wait stack-delete-complete --stack-name RtpOverlayStack --region us-east-1 2>/dev/null

echo "✓ All CloudFormation stacks deleted"
```

### Step-by-Step Cleanup

```bash
cd infrastructure

# 1. Delete GR Processing Stack
aws cloudformation delete-stack --stack-name GRProcessingStack --region us-east-1

# 2. Delete Invoice Processing Stack
aws cloudformation delete-stack --stack-name InvoiceProcessingStack --region us-east-1

# 3. Delete AgentCore Invoker Stack
aws cloudformation delete-stack --stack-name AgentCoreInvokerStack --region us-east-1

# 4. Delete Visa Stub Stack
aws cloudformation delete-stack --stack-name VisaStubStack --region us-east-1

# 5. Delete Lambda Stack
aws cloudformation delete-stack --stack-name RtpOverlayLambdaStack --region us-east-1

# 6. Delete Infrastructure Stack (VPC, RDS)
aws cloudformation delete-stack --stack-name RtpOverlayStack --region us-east-1
```

### Clean Up AgentCore Resources

```bash
cd infrastructure

# Activate Python virtual environment
source .venv/bin/activate

# Delete Payment Agent from AgentCore Runtime
cd lambda/payment-agents
python3 << 'EOF'
from bedrock_agentcore_starter_toolkit import Runtime
try:
    runtime = Runtime()
    runtime.delete()
    print("✓ Payment Agent deleted")
except Exception as e:
    print(f"⚠️  Payment Agent deletion: {e}")
EOF
cd ../..

# Delete AgentCore Gateway
python3 << 'EOF'
import boto3
bedrock = boto3.client('bedrock-agent', region_name='us-east-1')
try:
    agents = bedrock.list_agents()
    for agent in agents.get('agentSummaries', []):
        if agent['agentName'] == 'visa-b2b-payment-gateway':
            bedrock.delete_agent(agentId=agent['agentId'], skipResourceInUseCheck=True)
            print(f"✓ Deleted gateway: {agent['agentId']}")
except Exception as e:
    print(f"⚠️  Gateway deletion: {e}")
EOF
```

### Clean Up SSM Parameters

```bash
# Delete SSM parameters
aws ssm delete-parameter --name "/rtp-overlay/payment-agent-arn" --region us-east-1 2>/dev/null
aws ssm delete-parameter --name "/rtp-overlay/kms-key-arn" --region us-east-1 2>/dev/null
echo "✓ SSM parameters deleted"
```

### Clean Up KMS Key

```bash
# Get KMS key ID
KMS_KEY_ID=$(aws kms list-aliases \
    --query "Aliases[?AliasName=='alias/rtp-overlay-card-encryption'].TargetKeyId" \
    --output text \
    --region us-east-1)

if [ -n "$KMS_KEY_ID" ]; then
    # Schedule key deletion (minimum 7 days)
    aws kms schedule-key-deletion \
        --key-id $KMS_KEY_ID \
        --pending-window-in-days 7 \
        --region us-east-1
    echo "✓ KMS key scheduled for deletion in 7 days"
else
    echo "⚠️  KMS key not found"
fi
```

### Clean Up Local Files

```bash
cd infrastructure

# Remove generated files
rm -f lambda-code.zip
rm -f lambda-layer.zip
rm -f .agentcore-gateway-config.json
rm -f .payment-agent-config.json
rm -f lambda/payment-agents/.bedrock_agentcore.yaml

# Remove CDK output
rm -rf cdk.out

echo "✓ Local files cleaned up"
```

### Verify Cleanup

```bash
# Check remaining stacks
aws cloudformation list-stacks \
    --region us-east-1 \
    --query 'StackSummaries[?StackStatus!=`DELETE_COMPLETE`].[StackName,StackStatus]' \
    --output table

# Check S3 buckets (should be empty)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 ls | grep rtp-overlay

# Check AgentCore agents
aws bedrock-agent list-agents --region us-east-1 --query 'agentSummaries[?contains(agentName, `visa`)]'
```

### Cost Savings After Cleanup

After cleanup, you will stop incurring costs for:
- RDS database (~$15/month)
- NAT Gateway (~$32/month if deployed)
- Lambda executions
- S3 storage
- API Gateway requests
- Bedrock model invocations

**Total savings:** ~$50-70/month

---

## Troubleshooting

**Issue:** Stack already exists  
**Fix:** Delete and redeploy: `aws cloudformation delete-stack --stack-name <StackName>`

**Issue:** API URL not found  
**Fix:** Deploy Lambda stack first: `./deploy-lambda.sh`

**Issue:** Docker not running  
**Fix:** Start Docker Desktop, then run `./rebuild-lambda-layer.sh`

**Issue:** Stack deletion fails  
**Fix:** Check if resources are in use, wait a few minutes, then retry deletion

**Issue:** S3 bucket deletion fails  
**Fix:** Empty the bucket first: `aws s3 rm s3://bucket-name --recursive`


