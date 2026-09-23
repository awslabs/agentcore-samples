# RTP Overlay - Complete Deployment Guide

## Overview

Complete deployment guide for the RTP Overlay System to AWS. This guide covers infrastructure, backend API, invoice processing, GR processing, and frontend deployment.

**Total Deployment Time:** ~45-60 minutes

## Prerequisites

- AWS CLI configured with valid credentials
- Node.js 18+ installed
- **CDK CLI 2.102.0+** installed: `npm install -g aws-cdk@latest` (verify: `cdk --version`)
- Docker installed and running (required for Lambda layer)
- **Python 3.8+** installed
- AWS account with appropriate permissions

**Note:** If you see "Cloud assembly schema version mismatch" error, upgrade CDK CLI: `npm install -g aws-cdk@latest`

## Architecture Overview

### Visual Architecture

**Reference Architecture** - Complete system architecture:
- AWS Cloud infrastructure (VPC, Lambda, Bedrock, Database)
- External integrations (Visa B2B, Finance ERP, Supplier Portal)
- Multi-agent components (Supervisor, IDP, Match, Payment agents)
- Data flow and system boundaries

![System Components](visa-b2b-spec/Agent_RA_B2B.drawio.png)

**Sequence Diagram** - Complete Procure-to-Pay AP workflow:
- Supplier onboarding and PO creation
- Goods receipt and invoice processing
- PO-GR-Invoice matching with fuzzy logic
- Exception management and approval workflows
- Payment creation and authorization
- Payment execution via Visa B2B virtual cards
- Bank statement reconciliation and reporting

![Procure-to-Pay Workflow](visa-b2b-spec/Agent_sequence_Diagram_B2B_B2C-B2B%20Sequence.drawio.png)

### Stack Components

```
Infrastructure Stack (RtpOverlayStack)
├── VPC with public/private subnets
├── RDS PostgreSQL database
├── S3 bucket for receipts
└── Secrets Manager for credentials

Lambda Stack (RtpOverlayLambdaStack)
├── Lambda Layer (37MB - node_modules)
├── API Lambda (application code)
├── Migration Lambda
└── API Gateway REST API

Invoice Processing Stack (InvoiceProcessingStack)
├── Invoice Processing Lambda (Python + Bedrock)
├── Invoice input bucket
├── ISO20022 output bucket
├── IAM roles and permissions
└── API Key Secret

GR Processing Stack (GRProcessingStack)
├── GR Processing Lambda (Python + Bedrock)
├── GR input bucket
└── IAM roles and permissions

Visa B2B Integration (NEW)
├── AgentCore Gateway (Bedrock Agent with Visa B2B tools)
├── Visa Stub APIs (3 Lambda functions)
│   ├── getPaymentDetails
│   ├── processPayments
│   └── virtualCardRequisition
├── Payment Agent Lambda (Python + Bedrock)
├── Supervisor Agent Lambda (Python + Bedrock)
└── Card Requisition Agent Lambda (Python + Bedrock)
```

## Quick Start

For experienced users who want to deploy everything at once:

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

**Total Time:** ~40-50 minutes

## Step-by-Step Deployment

### Step 0: Setup Python Environment (One-Time)

```bash
cd infrastructure
./setup-python-env.sh
```

**Wait time:** ~30 seconds

**What this does:**
- Creates Python virtual environment in `infrastructure/.venv`
- Installs boto3 and bedrock-agentcore-starter-toolkit
- Isolates Python dependencies from system packages

**Note:** This only needs to be run once. Deployment scripts will automatically use this environment.

### Step 1: Deploy Infrastructure (VPC, RDS)

```bash
npm install
npm run build
./deploy.sh
```

**Wait time:** ~15 minutes

**What this creates:**
- VPC with 2 availability zones
- RDS PostgreSQL database (t3.micro)
- Base S3 bucket for receipts
- Secrets Manager for database credentials
- Security groups for Lambda and RDS

**Outputs:**
- VPC ID
- Database endpoint
- S3 bucket name
- Database secret ARN

### Step 2: Build Lambda Layer

```bash
./rebuild-lambda-layer.sh
```

