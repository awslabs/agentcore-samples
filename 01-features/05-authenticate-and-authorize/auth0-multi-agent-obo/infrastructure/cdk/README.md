# AgentCore CDK Infrastructure - Template / Reference

This directory contains AWS CDK (Cloud Development Kit) infrastructure definitions for the AgentCore Financial Services platform.

## IMPORTANT NOTICE

```
***************************************************************************
* THIS IS A TEMPLATE/EXAMPLE SHOWING THE CDK RESOURCE STRUCTURE          *
* These files do NOT create any actual AWS resources                      *
*                                                                        *
* For actual deployment, use deploy_all.sh at the project root:          *
*   ./deploy_all.sh                                                      *
*                                                                        *
* This CDK stack is provided as a reference for teams wanting to adopt   *
* CDK-based deployment in their own environments.                        *
***************************************************************************
```

The code in this directory is intentionally non-functional and serves as:
- A reference for teams wanting to adopt CDK-based deployment
- Architecture documentation showing the intended resource structure
- Template for future CDK implementation
- Educational resource for understanding the system design

### Deployment

This sample uses shell-based deployment scripts at the project root rather than CDK:

| Script | Purpose |
|--------|---------|
| `./deploy_all.sh` | Deploy all agents (ECR push, runtime creation, readiness check) |
| `./cleanup_all.sh` | Tear down all deployed resources |
| `./debug.sh` | Read-only diagnostic commands for inspection |

## Directory Structure

```
cdk/
├── __init__.py                      # Package initialization
├── app.py                           # CDK app entry point (STUB)
├── agentcore_identity_stack.py      # Identity & Auth infrastructure (STUB)
├── agentcore_runtime_stack.py       # Agent runtime infrastructure (STUB)
├── agentcore_gateway_stack.py       # API Gateway infrastructure (STUB)
├── requirements.txt                 # Python dependencies
├── cdk.json                         # CDK configuration
└── README.md                        # This file
```

## Stack Overview

### 1. AgentCore Identity Stack (STUB)

**Purpose**: Manages authentication and authorization infrastructure

**Would create**:
- JWT Authorizer for API Gateway integrated with Auth0
- IAM roles for workload identities (one per agent)
- IAM policies for agent permissions
- Secrets Manager entries for Auth0 credentials
- Optional: Cognito User Pool (Auth0 alternative)

**Key Resources** (when implemented):
```python
# JWT Authorizer
jwt_authorizer = CfnAuthorizer(
    authorizer_type="JWT",
    identity_source=["$request.header.Authorization"],
    jwt_configuration={
        "audience": ["https://api.agentcore.example.com"],
        "issuer": "https://your-tenant.us.auth0.com/",
    }
)

# Agent IAM Roles
coordinator_role = Role(
    role_name="agentcore-coordinator-dev",
    assumed_by=ServicePrincipal("lambda.amazonaws.com")
)
```

### 2. AgentCore Runtime Stack (STUB)

**Purpose**: Manages execution environment for agents

**Would create**:
- Lambda functions for each agent:
  - Coordinator Agent
  - Customer Profile Agent
  - Accounts Agent
  - Transactions Agent
  - Cards Agent
- DynamoDB tables for:
  - Agent conversation state
  - Customer profiles
  - Accounts, transactions, cards
  - Agent metrics
- S3 buckets for:
  - Agent logs
  - Customer documents
  - Agent artifacts
- EventBridge event bus for agent orchestration
- SQS queues for async communication
- CloudWatch Log Groups for logging
- Optional: ECS Fargate services for long-running agents

**Key Resources** (when implemented):
```python
# Lambda Function
coordinator = Function(
    function_name="agentcore-coordinator-dev",
    runtime=Runtime.PYTHON_3_12,
    handler="coordinator.handler",
    code=Code.from_asset("../../agents/coordinator"),
    memory_size=1024,
    timeout=Duration.seconds(30)
)

# DynamoDB Table
agent_state_table = Table(
    table_name="agentcore-agent_state-dev",
    partition_key=Attribute(name="session_id", type=AttributeType.STRING),
    sort_key=Attribute(name="timestamp", type=AttributeType.STRING),
    billing_mode=BillingMode.PAY_PER_REQUEST
)
```

### 3. AgentCore Gateway Stack (STUB)

**Purpose**: Manages API Gateway configurations

**Would create**:
- HTTP API Gateway for public client access
- REST API Gateway for private agent-to-agent communication
- VPC Link for private connectivity to ECS services
- WAF Web ACL with security rules:
  - Rate limiting
  - SQL injection protection
  - XSS protection
  - IP filtering
