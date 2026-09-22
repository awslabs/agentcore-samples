# Lakehouse Agent Deployment Record

**Deployment Date**: September 1, 2026  
**AWS Account**: 840016564632  
**Region**: us-east-1  
**Identity Providers Supported**: Cognito, Okta, Auth0

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Deployment Steps](#deployment-steps)
4. [Deployed Resources](#deployed-resources)
5. [SSM Parameters](#ssm-parameters)
6. [Test Users](#test-users)
7. [Testing the Deployment](#testing-the-deployment)
8. [Troubleshooting](#troubleshooting)

---

## Overview

The Lakehouse Agent is a conversational AI agent that provides secure, role-based access to insurance claims data stored in S3 Tables and claim notes stored in OpenSearch Serverless. The architecture implements:

- **Row-level security** via Lake Formation and IAM role assumption
- **Tool-level authorization** via gateway interceptors
- **Two gateway patterns**:
  - Claims Gateway (GW1): JWT-based authorization with request/response interceptors
  - Notes Gateway (GW2): On-Behalf-Of (OBO) token exchange pattern

---

## Prerequisites

- AWS CLI configured with appropriate credentials
- Python 3.10+ with virtual environment
- Docker (for MCP server container builds)
- `bedrock-agentcore` CLI installed

### Virtual Environment Setup

```bash
cd /Users/setram/agentcore-samples/02-use-cases/01-conversational-agents/lakehouse-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Deployment Steps

All deployment scripts are run from the project root using the virtual environment:

```bash
cd /Users/setram/agentcore-samples/02-use-cases/01-conversational-agents/lakehouse-agent
```

### Step 1: Cognito Setup

**Directory**: `deployment/1-cognito-setup/`

Creates Cognito User Pool, app clients, resource server, and test users with group memberships.

```bash
cd deployment/1-cognito-setup && ../../.venv/bin/python deploy_cognito.py
```

**Resources Created**:
- User Pool: `us-east-1_ltDCHjilU`
- App Client (user auth): `322g0thkmdsu36no6igp82strp`
- M2M Client (machine-to-machine): `6pr67sdg22h87v7vekkh7hnco8`
- Domain: `https://lakehouse-ltdchjilu.auth.us-east-1.amazoncognito.com`
- Resource Server: `lakehouse-api`
- Groups: `lakehouse-policyholders`, `lakehouse-adjusters`, `lakehouse-administrators`

### Step 1b: Auth0 Setup (Alternative to Cognito)

If using Auth0 as the identity provider instead of or in addition to Cognito:

**Prerequisites**:
1. Auth0 tenant with configured application
2. M2M (Machine-to-Machine) application for gateway-to-runtime communication
3. Auth0 Management API access for user metadata

**SSM Parameters Required**:

```bash
# Store Auth0 configuration in SSM
aws ssm put-parameter --name /app/lakehouse-agent/auth0-domain --value "your-tenant.auth0.com" --type String --region us-east-1
aws ssm put-parameter --name /app/lakehouse-agent/auth0-audience --value "https://your-api-identifier" --type String --region us-east-1
aws ssm put-parameter --name /app/lakehouse-agent/auth0-client-id --value "YOUR_CLIENT_ID" --type String --region us-east-1
aws ssm put-parameter --name /app/lakehouse-agent/auth0-client-secret --value "YOUR_CLIENT_SECRET" --type SecureString --region us-east-1
aws ssm put-parameter --name /app/lakehouse-agent/auth0-obo-client-id --value "YOUR_M2M_CLIENT_ID" --type String --region us-east-1
aws ssm put-parameter --name /app/lakehouse-agent/auth0-obo-client-secret --value "YOUR_M2M_CLIENT_SECRET" --type SecureString --region us-east-1
```

**DynamoDB Role Mapping for Auth0 Users**:

Auth0 `sub` claims use the format `auth0|{user_id}` (with a pipe character). Add role mappings for your Auth0 users:

```python
import boto3

dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
table = dynamodb.Table('lakehouse_tenant_role_map')

# Example: Add mapping for an Auth0 user
item = {
    "claim_name": "sub",
    "claim_value": "auth0|YOUR_USER_ID",  # Note: pipe character is valid
    "role_type": "iam_role",
    "role_value": "arn:aws:iam::840016564632:role/lakehouse-adjusters-role",
    "allowed_tools": ["get_claims_summary", "get_claim_details", "query_claims"],
    "description": "Auth0 user role mapping"
}
table.put_item(Item=item)
```

**Important Auth0 Notes**:
- The `sub` claim format `auth0|{id}` contains a pipe character, which is valid for DynamoDB partition keys
- Auth0 uses REQUEST interceptor pattern (similar to Cognito) rather than true OBO token exchange
- For the Notes Gateway, Auth0 M2M credentials are used for the gateway→runtime leg

### Step 2: IAM Roles

**Directory**: `deployment/2-iam-roles/`

Creates Lake Formation execution role and tenant-specific IAM roles for row-level security.

```bash
cd deployment/2-iam-roles && ../../.venv/bin/python deploy_iam_roles.py
```

**Resources Created**:
- `LakeFormationS3TablesDataAccessRole` - Lake Formation execution role
- `lakehouse-policyholders-role` - Row-level access for policyholders
- `lakehouse-adjusters-role` - Row-level access for adjusters
- `lakehouse-administrators-role` - Full access for administrators
- `lakehouse_tenant_role_map` - DynamoDB table for tenant-to-role mapping

### Step 3: S3 Tables + Lake Formation

**Directory**: `deployment/3-s3tables-setup/`

Creates S3 Tables bucket, Iceberg tables, and loads sample data.

```bash
cd deployment/3-s3tables-setup && ../../.venv/bin/python deploy_s3tables.py
cd deployment/3-s3tables-setup && ../../.venv/bin/python load_sample_data.py
```

**Resources Created**:
- S3 Tables Bucket: `lakehouse-840016564632-j6awxq`
- Catalog: `s3tablescatalog/lakehouse-840016564632-j6awxq`
- Database: `lakehouse_data`
- Tables: `claims`, `users`

### Step 4a: Claims MCP Server

**Directory**: `deployment/4a-mcp-claims-server/`

Deploys the MCP server runtime for claims queries against S3 Tables.

**Option A: Original boto3 scripts**
```bash
cd deployment/4a-mcp-claims-server && ../../.venv/bin/python deploy_runtime.py
```

**Option B: CLI deployment** (refactored to `cli-deployment/claims-mcp-server/`)
```bash
cd deployment/cli-deployment/claims-mcp-server && ../../../.venv/bin/python deploy.py
```

**Resources Created**:
- Runtime: `lakehouse_mcp_server-Hz7gWj6DeG`
- ARN: `arn:aws:bedrock-agentcore:us-east-1:840016564632:runtime/lakehouse_mcp_server-Hz7gWj6DeG`

### Step 4b: OpenSearch MCP Server

**Directory**: `deployment/4b-mcp-opensearch-server/`

Creates OpenSearch Serverless collection, deploys MCP server, and loads sample notes.

```bash
# Deploy OpenSearch collection (via 5b folder)
cd deployment/5b-obo-gateway-setup && ../../.venv/bin/python 01_deploy_opensearch_collection.py
```

**Option A: Original boto3 scripts**
```bash
# Deploy OpenSearch MCP runtime
cd deployment/4b-mcp-opensearch-server && ../../.venv/bin/python deploy_runtime.py
```

**Option B: CLI deployment** (refactored to `cli-deployment/opensearch-mcp-server/`)
```bash
cd deployment/cli-deployment/opensearch-mcp-server && ../../../.venv/bin/python deploy.py
```

```bash
# Seed Cognito user subs to SSM
cd deployment/4b-mcp-opensearch-server && ../../.venv/bin/python seed_cognito_user_subs.py

# Load sample claim notes
cd deployment/4b-mcp-opensearch-server && ../../.venv/bin/python load_sample_opensearch_data.py
```

**Resources Created**:
- OpenSearch Collection: `k3eaq4908mg90ao087h6`
- Endpoint: `https://k3eaq4908mg90ao087h6.us-east-1.aoss.amazonaws.com`
- Runtime: `opensearch_mcp_server-EWNIJu5t0c`
- Index: `claim-notes` (15 documents across 5 users)

### Step 5a: Gateway Interceptors + Claims Gateway

**Directory**: `deployment/5a-gateway-setup/`

Deploys Lambda interceptors and creates the Claims Gateway (GW1).

```bash
# Deploy request interceptor
cd deployment/5a-gateway-setup/interceptor-request && bash deploy.sh

# Deploy response interceptor
cd deployment/5a-gateway-setup/interceptor-response && bash deploy.sh

# Deploy notes interceptor
cd deployment/5a-gateway-setup/interceptor-notes && bash deploy.sh

# Create Claims Gateway
cd deployment/5a-gateway-setup && ../../.venv/bin/python create_gateway.py
```

**Resources Created**:
- `lakehouse-gateway-interceptor` - Request interceptor (tool authorization, role mapping)
- `lakehouse-gateway-response-interceptor` - Response interceptor (filters search tool)
- `lakehouse-notes-interceptor` - Notes interceptor (OBO identity injection)
- Gateway: `lakehouse-gateway-onimemt9or`
- URL: `https://lakehouse-gateway-onimemt9or.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp`

### Step 5b: Notes Gateway (OBO)

**Directory**: `deployment/5b-obo-gateway-setup/`

Creates the Notes Gateway (GW2) with On-Behalf-Of token exchange.

```bash
# Create OAuth provider
cd deployment/5b-obo-gateway-setup && ../../.venv/bin/python 03_create_oauth_provider.py

# Create OBO Gateway
cd deployment/5b-obo-gateway-setup && ../../.venv/bin/python 04_create_obo_gateway.py
```

**IMPORTANT: AOSS Data Access Policy Verification**

After deploying the OpenSearch MCP server runtime (Step 4b), verify the runtime role is included in the AOSS data access policy. Without this, the runtime will receive `403 Forbidden` errors when querying OpenSearch.

```bash
# Check current data access policy
aws opensearchserverless get-access-policy \
  --name lakehouse-claim-notes-data-access \
  --type data \
  --region us-east-1 \
  --query "accessPolicyDetail.policy" \
  --output text | python -m json.tool
```

The policy should include a rule block with the runtime execution role as a Principal:
```json
{
  "Rules": [
    {"Resource": ["collection/lakehouse-claim-notes"], "Permission": ["aoss:DescribeCollectionItems"], "ResourceType": "collection"},
    {"Resource": ["index/lakehouse-claim-notes/*"], "Permission": ["aoss:DescribeIndex", "aoss:ReadDocument"], "ResourceType": "index"}
  ],
  "Principal": ["arn:aws:iam::840016564632:role/bedrock-agentcore-runtime-role-opensearch_mcp_server"],
  "Description": "Read-only access for the OpenSearch_MCP_Server runtime role"
}
```

If missing, update the policy using `update_access_policy` or re-run `01_deploy_opensearch_collection.py` after cleaning up.

**Resources Created**:
- Gateway: `lakehouse-notes-gateway-ccf7yxxzxi`
- URL: `https://lakehouse-notes-gateway-ccf7yxxzxi.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp`

### Step 6: Lakehouse Agent

**Directory**: `deployment/6-lakehouse-agent/`

Deploys the conversational agent with two MCP clients.

**Option A: Original boto3 scripts**
```bash
cd deployment/6-lakehouse-agent && ../../.venv/bin/python deploy_lakehouse_agent.py
```

**Option B: CLI deployment** (refactored to `cli-deployment/lakehouse-agent/`)
```bash
cd deployment/cli-deployment/lakehouse-agent && ../../../.venv/bin/python deploy.py
```

**Resources Created**:
- Agent Runtime: `lakehouse_agent-nY19wb4b9X`
- ARN: `arn:aws:bedrock-agentcore:us-east-1:840016564632:runtime/lakehouse_agent-nY19wb4b9X`

### Step 7: Set Test User Passwords

**Directory**: `deployment/1-cognito-setup/`

Sets permanent passwords for test users (bypasses NEW_PASSWORD_REQUIRED challenge).

```bash
cd deployment/1-cognito-setup && ../../.venv/bin/python set_test_user_passwords.py
```

---

## Deployed Resources

### Compute

| Resource | ID/Name | ARN |
|----------|---------|-----|
| Lakehouse Agent | `lakehouse_agent-nY19wb4b9X` | `arn:aws:bedrock-agentcore:us-east-1:840016564632:runtime/lakehouse_agent-nY19wb4b9X` |
| Claims MCP Server | `lakehouse_mcp_server-Hz7gWj6DeG` | `arn:aws:bedrock-agentcore:us-east-1:840016564632:runtime/lakehouse_mcp_server-Hz7gWj6DeG` |
| OpenSearch MCP Server | `opensearch_mcp_server-EWNIJu5t0c` | `arn:aws:bedrock-agentcore:us-east-1:840016564632:runtime/opensearch_mcp_server-EWNIJu5t0c` |

### Gateways

| Gateway | ID | URL |
|---------|-----|-----|
| Claims Gateway (GW1) | `lakehouse-gateway-onimemt9or` | `https://lakehouse-gateway-onimemt9or.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp` |
| Notes Gateway (GW2) | `lakehouse-notes-gateway-ccf7yxxzxi` | `https://lakehouse-notes-gateway-ccf7yxxzxi.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp` |

### Lambda Functions

| Function | ARN |
|----------|-----|
| Request Interceptor | `arn:aws:lambda:us-east-1:840016564632:function:lakehouse-gateway-interceptor` |
| Response Interceptor | `arn:aws:lambda:us-east-1:840016564632:function:lakehouse-gateway-response-interceptor` |
| Notes Interceptor | `arn:aws:lambda:us-east-1:840016564632:function:lakehouse-notes-interceptor` |

### IAM Roles

| Role | ARN |
|------|-----|
| Lake Formation Role | `arn:aws:iam::840016564632:role/LakeFormationS3TablesDataAccessRole` |
| Interceptor Role | `arn:aws:iam::840016564632:role/InsuranceClaimsGatewayInterceptorRole` |
| Policyholders Role | `arn:aws:iam::840016564632:role/lakehouse-policyholders-role` |
| Adjusters Role | `arn:aws:iam::840016564632:role/lakehouse-adjusters-role` |
| Administrators Role | `arn:aws:iam::840016564632:role/lakehouse-administrators-role` |

### Data Stores

| Resource | Identifier |
|----------|------------|
| S3 Tables Bucket | `lakehouse-840016564632-j6awxq` |
| Catalog | `s3tablescatalog/lakehouse-840016564632-j6awxq` |
| Database | `lakehouse_data` |
| OpenSearch Collection | `k3eaq4908mg90ao087h6` |
| OpenSearch Endpoint | `https://k3eaq4908mg90ao087h6.us-east-1.aoss.amazonaws.com` |
| DynamoDB Table | `lakehouse_tenant_role_map` |

### Cognito

| Resource | Value |
|----------|-------|
| User Pool ID | `us-east-1_ltDCHjilU` |
| User Pool ARN | `arn:aws:cognito-idp:us-east-1:840016564632:userpool/us-east-1_ltDCHjilU` |
| App Client ID | `322g0thkmdsu36no6igp82strp` |
| M2M Client ID | `6pr67sdg22h87v7vekkh7hnco8` |
| Domain | `https://lakehouse-ltdchjilu.auth.us-east-1.amazoncognito.com` |
| Resource Server | `lakehouse-api` |

---

## SSM Parameters

All configuration is stored under `/app/lakehouse-agent/` prefix:

```bash
aws ssm get-parameters-by-path --path /app/lakehouse-agent --recursive --query "Parameters[*].[Name,Value]" --output table --region us-east-1
```

### Key Parameters

| Parameter | Description |
|-----------|-------------|
| `/app/lakehouse-agent/idp-provider` | Current IdP: `cognito`, `okta`, or `auth0` |
| `/app/lakehouse-agent/agent-runtime-arn` | Agent ARN |
| `/app/lakehouse-agent/gateway-url` | Claims Gateway URL |
| `/app/lakehouse-agent/notes-gateway-url` | Notes Gateway URL |
| `/app/lakehouse-agent/cognito-user-pool-id` | Cognito User Pool |
| `/app/lakehouse-agent/cognito-app-client-id` | Cognito App Client |
| `/app/lakehouse-agent/opensearch-collection-endpoint` | OpenSearch endpoint |

### Auth0-specific Parameters

| Parameter | Description |
|-----------|-------------|
| `/app/lakehouse-agent/auth0-domain` | Auth0 tenant domain (e.g., `your-tenant.auth0.com`) |
| `/app/lakehouse-agent/auth0-audience` | Auth0 API audience/identifier |
| `/app/lakehouse-agent/auth0-client-id` | Auth0 application client ID (user auth) |
| `/app/lakehouse-agent/auth0-client-secret` | Auth0 application client secret (SecureString) |
| `/app/lakehouse-agent/auth0-obo-client-id` | Auth0 M2M client ID (gateway→runtime) |
| `/app/lakehouse-agent/auth0-obo-client-secret` | Auth0 M2M client secret (SecureString) |

---

## Test Users

All test users use password: `TempPass123!`

| Email | Group | Permissions |
|-------|-------|-------------|
| `policyholder001@example.com` | `lakehouse-policyholders` | View own claims only |
| `policyholder002@example.com` | `lakehouse-policyholders` | View own claims only |
| `adjuster001@example.com` | `lakehouse-adjusters` | View all claims, manage notes |
| `adjuster002@example.com` | `lakehouse-adjusters` | View all claims, manage notes |
| `admin@example.com` | `lakehouse-administrators` | Full access |

### User SUBs (for OpenSearch owner filtering)

| User | Cognito SUB |
|------|-------------|
| policyholder001 | `e41854c8-a021-70f1-8f8c-b0bca30cd363` |
| policyholder002 | `f4f89498-50f1-70a9-1279-17e0ebf2163e` |
| adjuster001 | `9448b4b8-50d1-70c2-f99e-af8bd6164a47` |
| adjuster002 | `a4481448-9021-7004-747a-04aa9fbbd451` |
| admin | `64c8d4e8-20f1-700d-2d3c-194dc94f5dee` |

---

## Testing the Deployment

### Run Streamlit UI

```bash
cd /Users/setram/agentcore-samples/02-use-cases/01-conversational-agents/lakehouse-agent/streamlit-ui
../.venv/bin/streamlit run streamlit_app.py
```

Access at `http://localhost:8501`

### Test Gateway Directly

```bash
cd deployment/5a-gateway-setup && ../../.venv/bin/python test_gateway.py
```

### Sample Queries

1. **As Policyholder**: "Show me my claims"
2. **As Adjuster**: "List all open claims" or "Show notes for claim CLM-001"
3. **As Admin**: "Show all claims and their status"

---

## Troubleshooting

### Password Reset Loop

If the Streamlit UI shows password reset even after setting passwords:

```bash
cd deployment/1-cognito-setup && ../../.venv/bin/python set_test_user_passwords.py
```

### Check User Status

```bash
cd deployment/5a-gateway-setup && ../../.venv/bin/python check_users.py
```

### Verify SSM Parameters

```bash
aws ssm get-parameter --name /app/lakehouse-agent/idp-provider --query "Parameter.Value" --output text --region us-east-1
```

### View Lambda Logs

```bash
aws logs tail /aws/lambda/lakehouse-gateway-interceptor --follow --region us-east-1
```

### Sync OAuth Credential Providers with SSM

If gateway targets fail with "Error parsing ClientCredentials response" or "invalid_client" errors, the OAuth credential providers may have stale credentials that don't match what's in SSM. Use these steps to sync them:

#### For Okta

**Step 1: Get current credentials from SSM**

```bash
# Get Okta OBO credentials
OKTA_CLIENT_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/okta-obo-client-id --query "Parameter.Value" --output text --region us-east-1)
OKTA_CLIENT_SECRET=$(aws ssm get-parameter --name /app/lakehouse-agent/okta-obo-client-secret --query "Parameter.Value" --output text --region us-east-1 --with-decryption)
OKTA_DISCOVERY_URL="https://integrator-9803828.okta.com/oauth2/aus16nq99b5SrujQg698/.well-known/openid-configuration"

echo "Client ID: $OKTA_CLIENT_ID"
```

**Step 2: Update the Claims Gateway OAuth provider (`lakehouse-mcp-okta-oauth-provider`)**

```bash
aws bedrock-agentcore-control update-oauth2-credential-provider \
  --name lakehouse-mcp-okta-oauth-provider \
  --credential-provider-vendor CustomOauth2 \
  --oauth2-provider-config-input "{
    \"customOauth2ProviderConfig\": {
      \"oauthDiscovery\": {
        \"discoveryUrl\": \"$OKTA_DISCOVERY_URL\"
      },
      \"clientId\": \"$OKTA_CLIENT_ID\",
      \"clientSecret\": \"$OKTA_CLIENT_SECRET\"
    }
  }" \
  --region us-east-1
```

**Step 3: Update the Notes Gateway OAuth provider (`lakehouse-obo-okta-provider`)**

```bash
aws bedrock-agentcore-control update-oauth2-credential-provider \
  --name lakehouse-obo-okta-provider \
  --credential-provider-vendor CustomOauth2 \
  --oauth2-provider-config-input "{
    \"customOauth2ProviderConfig\": {
      \"oauthDiscovery\": {
        \"discoveryUrl\": \"$OKTA_DISCOVERY_URL\"
      },
      \"clientId\": \"$OKTA_CLIENT_ID\",
      \"clientSecret\": \"$OKTA_CLIENT_SECRET\"
    }
  }" \
  --region us-east-1
```

#### For Auth0

> **Important**: Auth0 requires the `audience` parameter for client credentials flow. You must use `Auth0Oauth2` vendor with `includedOauth2ProviderConfig` - do NOT use `CustomOauth2` as it doesn't send the required `audience` parameter.

**Step 1: Get current credentials from SSM**

```bash
# Get Auth0 M2M credentials
AUTH0_DOMAIN=$(aws ssm get-parameter --name /app/lakehouse-agent/auth0-domain --query "Parameter.Value" --output text --region us-east-1)
AUTH0_CLIENT_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/auth0-obo-client-id --query "Parameter.Value" --output text --region us-east-1)
AUTH0_CLIENT_SECRET=$(aws ssm get-parameter --name /app/lakehouse-agent/auth0-obo-client-secret --query "Parameter.Value" --output text --region us-east-1 --with-decryption)

echo "Auth0 Domain: $AUTH0_DOMAIN"
echo "Client ID: $AUTH0_CLIENT_ID"
```

**Step 2: Create/Update the Auth0 OAuth provider**

Use `Auth0Oauth2` vendor type with `includedOauth2ProviderConfig`:

```bash
# For new provider:
aws bedrock-agentcore-control create-oauth2-credential-provider \
  --name lakehouse-obo-auth0-provider \
  --credential-provider-vendor Auth0Oauth2 \
  --oauth2-provider-config-input "{
    \"includedOauth2ProviderConfig\": {
      \"clientId\": \"$AUTH0_CLIENT_ID\",
      \"clientSecret\": \"$AUTH0_CLIENT_SECRET\",
      \"authorizationEndpoint\": \"https://$AUTH0_DOMAIN/authorize\",
      \"tokenEndpoint\": \"https://$AUTH0_DOMAIN/oauth/token\",
      \"issuer\": \"https://$AUTH0_DOMAIN/\"
    }
  }" \
  --region us-east-1

# For existing provider update:
aws bedrock-agentcore-control update-oauth2-credential-provider \
  --name lakehouse-obo-auth0-provider \
  --credential-provider-vendor Auth0Oauth2 \
  --oauth2-provider-config-input "{
    \"includedOauth2ProviderConfig\": {
      \"clientId\": \"$AUTH0_CLIENT_ID\",
      \"clientSecret\": \"$AUTH0_CLIENT_SECRET\",
      \"authorizationEndpoint\": \"https://$AUTH0_DOMAIN/authorize\",
      \"tokenEndpoint\": \"https://$AUTH0_DOMAIN/oauth/token\",
      \"issuer\": \"https://$AUTH0_DOMAIN/\"
    }
  }" \
  --region us-east-1
```

**Step 3: Get the callback URL and register it with Auth0**

```bash
aws bedrock-agentcore-control get-oauth2-credential-provider \
  --name lakehouse-obo-auth0-provider \
  --region us-east-1 \
  --query "callbackUrl" --output text
```

Add this callback URL to your Auth0 application's "Allowed Callback URLs" in the Auth0 dashboard.

#### Trigger Gateway Target Re-sync (All IdPs)

**Step 4: Trigger gateway target re-sync**

After updating the OAuth providers, the gateway targets need to retry their connection. Update each target to trigger a re-sync:

```bash
# Get gateway and target IDs
CLAIMS_GATEWAY_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/gateway-id --query "Parameter.Value" --output text --region us-east-1)
NOTES_GATEWAY_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/notes-gateway-id --query "Parameter.Value" --output text --region us-east-1)

# List targets and their status
aws bedrock-agentcore-control list-gateway-targets --gateway-identifier $CLAIMS_GATEWAY_ID --region us-east-1
aws bedrock-agentcore-control list-gateway-targets --gateway-identifier $NOTES_GATEWAY_ID --region us-east-1

# Sync targets (replace TARGET_ID with actual IDs from above)
aws bedrock-agentcore-control synchronize-gateway-targets \
  --gateway-identifier $CLAIMS_GATEWAY_ID \
  --target-id-list TARGET_ID \
  --region us-east-1

aws bedrock-agentcore-control synchronize-gateway-targets \
  --gateway-identifier $NOTES_GATEWAY_ID \
  --target-id-list TARGET_ID \
  --region us-east-1
```

**Step 5: Verify target status**

```bash
# Check that targets are now READY
aws bedrock-agentcore-control get-gateway-target --gateway-identifier $CLAIMS_GATEWAY_ID --target-id TARGET_ID --region us-east-1 --query "status"
aws bedrock-agentcore-control get-gateway-target --gateway-identifier $NOTES_GATEWAY_ID --target-id TARGET_ID --region us-east-1 --query "status"
```

### OpenSearch 403 Forbidden Errors

If the OpenSearch MCP server returns 403 Forbidden errors when querying claim notes:

1. **Check AOSS data access policy** includes the runtime execution role:
```bash
aws opensearchserverless get-access-policy \
  --name lakehouse-claim-notes-data-access \
  --type data \
  --region us-east-1
```

2. **Check runtime role IAM permissions** include `aoss:APIAccessAll`:
```bash
aws iam get-role-policy \
  --role-name bedrock-agentcore-runtime-role-opensearch_mcp_server \
  --policy-name aoss-access-policy
```

3. If the AOSS data access policy is missing the runtime role, re-run the collection setup (after cleanup) or manually update the policy.

---

## Cleanup

To remove all deployed resources:

```bash
# Agent
cd deployment/6-lakehouse-agent && ../../.venv/bin/python cleanup_agent.py

# Gateways
cd deployment/5a-gateway-setup && ../../.venv/bin/python cleanup_gateway.py
cd deployment/5b-obo-gateway-setup && ../../.venv/bin/python 06_cleanup_obo_gateway.py

# MCP Servers
cd deployment/4a-mcp-claims-server && ../../.venv/bin/python cleanup_runtime.py
cd deployment/4b-mcp-opensearch-server && ../../.venv/bin/python cleanup_runtime.py

# S3 Tables, IAM, Cognito - manual cleanup or use respective cleanup scripts
```

---

## Refactoring Status

3 out of 10 deployment folders were refactored to use the `agentcore` CLI — specifically the three AgentCore runtime deployments (two MCP servers + the agent). Gateway creation stays with boto3 because the `agentcore` CLI doesn't support attaching Lambda interceptors, which are required for identity propagation and row-level security.

### Refactoring Summary

| Folder | Refactored? | CLI Location / Reason |
|--------|-------------|----------------------|
| `1-cognito-setup/` | No | Infrastructure setup, not a runtime |
| `1-okta-setup/` | No | Infrastructure setup, not a runtime |
| `2-lakehouse-tenant-roles-setup/` | No | IAM role setup, not a runtime |
| `3-s3tables-setup/` | No | Data infrastructure, not a runtime |
| `4a-mcp-lakehouse-server/` | **Yes** | → `cli-deployment/claims-mcp-server/` |
| `4b-mcp-opensearch-server/` | **Yes** | → `cli-deployment/opensearch-mcp-server/` |
| `5a-gateway-setup/` | No | CLI can't attach Lambda interceptors |
| `5b-obo-gateway-setup/` | No | CLI can't attach Lambda interceptors |
| `6-lakehouse-agent/` | **Yes** | → `cli-deployment/lakehouse-agent/` |
| `iam-policies/` | No | Policy templates, not deployable |

---

## Next Steps

- [x] Test Okta IdP integration
- [x] Test Auth0 IdP integration
- [ ] Performance testing with concurrent users
- [ ] Update CLI deployment scripts for compatibility