**Wait time:** ~3 minutes

**What this does:**
- Uses Docker to install dependencies for Linux x86_64
- Creates `lambda-layer.zip` (~37MB)
- Required for native modules (bcrypt, pg-native)

**Why Docker?**
AWS Lambda runs on Linux. Native npm packages must be compiled for the correct platform.

### Step 3: Deploy API Lambda

```bash
./deploy-lambda.sh
```

**Wait time:** ~5-10 minutes (first time), ~30-40 seconds (updates)

**What this does:**
- Builds backend TypeScript code
- Creates lambda-code.zip automatically
- Uploads Lambda Layer
- Deploys API Lambda and Migration Lambda
- Sets up API Gateway
- Returns API Gateway URL

**Save the API URL** - you'll need it for frontend configuration.

### Step 4: Run Database Migrations

```bash
./run-migrations.sh
```

**Wait time:** ~30 seconds

**What this does:**
- Creates all database tables
- Seeds test users (admin, purchasing1, receiving1, treasury1)
- Seeds test vendors and purchase orders

### Step 5: Setup KMS Encryption

```bash
./setup-kms-encryption.sh
```

**Wait time:** ~1-2 minutes

**What this creates:**
- AWS KMS key for encrypting sensitive payment card data
- Key alias: `alias/rtp-overlay-card-encryption`
- SSM Parameter: `/rtp-overlay/kms-key-arn` (stores key ARN)
- IAM permissions for Lambda to use the key

**Why this is needed:**
Virtual card numbers from Visa B2B must be encrypted at rest in the database. This KMS key provides secure encryption/decryption for PCI compliance.

**Note:** Safe to run multiple times (idempotent).

### Step 6: Deploy Visa B2B Stub APIs

```bash
./deploy-visa-stub.sh
```

**Wait time:** ~3-5 minutes

**What this creates:**
- 3 Lambda functions mocking Visa B2B APIs:
  - VirtualCardRequisition
  - ProcessPayments
  - GetPaymentDetails
- API Gateway for stub APIs

### Step 7: Deploy AgentCore Gateway

```bash
./deploy-agentcore-gateway.sh
```

**Wait time:** ~1-2 minutes

**What this does:**
- Auto-generates OpenAPI spec from stub API URL
- Creates S3 bucket for specs
- Creates IAM role with required permissions
- Configures gateway with 3 MCP tools
- Saves gateway info to SSM Parameter Store
- Saves config to `.agentcore-gateway-config.json`

**MCP Tools Generated:**
- `VirtualCardRequisition` - Create virtual payment card
- `ProcessPayments` - Process payment transaction
- `GetPaymentDetails` - Get payment status

### Step 8: Deploy Invoice Processing

```bash
./deploy-invoice-processing.sh
```

**Wait time:** ~3-5 minutes

**What this creates:**
- Invoice processing Lambda (Python + Bedrock)
- S3 input bucket for invoice uploads
- S3 output bucket for ISO20022 XML files
- IAM role with Bedrock permissions
- API key secret for Lambda → API authentication

**⚠️ IMPORTANT:** Must be deployed BEFORE AgentCore Invoker (Step 9 needs ISO20022 Lambda ARN).

**Note:** Script automatically fetches the API URL from Step 3.

### Step 9: Deploy AgentCore Invoker

```bash
./deploy-agentcore-invoker.sh
```

**Wait time:** ~3-5 minutes

**What this creates:**
- Python Lambda function that bridges TypeScript backend with Bedrock AgentCore SDK
- Invokes Payment Agent Runtime and returns results to Backend Lambda
- No VPC configuration (only calls AWS APIs)
- CloudFormation export for Lambda function name

**Why this is needed:**
The Backend Lambda is TypeScript/Node.js, but the Bedrock AgentCore SDK is Python-only. This Lambda acts as a bridge, receiving payment requests from the Backend Lambda and invoking the AgentCore Runtime.

**Post-Deployment:**
The script automatically redeploys the Backend Lambda to import the AgentCore Invoker function name.

### Step 10: Deploy Payment Agent

```bash
./deploy-payment-agent.sh
```