- CloudWatch Dashboards for monitoring
- Custom domain name and Route 53 DNS records

**Key Resources** (when implemented):
```python
# HTTP API
public_api = HttpApi(
    api_name="agentcore-public-api-dev",
    cors_preflight={
        "allow_origins": ["https://app.agentcore.example.com"],
        "allow_methods": [CorsHttpMethod.GET, CorsHttpMethod.POST],
        "allow_headers": ["Authorization", "Content-Type"]
    },
    default_authorizer=auth0_authorizer
)

# WAF Web ACL
waf_acl = CfnWebACL(
    scope="REGIONAL",
    rules=[
        {"name": "RateLimit", "limit": 2000},
        {"name": "AWSManagedRulesCommonRuleSet"}
    ]
)
```

## Prerequisites (for real implementation)

1. **AWS Account**: Active AWS account with appropriate permissions
2. **AWS CDK**: Install CDK CLI
   ```bash
   npm install -g aws-cdk
   ```
3. **Python 3.12+**: Python runtime
4. **AWS CLI**: Configured with credentials
   ```bash
   aws configure
   ```
5. **Docker**: For bundling Lambda functions (if using)

## Setup Instructions (for real implementation)

### 1. Install Dependencies

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install Python packages
pip install -r requirements.txt
```

### 2. Configure Environment

Create a `.env` file or set environment variables:

```bash
# AWS Configuration
export CDK_DEFAULT_ACCOUNT="123456789012"
export CDK_DEFAULT_REGION="us-east-1"

# Auth0 Configuration
export AUTH0_DOMAIN="your-tenant.us.auth0.com"
export AUTH0_AUDIENCE="https://api.agentcore.example.com"

# Environment
export ENVIRONMENT="dev"  # dev, staging, or prod
```

### 3. Bootstrap CDK (First Time Only)

```bash
cdk bootstrap aws://ACCOUNT-NUMBER/REGION
```

### 4. Synthesize CloudFormation Templates

```bash
# Synthesize all stacks
cdk synth

# Synthesize specific stack
cdk synth AgentCoreIdentityStack-dev
```

### 5. Deploy Stacks

```bash
# Deploy all stacks
cdk deploy --all

# Deploy specific stack
cdk deploy AgentCoreIdentityStack-dev

# Deploy with approval
cdk deploy --all --require-approval=never
```

### 6. Verify Deployment

```bash
# List deployed stacks
cdk list

# View stack outputs
aws cloudformation describe-stacks \
  --stack-name AgentCoreIdentityStack-dev \
  --query 'Stacks[0].Outputs'
```

## CDK Commands

```bash
# List all stacks
cdk list

# Synthesize CloudFormation template
cdk synth

# Compare deployed stack with current state
cdk diff

# Deploy stack
cdk deploy

# Destroy stack
cdk destroy

# View stack outputs
cdk outputs

# Generate constructs from CloudFormation template
cdk import
```

## Current Status: STUB MODE

When you run `cdk synth` with the current stub implementation:

```bash
python app.py
```

You'll see output like:
```
================================================================================
AgentCore CDK Application - STUB MODE
================================================================================
Environment: dev
Account: 123456789012
Region: us-east-1

NOTE: This is a STUB implementation that does NOT create actual resources.
Stack definitions exist as templates only.
================================================================================

[ STUB ] Identity Stack - NOT CREATED
  - Would create: JWT Authorizer, IAM roles, Identity providers

[ STUB ] Runtime Stack - NOT CREATED
  - Would create: Lambda functions, ECS services, DynamoDB tables

[ STUB ] Gateway Stack - NOT CREATED
  - Would create: API Gateway, VPC Link, WAF, CloudWatch

================================================================================
CDK App Initialization Complete (STUB MODE)
To implement: Uncomment stack definitions and provide implementations
================================================================================
```

## Converting Stubs to Real Implementation

To convert these stubs into working infrastructure:

### Step 1: Uncomment Stack Initializations

In `app.py`, uncomment the stack initialization code:

```python
# Uncomment these lines:
identity_stack = AgentCoreIdentityStack(
    app,
    f"AgentCoreIdentityStack-{environment}",
    env=env,
    environment=environment,
    auth0_domain=os.environ.get("AUTH0_DOMAIN"),
    auth0_audience=os.environ.get("AUTH0_AUDIENCE"),
)

runtime_stack = AgentCoreRuntimeStack(
    app,
    f"AgentCoreRuntimeStack-{environment}",
    env=env,
    environment=environment,
    identity_stack=identity_stack,
)

