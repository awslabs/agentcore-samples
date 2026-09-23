# RTP Overlay Infrastructure

AWS CDK infrastructure for the RTP Overlay System with Lambda deployment.

## Architecture Overview

This project uses **three CDK stacks**:

1. **RtpOverlayStack** - Core infrastructure (VPC, RDS, S3)
2. **RtpOverlayLambdaStack** - API Lambda functions with layers
3. **RtpOverlayInvoiceProcessingStack** - Invoice processing with Bedrock AI

## What Gets Deployed

### Stack 1: Core Infrastructure (RtpOverlayStack)

**Networking:**
- VPC with 2 Availability Zones
- Public subnets (for Lambda internet access)
- Private subnets (for RDS)
- Database subnets (isolated)

**Database:**
- RDS PostgreSQL 15.4 (db.t3.micro)
- Secrets Manager for credentials
- Security groups (Lambda → RDS access)
- Automated backups (7-day retention)
- Encryption at rest

**Storage:**
- S3 bucket for receipt documents
- Versioning enabled
- Encryption at rest
- CORS configured for frontend
- Lifecycle policy (90-day retention)

### Stack 2: Lambda API (RtpOverlayLambdaStack)

**Lambda Functions:**
- API Lambda (handles all API requests)
- Migration Lambda (runs database migrations)

**Lambda Layer:**
- Dependencies layer (~37MB)
- Includes: express, typeorm, pg, bcrypt, etc.
- Built with Docker for Linux compatibility

**API Gateway:**
- REST API with /prod stage
- Lambda proxy integration
- CORS enabled

### Stack 3: Invoice Processing (RtpOverlayInvoiceProcessingStack)

**S3 Buckets:**
- Input bucket (invoice PDFs)
- Output bucket (ISO 20022 XML files)

**Lambda Function:**
- Python runtime with Bedrock SDK
- Triggered by S3 uploads
- Extracts invoice data with AI
- Generates ISO 20022 XML
- Calls API to create invoice records

**Secrets:**
- API key for Lambda → API authentication

## Prerequisites

- AWS CLI configured with valid credentials
- Node.js 18+ installed
- AWS CDK CLI: `npm install -g aws-cdk`
- Docker installed and running (for Lambda layer)

## Quick Deployment

```bash
# 1. Deploy infrastructure
cd infrastructure
./deploy.sh

# 2. Build Lambda layer (one-time)
./rebuild-lambda-layer.sh

# 3. Deploy Lambda functions
./deploy-lambda.sh

# 4. Run migrations
./run-migrations.sh

# 5. Deploy invoice processing (optional)
export RTP_API_URL="<API_URL_FROM_STEP_3>"
./deploy-invoice-processing.sh
```

See `../DEPLOYMENT-GUIDE.md` for complete step-by-step instructions.

## Deployment Scripts

### `deploy.sh`
Deploys core infrastructure stack (VPC, RDS, S3).
- **Time:** ~10-15 minutes
- **Run:** Once initially, then only when infrastructure changes

### `rebuild-lambda-layer.sh`
Builds Lambda layer with dependencies using Docker.
- **Time:** ~2-3 minutes
- **Run:** When backend dependencies change in `package.json`

### `deploy-lambda.sh`
Deploys Lambda functions and API Gateway.
- **Time:** ~5-10 minutes (first time), ~30-40 seconds (updates)
- **Run:** After every backend code change

### `run-migrations.sh`
Runs database migrations via Migration Lambda.
- **Time:** ~30 seconds
- **Run:** After deploying new migrations

### `deploy-invoice-processing.sh`
Deploys invoice processing stack (S3 + Lambda).
- **Time:** ~3-5 minutes
- **Run:** Once for invoice processing feature

### `get-db-info.sh`
Retrieves database connection info from Secrets Manager.
- **Use:** For local development or debugging

## Stack Outputs

### RtpOverlayStack
- `VpcId` - VPC ID for Lambda deployment
- `DatabaseEndpoint` - RDS endpoint
- `DatabasePort` - 5432
- `DatabaseSecretArn` - Secrets Manager ARN
- `ReceiptsBucketName` - S3 bucket name
- `ReceiptsBucketArn` - S3 bucket ARN

### RtpOverlayLambdaStack
- `ApiUrl` - API Gateway URL (use this for frontend)
- `ApiLambdaName` - API Lambda function name
- `MigrationLambdaName` - Migration Lambda function name

### RtpOverlayInvoiceProcessingStack
- `InputBucketName` - Invoice upload bucket
- `OutputBucketName` - ISO 20022 XML bucket
- `ProcessorLambdaName` - Invoice processor function name

## Accessing Database Credentials

```bash
# Get credentials from Secrets Manager
aws secretsmanager get-secret-value \
  --secret-id rtp-overlay-db-credentials \
  --query SecretString \
  --output text | jq .

# Or use the helper script
./get-db-info.sh
```

## Lambda Layer Architecture

**Why use layers?**
- Lambda has 250MB unzipped size limit
- Dependencies (~37MB) + code (~100KB) = under limit
- Faster deployments (layer cached, only code changes)
- Native modules (bcrypt, pg-native) need Linux compilation

**Layer contents:**
```
nodejs/
└── node_modules/
    ├── express/
    ├── typeorm/
    ├── pg/
    ├── bcrypt/
    └── ... (all production dependencies)
```