**Wait time:** ~5-10 minutes

**What this does:**
- Fixes CodeBuild IAM permissions for ECR push
- Retrieves gateway info from SSM Parameter Store
- Retrieves environment variables from CloudFormation
- Deploys AI agent to AgentCore Runtime
- Waits for READY status
- Saves agent ARN to SSM Parameter Store

**Prerequisites:**
- Gateway must be deployed (Step 7) to save gateway info to SSM
- Invoice Processing must be deployed (Step 8)
- AgentCore Invoker must be deployed (Step 9)

**What this does:**
- Fixes CodeBuild IAM permissions for ECR push
- Retrieves gateway info from SSM Parameter Store
- Retrieves environment variables from CloudFormation
- Deploys AI agent to AgentCore Runtime
- Waits for READY status
- Saves agent ARN to SSM Parameter Store
- **Automatically updates Backend Lambda with Payment Agent ARN**
- **Automatically redeploys AgentCore Invoker with Payment Agent ARN**

**Post-Deployment:**
The script automatically handles all redeployments needed for integration.

### Step 11: Deploy GR Processing

```bash
./deploy-gr-processing.sh
```

**Wait time:** ~3-5 minutes

**What this creates:**
- GR processing Lambda (Python + Bedrock)
- S3 input bucket for GR uploads
- IAM role with Bedrock permissions

### Step 12: Configure S3 Notifications

```bash
./configure-s3-notifications.sh
```

**Wait time:** ~1 minute

**What this does:**
- Configures S3 event notifications for Invoice and GR processing
- Adds Lambda permissions for S3 to invoke functions
- Sets up triggers for file uploads

**Triggers configured:**
- Invoice Processing: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.json`, `.csv`
- GR Processing: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`

### Step 13: Get API URL

```bash
aws cloudformation describe-stacks \
  --stack-name RtpOverlayLambdaStack \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text
```

Save this URL for frontend configuration.

### Step 14: Test the API

```bash
API_URL="<your-api-url>"

# Health check
curl ${API_URL}/health

# Login test
curl -X POST ${API_URL}/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

### Step 15: Configure Frontend

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

This creates `rtp-overlay-deployment.zip`. To deploy:

1. Go to [AWS Amplify Console](https://console.aws.amazon.com/amplify/)
2. Select your app (or create new)
3. Click "Deploy updates"
4. Upload `rtp-overlay-deployment.zip`

**Note:** Make sure to set the `VITE_API_BASE_URL` environment variable in Amplify with your API Gateway URL.

## Using the Two-Portal System

### Buyer Portal (Internal AP Users)

**Purpose:** Main interface for Accounts Payable users to manage invoices and payments

**Access:** <http://localhost:5173> (dev) or `https://your-domain.com` (prod)

**Key Features:**
- Upload and manage invoices
- Process payments with AI-powered decision making
- View virtual card details (masked by default)
- Click "Show" to reveal full card number
- Click "Copy" to copy card details
- Track payment status
- Full invoice management capabilities

**Payment Processing:**
1. Navigate to Invoices
2. Click on an invoice
3. Click "Process Payment" button
4. AI agent decides: Visa B2B or ISO20022
5. Virtual card displayed (if Visa B2B)
6. Copy Payment ID for supplier

### Supplier Portal (External Suppliers)

**Purpose:** Simple interface for suppliers to retrieve virtual card details

**Access:** <http://localhost:5173/supplier/login> (dev) or `https://your-domain.com/supplier/login` (prod)

**Key Features:**
- Simple login with Payment ID or Tracking Number
- View invoice details (invoice number, amount, due date)
- View virtual card details (unmasked)
- Copy card details for payment processing
- View payment status
- Clean, minimal design

**Supplier Flow:**
1. Receive Payment ID or Tracking Number from buyer
2. Navigate to supplier portal
3. Enter Payment ID or Tracking Number
4. View and copy virtual card details
5. Process payment in your own system

### End-to-End Demo Flow

**Complete payment journey:**