gateway_stack = AgentCoreGatewayStack(
    app,
    f"AgentCoreGatewayStack-{environment}",
    env=env,
    environment=environment,
    identity_stack=identity_stack,
    runtime_stack=runtime_stack,
)
```

### Step 2: Implement Stack Resources

In each stack file, uncomment the resource creation code marked with "When implemented:".

Example from `agentcore_identity_stack.py`:

```python
# Replace this:
print("  [STUB] Would create: JWT Authorizer for Auth0")

# With this:
from aws_cdk.aws_apigatewayv2 import CfnAuthorizer

self.jwt_authorizer = CfnAuthorizer(
    self,
    "Auth0JWTAuthorizer",
    api_id=api.api_id,
    authorizer_type="JWT",
    identity_source=["$request.header.Authorization"],
    name=f"auth0-jwt-authorizer-{environment}",
    jwt_configuration={
        "audience": [self.auth0_audience],
        "issuer": f"https://{self.auth0_domain}/",
    },
)
```

### Step 3: Add Agent Code

Ensure your agent Lambda code is available:

```
./
├── agents/
│   ├── coordinator/
│   │   ├── coordinator.py          # Lambda handler
│   │   ├── requirements.txt
│   │   └── tools/
│   ├── customer_profile/
│   │   ├── customer_profile.py
│   │   └── ...
│   └── ...
```

### Step 4: Configure Dependencies

Update Lambda code paths in stack definitions to point to your agent code.

### Step 5: Test and Deploy

```bash
# Validate synthesis
cdk synth

# Review changes
cdk diff

# Deploy to dev environment
cdk deploy --all --context environment=dev

# Deploy to prod environment
cdk deploy --all --context environment=prod
```

## Environment-Specific Deployments

Deploy to different environments by changing context:

```bash
# Development
cdk deploy --all --context environment=dev

# Staging
cdk deploy --all --context environment=staging

# Production
cdk deploy --all --context environment=prod --require-approval=always
```

## Cost Estimation

When implemented, estimated monthly costs (dev environment):

| Service | Usage | Est. Cost |
|---------|-------|-----------|
| Lambda | 1M invocations, 512MB | ~$5 |
| API Gateway | 1M requests | ~$3.50 |
| DynamoDB | On-demand, 1M reads/writes | ~$2.50 |
| S3 | 10GB storage, 1M requests | ~$0.50 |
| CloudWatch Logs | 10GB ingestion | ~$5 |
| EventBridge | 1M events | ~$1 |
| **Total** | | **~$17.50/month** |

Production costs will be higher based on actual usage.

## Security Best Practices

When implementing, follow these security practices:

1. **IAM Roles**: Use least privilege principle
2. **Encryption**: Enable encryption at rest for all data
3. **Secrets**: Store sensitive data in Secrets Manager
4. **VPC**: Deploy Lambda in VPC for private resources
5. **WAF**: Enable WAF with appropriate rules
6. **Logging**: Enable CloudTrail and CloudWatch Logs
7. **Monitoring**: Set up CloudWatch Alarms
8. **Scanning**: Enable GuardDuty and Security Hub

## Monitoring and Observability

When implemented, monitor using:

- **CloudWatch Dashboards**: Pre-built dashboards for each stack
- **CloudWatch Alarms**: Alert on errors, latency, throttling
- **X-Ray Tracing**: Distributed tracing for Lambda functions
- **CloudWatch Insights**: Query and analyze logs
- **Cost Explorer**: Track infrastructure costs

## Troubleshooting

### Common Issues (when implementing)

**Issue**: CDK synthesis fails
```bash
# Check Python dependencies
pip list | grep aws-cdk

# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

**Issue**: Deployment fails with permissions error
```bash
# Check AWS credentials
aws sts get-caller-identity

# Verify IAM permissions for CDK
```

**Issue**: Lambda function fails to deploy
```bash
# Check Lambda code path
ls -la ../../agents/coordinator/

# Verify handler exists
grep -r "def handler" ../../agents/coordinator/
```

## Additional Resources

- [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
- [CDK Python Reference](https://docs.aws.amazon.com/cdk/api/v2/python/)
- [CDK Best Practices](https://docs.aws.amazon.com/cdk/latest/guide/best-practices.html)
- [Auth0 AWS Integration](https://auth0.com/docs/integrations/aws)
- [AgentCore Documentation](../../README.md)

## Support

For issues or questions:
- Review stub comments in stack files
- Check AWS CDK documentation
- Consult AgentCore architecture documentation
- Contact the AgentCore development team

## License

Apache-2.0
