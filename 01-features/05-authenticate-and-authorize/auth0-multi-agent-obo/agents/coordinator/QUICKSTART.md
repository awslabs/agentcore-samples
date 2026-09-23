# Coordinator Agent - Quick Start Guide

This guide helps you get the Coordinator Agent up and running quickly.

## Prerequisites

- AWS Account with AgentCore access
- Auth0 tenant configured
- Python 3.11+
- Docker (for containerization)
- AWS CLI configured

## Step 1: Set Environment Variables

Create a `.env` file:

```bash
# AWS
AWS_REGION=us-east-1

# Auth0
AUTH0_DOMAIN=your-tenant.auth0.com
AUTH0_AUDIENCE=https://agentcore-financial-api

# AgentCore Agent IDs (set after creating sub-agents)
PROFILE_AGENT_ID=agent-profile-xyz
ACCOUNTS_AGENT_ID=agent-accounts-xyz

# AgentCore Memory
AGENTCORE_MEMORY_ID=memory-xyz123

# Logging
LOG_LEVEL=INFO
```

## Step 2: Install Dependencies

```bash
cd agents/coordinator
pip install -r requirements.txt
```

## Step 3: Build Docker Container

```bash
# Build the image
docker build -t coordinator-agent:latest .

# Test locally (optional)
docker run -p 8080:8080 \
  --env-file .env \
  coordinator-agent:latest
```

## Step 4: Push to ECR

```bash
# Authenticate to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  123456789012.dkr.ecr.us-east-1.amazonaws.com

# Create ECR repository
aws ecr create-repository \
  --repository-name coordinator-agent \
  --region us-east-1

# Tag and push
docker tag coordinator-agent:latest \
  123456789012.dkr.ecr.us-east-1.amazonaws.com/coordinator-agent:latest

docker push \
  123456789012.dkr.ecr.us-east-1.amazonaws.com/coordinator-agent:latest
```

## Step 5: Create AgentCore Agent

```bash
# Create the coordinator agent
aws bedrock-agent create-agent \
  --agent-name "Financial Services Coordinator" \
  --agent-resource-role-arn "arn:aws:iam::123456789012:role/AgentCoreRole" \
  --instruction "You are a financial services coordinator agent." \
  --foundation-model "anthropic.claude-3-5-sonnet-20241022-v2:0" \
  --runtime-configuration '{
    "containerImage": "123456789012.dkr.ecr.us-east-1.amazonaws.com/coordinator-agent:latest"
  }' \
  --region us-east-1
```

## Step 6: Configure JWT Authorizer

Set up Auth0 JWT authorizer in AgentCore:

```bash
# Create JWT authorizer (via CDK or Console)
# Configure with:
# - Issuer: https://your-tenant.auth0.com/
# - Audience: https://agentcore-financial-api
# - JWKS URL: https://your-tenant.auth0.com/.well-known/jwks.json
```

## Step 7: Test the Agent

### Test with AWS CLI

```bash
# Get an Auth0 access token first
ACCESS_TOKEN="your-jwt-token"

# Invoke the agent
aws bedrock-agent-runtime invoke-agent \
  --agent-id "agent-coordinator-xyz" \
  --agent-alias-id "TSTALIASID" \
  --session-id "test-session-123" \
  --input-text "What is my account balance?" \
  --session-state '{
    "sessionAttributes": {
      "authorization": "Bearer '$ACCESS_TOKEN'"
    }
  }' \
  output.json

# View response
cat output.json
```

### Test with Python

```python
import boto3
import json

client = boto3.client('bedrock-agent-runtime', region_name='us-east-1')

response = client.invoke_agent(
    agentId='agent-coordinator-xyz',
    agentAliasId='TSTALIASID',
    sessionId='test-session-123',
    inputText='What is my account balance?',
    sessionState={
        'sessionAttributes': {
            'authorization': 'Bearer your-jwt-token'
        }
    }
)

# Process streaming response
for event in response['completion']:
    if 'chunk' in event:
        print(event['chunk']['bytes'].decode('utf-8'))
```

## Common Commands

### View Logs

```bash
# View CloudWatch logs
aws logs tail /aws/bedrock-agent/coordinator \
  --follow \
  --region us-east-1
```

### Update Agent

```bash
# After code changes, rebuild and push
docker build -t coordinator-agent:latest .
docker tag coordinator-agent:latest \
  123456789012.dkr.ecr.us-east-1.amazonaws.com/coordinator-agent:latest
docker push \
  123456789012.dkr.ecr.us-east-1.amazonaws.com/coordinator-agent:latest

# Update agent to use new image
aws bedrock-agent update-agent \
  --agent-id "agent-coordinator-xyz" \
  --runtime-configuration '{
    "containerImage": "123456789012.dkr.ecr.us-east-1.amazonaws.com/coordinator-agent:latest"
  }' \
  --region us-east-1
```

### Run Tests

```bash
# Unit tests
pytest tests/test_coordinator.py -v

# Integration tests (requires AWS credentials)
pytest tests/test_coordinator_integration.py -v

# Coverage report
pytest --cov=coordinator tests/
```

## Troubleshooting

### Issue: "Agent ID not configured"

**Solution**: Ensure all sub-agent environment variables are set:
```bash
export PROFILE_AGENT_ID=agent-profile-xyz
export ACCOUNTS_AGENT_ID=agent-accounts-xyz
```

### Issue: "Authorization failed"

**Solution**: Check JWT token and claims:
- Verify token is not expired
- Check `customer_id` claim is present
- Verify required scopes (profile:personal:read, profile:personal:write)

### Issue: "Sub-agent invocation failed"

**Solution**: Check sub-agent configuration:
- Verify sub-agent IDs are correct
- Check sub-agent is deployed and active
- Review sub-agent CloudWatch logs

### Issue: "Import error for agentcore_runtime"

**Solution**: AgentCore Runtime is AWS proprietary. For local testing:
- Use mock objects for InvocationRequest/InvocationResponse
- Or run within AgentCore environment

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     AgentCore Gateway                        │
│                  (JWT Authentication)                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       │ JWT Token + Request
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   Coordinator Agent                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  1. Extract User Context (auth_context.py)           │  │
│  │  2. Validate Authorization                           │  │
│  │  3. Scope-gate tools (hide accounts if no scopes)   │  │
│  │  4. RFC 8693 token exchange (scope attenuation)     │  │
│  │  5. Route to Sub-Agents (subagent_router.py)         │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────┬──────────────────────┬───────────────────────┘
          │                      │
          ▼                      ▼
    ┌─────────┐            ┌─────────┐
    │ Profile │            │Accounts │
    │  Agent  │            │  Agent  │
    └─────────┘            └─────────┘
```

## Next Steps

1. Deploy the sub-agents (profile, accounts)
2. Configure Auth0 rules/actions for custom claims
3. Set up CloudWatch dashboards for monitoring
4. Implement the Streamlit client for testing
5. Configure production deployment with CDK

## Resources

- [Full README](./README.md)
- [AgentCore Documentation](https://docs.aws.amazon.com/agentcore/)
- [Auth0 Integration Guide](../../../docs/auth0-integration.md)
- [Architecture Overview](../../../docs/architecture.md)