1. **Buyer processes payment:**
   - Login to buyer portal: `admin` / `admin123`
   - Navigate to Invoices
   - Click on an invoice
   - Click "Process Payment"
   - View virtual card (masked)
   - Copy Payment ID: `PAY-123456`

2. **Supplier retrieves card:**
   - Navigate to supplier portal
   - Enter Payment ID: `PAY-123456`
   - View unmasked card details
   - Copy card information
   - Process payment manually in their system

3. **Status tracking:**
   - Both portals show payment status
   - Updates automatically via Visa B2B API

## Deployment Verification

After deployment, verify each component:

### Infrastructure Stack
```bash
aws cloudformation describe-stacks \
  --stack-name RtpOverlayStack \
  --region us-east-1 \
  --query 'Stacks[0].StackStatus'
# Expected: CREATE_COMPLETE
```

### Lambda Stack
```bash
aws cloudformation describe-stacks \
  --stack-name RtpOverlayLambdaStack \
  --region us-east-1 \
  --query 'Stacks[0].StackStatus'
# Expected: CREATE_COMPLETE
```

### Invoice Processing Stack
```bash
aws cloudformation describe-stacks \
  --stack-name InvoiceProcessingStack \
  --region us-east-1 \
  --query 'Stacks[0].StackStatus'
# Expected: CREATE_COMPLETE
```

### GR Processing Stack
```bash
aws cloudformation describe-stacks \
  --stack-name GRProcessingStack \
  --region us-east-1 \
  --query 'Stacks[0].StackStatus'
# Expected: CREATE_COMPLETE
```

### S3 Notifications
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Check invoice bucket notifications
aws s3api get-bucket-notification-configuration \
  --bucket rtp-overlay-invoices-input-${ACCOUNT_ID} \
  --region us-east-1

# Check GR bucket notifications
aws s3api get-bucket-notification-configuration \
  --bucket rtp-overlay-receipts-input-${ACCOUNT_ID} \
  --region us-east-1
```

## Testing the System

### Test Invoice Upload

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Upload a test invoice
aws s3 cp test-invoice.pdf \
  s3://rtp-overlay-invoices-input-${ACCOUNT_ID}/ \
  --region us-east-1

# Check Lambda logs
aws logs tail /aws/lambda/rtp-overlay-invoice-processor \
  --follow \
  --region us-east-1
```

### Test GR Upload

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Upload a test receipt
aws s3 cp test-receipt.jpg \
  s3://rtp-overlay-receipts-input-${ACCOUNT_ID}/ \
  --region us-east-1

# Check Lambda logs
aws logs tail /aws/lambda/rtp-overlay-gr-processor \
  --follow \
  --region us-east-1
```

### Test Visa B2B Integration

```bash
# Test AgentCore Gateway tools
cd infrastructure
python3 scripts/test-gateway-tools.py

# Test Visa stub APIs directly
STUB_API_URL=$(aws cloudformation describe-stacks \
  --stack-name VisaStubStack \
  --query 'Stacks[0].Outputs[?OutputKey==`VisaStubApiUrl`].OutputValue' \
  --output text)

# Test VirtualCardRequisition
curl -X POST ${STUB_API_URL}vpa/v1/accountManagement/VirtualCardRequisition \
  -H "Content-Type: application/json" \
  -d '{"messageId":"test-123","buyerId":9999,"accountNumber":4111111111111111}'

# Test ProcessPayments
curl -X POST ${STUB_API_URL}vpa/v1/payment/ProcessPayments \
  -H "Content-Type: application/json" \
  -d '{"messageId":"test-123","payment":{"accountNumber":4111111111111111}}'

# Test GetPaymentDetails
curl -X POST ${STUB_API_URL}vpa/v1/payment/GetPaymentDetails \
  -H "Content-Type: application/json" \
  -d '{"messageId":"test-123","buyerId":9999,"trackingNumber":9999999958}'

# Check gateway logs
aws logs tail /aws/lambda/visa-stub-virtualCardRequisition \
  --follow \
  --region us-east-1