**Code package:**
```
dist/           # Compiled TypeScript
lambda.ts       # Lambda handler
package.json    # Metadata
```

## Cost Estimate

Monthly costs (approximate):

- **RDS db.t3.micro:** ~$15/month
- **Lambda:** Free tier covers most dev usage
- **API Gateway:** ~$3.50 per million requests
- **S3:** ~$0.023 per GB
- **Data Transfer:** Variable

**Total: ~$20-30/month for development**

(No NAT Gateway needed - Lambda has direct internet access)

## Useful Commands

```bash
# View all stacks
cd infrastructure
npm run build
cdk list

# See what will change
cdk diff RtpOverlayStack
cdk diff RtpOverlayLambdaStack

# Deploy specific stack
cdk deploy RtpOverlayStack
cdk deploy RtpOverlayLambdaStack

# Get stack outputs
aws cloudformation describe-stacks \
  --stack-name RtpOverlayLambdaStack \
  --query 'Stacks[0].Outputs'

# View Lambda logs
aws logs tail /aws/lambda/RtpOverlayLambdaStack-ApiLambda --follow

# Invoke migration Lambda manually
aws lambda invoke \
  --function-name RtpOverlayLambdaStack-MigrationLambda \
  --log-type Tail \
  response.json
```

## Troubleshooting

### Lambda Package Too Large
```bash
# Verify layer and code sizes
ls -lh ../lambda-*.zip

# Should see:
# lambda-layer.zip  ~37M
# lambda-code.zip   ~79-107K

# If code is large, rebuild layer
./rebuild-lambda-layer.sh
```

### Docker Not Running
```bash
# Start Docker Desktop (macOS/Windows)
# Or start Docker service (Linux)
sudo systemctl start docker

# Verify
docker ps

# Then rebuild layer
./rebuild-lambda-layer.sh
```

### Database Connection Issues
```bash
# Check Lambda can reach RDS
# Verify security groups allow Lambda → RDS on port 5432

# Check Lambda is in VPC
aws lambda get-function-configuration \
  --function-name RtpOverlayLambdaStack-ApiLambda \
  --query 'VpcConfig'
```

### API Gateway 404 Errors
```bash
# Verify API Gateway deployment
aws apigateway get-deployments \
  --rest-api-id <API_ID>

# Check Lambda integration
aws apigateway get-integration \
  --rest-api-id <API_ID> \
  --resource-id <RESOURCE_ID> \
  --http-method GET
```

## Cleanup

To delete all resources:

```bash
# Delete stacks in reverse order
aws cloudformation delete-stack --stack-name RtpOverlayInvoiceProcessingStack
aws cloudformation delete-stack --stack-name RtpOverlayLambdaStack
aws cloudformation delete-stack --stack-name RtpOverlayStack

# Clean up local files
rm -rf cdk.out
rm ../lambda-*.zip
rm -rf ../lambda-layer
```

**Note:** S3 buckets and RDS snapshots may be retained. Delete manually if needed.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                         VPC                              │
│                                                          │
│  ┌──────────────┐              ┌──────────────┐        │
│  │ Public Subnet│              │ Public Subnet│        │
│  │   (AZ-1)     │              │   (AZ-2)     │        │
│  │              │              │              │        │
│  │ Lambda ENI   │              │ Lambda ENI   │        │
│  └──────┬───────┘              └──────┬───────┘        │
│         │                             │                │
│  ┌──────▼───────┐              ┌──────▼───────┐        │
│  │  DB Subnet   │              │  DB Subnet   │        │
│  │   (AZ-1)     │              │   (AZ-2)     │        │
│  │              │              │              │        │
│  │ RDS Primary  │◄────────────►│ RDS Standby │        │
│  └──────────────┘              └──────────────┘        │
│                                                          │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────┐
        │      Lambda Functions          │
        │  ┌──────────────────────────┐  │
        │  │  API Lambda              │  │
        │  │  (with Layer)            │  │
        │  └──────────────────────────┘  │
        │  ┌──────────────────────────┐  │
        │  │  Migration Lambda        │  │
        │  └──────────────────────────┘  │
        │  ┌──────────────────────────┐  │
        │  │  Invoice Processor       │  │
        │  │  (Python + Bedrock)      │  │
        │  └──────────────────────────┘  │
        └────────────────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────┐
        │      API Gateway               │
        │  https://xxx.execute-api...    │
        └────────────────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────┐
        │      S3 Buckets                │
        │  ┌──────────────────────────┐  │
        │  │  Receipts Bucket         │  │
        │  └──────────────────────────┘  │
        │  ┌──────────────────────────┐  │
        │  │  Invoice Input Bucket    │  │
        │  └──────────────────────────┘  │
        │  ┌──────────────────────────┐  │
        │  │  Invoice Output Bucket   │  │
        │  └──────────────────────────┘  │
        └────────────────────────────────┘
```

## Security

- Database in private subnet (no internet access)
- Lambda functions in VPC with security groups
- Database credentials in Secrets Manager
- S3 buckets block public access
- All data encrypted at rest
- API Gateway with CORS configured
- Invoice processor uses API key authentication

## Next Steps

After infrastructure deployment:

1. Get API URL from Lambda stack outputs
2. Configure frontend `.env` with API URL
3. Test API endpoints
4. Upload invoice PDFs to test processing
5. Monitor CloudWatch logs for issues

For complete deployment instructions, see `../DEPLOYMENT-GUIDE.md`.