```

## Visa B2B Integration Architecture

The Visa B2B payment integration uses a multi-agent architecture powered by AWS Bedrock.

**See the interaction flow:** [B2B Payment Sequence](visa-b2b-spec/Agent_sequence_Diagram_B2B.drawio.png)

### Components

1. **AgentCore Gateway** - Converts Visa B2B REST APIs into MCP tools:
   - `VirtualCardRequisition` - Create virtual payment card
   - `ProcessPayments` - Process payment transaction
   - `GetPaymentDetails` - Get payment status

2. **Visa Stub APIs** - Mock Visa B2B APIs for testing (3 Lambda functions)

3. **Payment Agent** - AI-powered payment orchestration:
   - Uses Bedrock (Claude) to decide: Visa B2B or ISO20022
   - Calls Gateway MCP tools for virtual card payments
   - Generates ISO20022 files for bank transfers
   - Writes audit trail to database

### Data Flow

```
Invoice Approved → Payment Agent → Decision (AI)
                                      ↓
                    ┌─────────────────┴─────────────────┐
                    ↓                                   ↓
            Visa B2B Payment                    ISO20022 File
                    ↓                                   ↓
        AgentCore Gateway → Visa APIs          S3 Bucket → Bank
                    ↓                                   ↓
              Database (tracking)                 Database (tracking)
```

### Database Schema

The `payments` table tracks all payment operations:
- Payment ID, amount, currency, method (visa_b2b or iso20022)
- Status (pending, processing, completed, failed)
- Visa transaction references or ISO20022 file paths
- Agent reasoning and timestamps

## Default Users

After running migrations, these users are available:

| Username | Password | Role |
|----------|----------|------|
| admin | admin123 | Administrator |
| purchasing1 | password123 | Purchasing Personnel |
| receiving1 | password123 | Receiving Personnel |
| treasury1 | password123 | Treasury Manager |

## Key Configuration Files

- `infrastructure/lib/infrastructure-stack.ts` - VPC, RDS, S3
- `infrastructure/lib/lambda-stack.ts` - API Lambda functions
- `infrastructure/lib/invoice-processing-stack.ts` - Invoice processing
- `infrastructure/lib/gr-processing-stack.ts` - GR processing
- `infrastructure/deploy.sh` - Infrastructure deployment
- `infrastructure/deploy-lambda.sh` - Lambda deployment
- `infrastructure/deploy-visa-stub.sh` - Visa stub APIs deployment
- `infrastructure/deploy-agentcore-gateway.sh` - AgentCore Gateway deployment
- `infrastructure/deploy-payment-agent.sh` - Payment Agent deployment
- `infrastructure/scripts/setup-agentcore-gateway.py` - Gateway setup script
- `infrastructure/scripts/test-gateway-tools.py` - Gateway testing script
- `infrastructure/configure-s3-notifications.sh` - S3 notification setup
- `visa-b2b-spec/gateway/visa-b2b-stub-openapi.json` - Visa API specification

## Troubleshooting

### Stack Already Exists

```bash
# Delete and redeploy
aws cloudformation delete-stack \
  --stack-name <StackName> \
  --region us-east-1

# Wait for deletion
aws cloudformation wait stack-delete-complete \
  --stack-name <StackName> \
  --region us-east-1

# Redeploy
./deploy-<stack>.sh
```

### Docker Not Running

```bash
# Start Docker Desktop (macOS/Windows)
# Or start Docker service (Linux)
sudo systemctl start docker

# Verify
docker ps

# Rebuild layer
./rebuild-lambda-layer.sh
```

### API URL Not Found

```bash
# Ensure Lambda stack is deployed
./deploy-lambda.sh

# Get URL manually
aws cloudformation describe-stacks \
  --stack-name RtpOverlayLambdaStack \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text
```

### Lambda Can't Connect to RDS

Check security groups:
```bash
# Lambda security group should allow outbound to RDS port 5432
# RDS security group should allow inbound from Lambda security group
```

### S3 Notifications Not Working

```bash
# Reconfigure notifications
cd infrastructure
./configure-s3-notifications.sh

# Check Lambda permissions
aws lambda get-policy \
  --function-name rtp-overlay-invoice-processor \
  --region us-east-1
```

### Build Errors

```bash
# Clean and rebuild
cd infrastructure
rm -rf node_modules cdk.out
npm install
npm run build

# Retry deployment
./deploy.sh
```

### Visa B2B Payment 403 Forbidden Error

**Symptom:** Payment agent fails with "403 Client Error: Forbidden" when calling gateway

**Cause:** AgentCore Runtime role is missing gateway permissions

**Check if permissions exist:**
```bash
# Find your Runtime role
aws iam list-roles --query "Roles[?starts_with(RoleName, 'AmazonBedrockAgentCoreSDKRuntime-us-east-1-')].RoleName" --output text

# Check for gateway permissions (replace ROLE_NAME with actual role)
aws iam get-role-policy --role-name ROLE_NAME --policy-name InvokeAgentCoreGateway
```

**Fix:** This is now handled automatically by the Payment Agent deployment script. If you still encounter this issue, re-run the payment agent deployment:
```bash
cd infrastructure
./deploy-payment-agent.sh
```

**What this does:**
- The Payment Agent script automatically redeploys the Gateway at the end
- This ensures the Runtime role (created during Payment Agent deployment) gets the required permissions
- No manual intervention needed

**Why this happens:**
- Gateway was deployed before Payment Agent (Runtime role didn't exist yet)
- The automated redeployment in the Payment Agent script fixes this

**Prevention:** Follow the documented deployment order. The Payment Agent script now handles permission grants automatically.

## Monitoring

### CloudWatch Logs

```bash
# API Lambda logs
aws logs tail /aws/lambda/RtpOverlayLambdaStack-ApiLambda \
  --follow \
  --region us-east-1

# Invoice Processing logs
aws logs tail /aws/lambda/rtp-overlay-invoice-processor \
  --follow \
  --region us-east-1

# GR Processing logs
aws logs tail /aws/lambda/rtp-overlay-gr-processor \
  --follow \
  --region us-east-1
```

### Stack Status

```bash
# List all stacks
aws cloudformation list-stacks \
  --region us-east-1 \
  --query 'StackSummaries[?StackStatus!=`DELETE_COMPLETE`].[StackName,StackStatus]' \
  --output table
```

## Cost Estimate

- **RDS t3.micro:** ~$15/month
- **Lambda:** Free tier covers most usage
- **API Gateway:** ~$3.50 per million requests
- **S3:** ~$0.023 per GB
- **NAT Gateway:** ~$32/month (if using)

**Estimated Total:** $50-70/month for development

## Cleanup - Remove All Resources

To avoid accumulating AWS costs after testing, follow these steps to remove all deployed resources.

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

**Wait time:** ~10-15 minutes

### Step-by-Step Cleanup

If you prefer to delete resources one at a time:

```bash
cd infrastructure

# 1. Delete GR Processing Stack
aws cloudformation delete-stack --stack-name GRProcessingStack --region us-east-1
echo "Waiting for GR Processing Stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name GRProcessingStack --region us-east-1

# 2. Delete Invoice Processing Stack
aws cloudformation delete-stack --stack-name InvoiceProcessingStack --region us-east-1
echo "Waiting for Invoice Processing Stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name InvoiceProcessingStack --region us-east-1

# 3. Delete AgentCore Invoker Stack
aws cloudformation delete-stack --stack-name AgentCoreInvokerStack --region us-east-1
echo "Waiting for AgentCore Invoker Stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name AgentCoreInvokerStack --region us-east-1

# 4. Delete Visa Stub Stack
aws cloudformation delete-stack --stack-name VisaStubStack --region us-east-1
echo "Waiting for Visa Stub Stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name VisaStubStack --region us-east-1

# 5. Delete Lambda Stack
aws cloudformation delete-stack --stack-name RtpOverlayLambdaStack --region us-east-1
echo "Waiting for Lambda Stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name RtpOverlayLambdaStack --region us-east-1

# 6. Delete Infrastructure Stack (VPC, RDS)
aws cloudformation delete-stack --stack-name RtpOverlayStack --region us-east-1
echo "Waiting for Infrastructure Stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name RtpOverlayStack --region us-east-1

echo "✓ All CloudFormation stacks deleted"
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

echo "✓ AgentCore resources cleaned up"
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

**Note:** KMS keys cannot be deleted immediately. AWS requires a minimum 7-day waiting period for security reasons.

### Clean Up S3 Buckets (If Needed)

If bucket deletion fails due to non-empty buckets:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Empty invoice bucket
aws s3 rm s3://rtp-overlay-invoices-input-${ACCOUNT_ID} --recursive --region us-east-1

# Empty ISO20022 bucket
aws s3 rm s3://rtp-overlay-iso20022-${ACCOUNT_ID} --recursive --region us-east-1

# Empty receipts bucket
aws s3 rm s3://rtp-overlay-receipts-input-${ACCOUNT_ID} --recursive --region us-east-1

# Empty gateway specs bucket
aws s3 rm s3://agentcore-gateway-specs-${ACCOUNT_ID} --recursive --region us-east-1

echo "✓ S3 buckets emptied"

# Now retry processing stack deletion if needed
aws cloudformation delete-stack --stack-name InvoiceProcessingStack --region us-east-1
aws cloudformation delete-stack --stack-name GRProcessingStack --region us-east-1
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

# Remove node_modules (optional)
rm -rf node_modules

echo "✓ Local files cleaned up"
```

### Verify Cleanup

After cleanup, verify all resources are removed:

```bash
# Check remaining CloudFormation stacks
echo "Checking CloudFormation stacks..."
aws cloudformation list-stacks \
    --region us-east-1 \
    --query 'StackSummaries[?StackStatus!=`DELETE_COMPLETE`].[StackName,StackStatus]' \
    --output table

# Check S3 buckets
echo "Checking S3 buckets..."
aws s3 ls | grep rtp-overlay

# Check AgentCore agents
echo "Checking AgentCore agents..."
aws bedrock-agent list-agents --region us-east-1 \
    --query 'agentSummaries[?contains(agentName, `visa`)]'

# Check SSM parameters
echo "Checking SSM parameters..."
aws ssm describe-parameters \
    --region us-east-1 \
    --query 'Parameters[?contains(Name, `rtp-overlay`)]'

# Check KMS keys
echo "Checking KMS keys..."
aws kms list-aliases \
    --region us-east-1 \
    --query 'Aliases[?contains(AliasName, `rtp-overlay`)]'
```

### Cost Savings After Cleanup

After cleanup, you will stop incurring costs for:

- **RDS database:** ~$15/month
- **NAT Gateway:** ~$32/month (if deployed)
- **Lambda executions:** Variable based on usage
- **S3 storage:** ~$0.023 per GB
- **API Gateway requests:** ~$3.50 per million requests
- **Bedrock model invocations:** Variable based on usage
- **KMS key:** ~$1/month (stops after deletion)

**Total savings:** ~$50-70/month for development environment

### Troubleshooting Cleanup

**Issue:** Stack deletion fails with "Resource in use"  
**Fix:** Wait a few minutes for dependent resources to finish deleting, then retry

**Issue:** S3 bucket deletion fails  
**Fix:** Empty the bucket first using the commands in "Clean Up S3 Buckets" section

**Issue:** RDS deletion takes a long time  
**Fix:** This is normal. RDS deletion can take 10-15 minutes due to final snapshot creation

**Issue:** KMS key still shows in console  
**Fix:** KMS keys remain visible for 7-30 days (pending deletion period) before permanent deletion

**Issue:** Lambda functions still exist  
**Fix:** Check if Lambda stack deletion completed. If not, manually delete functions from AWS Console

## Next Steps

1. Change default admin password
2. Update CORS settings in `lambda-stack.ts` with your frontend domain
3. Configure custom domain for API Gateway
4. Set up CI/CD pipeline
5. Configure CloudWatch alarms
6. Set up RDS backup strategy
7. Review and adjust security groups
8. Configure Bedrock model access

## Support

For issues:
1. Check CloudWatch logs for errors
2. Review CloudFormation events
3. Verify security group rules
4. Check IAM permissions
5. See `DEPLOYMENT-QUICK-START.md` for quick reference

---

**Deployment Status:** ✅ Ready to deploy
**Last Updated:** November 2024
