# Lakehouse Agent Deployment Guide (CLI-First)

This guide provides the complete deployment sequence for the Lakehouse Agent system using the `agentcore` CLI where possible and boto3 scripts where necessary (gateways with interceptors).

## Overview

The Lakehouse Agent is a conversational AI agent that provides secure, role-based access to insurance claims data stored in S3 Tables and claim notes stored in OpenSearch Serverless. The architecture implements:

- **Row-level security** via Lake Formation and IAM role assumption
- **Tool-level authorization** via gateway interceptors
- **Two gateway patterns**:
  - Claims Gateway (GW1): JWT-based authorization with request/response interceptors
  - Notes Gateway (GW2): On-Behalf-Of (OBO) token exchange pattern (Okta/Auth0) or interceptor pattern (Cognito)

This system supports **three identity providers**, selected by a single flag `IDP_PROVIDER ∈ {cognito, okta, auth0}` (default `cognito`). IdP-specific steps are marked **`[COGNITO]`**, **`[OKTA]`**, or **`[AUTH0]`** — run only the branch matching your choice.

### CLI vs boto3

| Component | Deployment Method | Reason |
|-----------|-------------------|--------|
| MCP Servers (Claims, OpenSearch) | `agentcore` CLI | Full CLI support |
| Lakehouse Agent | `agentcore` CLI | Full CLI support |
| Gateways (GW1, GW2) | boto3 scripts | CLI cannot attach Lambda interceptors |
| Interceptors | bash scripts | Lambda deployment |
| IdP, IAM, S3 Tables, Lake Formation | boto3/bash scripts | Infrastructure setup |

---

## Prerequisites

### Required Software

1. AWS CLI configured with appropriate permissions
2. Python 3.10+ with virtual environment
3. Docker running (for AgentCore Runtime deployments)
4. `bedrock-agentcore` CLI installed

```bash
# Install the agentcore CLI
pip install bedrock-agentcore

# Verify installation
agentcore --version
```

### AWS Region Configuration

All deployment scripts read the AWS region from your boto3 session:

```bash
# Option 1: Set via AWS CLI profile (recommended)
aws configure set region us-east-1 --profile your-profile

# Option 2: Set via environment variable
export AWS_REGION=us-east-1

# Verify your region
aws configure get region
```

> **Note**: Amazon Bedrock AgentCore is available in select regions. Verify [regional availability](https://docs.aws.amazon.com/general/latest/gr/bedrock-agent-core.html) before choosing a region.

### IdP-Specific Prerequisites

#### `[OKTA]` Prerequisites

If deploying with Okta (`IDP_PROVIDER=okta`):

1. An Okta org (free [Okta Integrator Free Plan](https://developer.okta.com/signup) is sufficient)
2. An Okta API token (Okta admin console → Security → API → Tokens)

Set both in `.env` before running Step 1:

```bash
OKTA_ORG_URL=dev-12345678.okta.com   # your tenant org URL, no scheme
OKTA_API_TOKEN=00abC...              # Okta management API token
```

> **🔑 The token needs a broad admin role.** Create it as a **Super Admin** (or custom role covering authorization servers, applications, groups, and users).

#### `[AUTH0]` Prerequisites

If deploying with Auth0 (`IDP_PROVIDER=auth0`):

1. An Auth0 tenant (free tier is sufficient)
2. A Management API application with appropriate permissions

Set in `.env` before running Step 1:

```bash
AUTH0_DOMAIN=your-tenant.auth0.com
AUTH0_CLIENT_ID=your-mgmt-api-client-id
AUTH0_CLIENT_SECRET=your-mgmt-api-client-secret
```

### Setup Virtual Environment

```bash
cd 02-use-cases/01-conversational-agents/lakehouse-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install bedrock-agentcore
```

---

## Deployment Sequence

### Step 0: Choose Your Identity Provider

Select the IdP once and persist it to SSM. Every downstream step reads the flag from SSM.

```bash
cd 02-use-cases/01-conversational-agents/lakehouse-agent
python -m utils.idp_config cognito   # or: okta, auth0
```

The default is `cognito`, so Cognito users may skip this step.

**SSM Parameters created:**
- `/app/lakehouse-agent/idp-provider`

---

### Step 1: Deploy Identity Provider

Run **only** the branch matching your `IDP_PROVIDER`.

#### `[COGNITO]` Deploy Cognito

Creates User Pool, OAuth clients, groups (policyholders, adjusters, administrators), and test users.

```bash
cd deployment/1-cognito-setup
python setup_cognito.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/cognito-user-pool-id`
- `/app/lakehouse-agent/cognito-user-pool-arn`
- `/app/lakehouse-agent/cognito-app-client-id`
- `/app/lakehouse-agent/cognito-app-client-secret` (SecureString)
- `/app/lakehouse-agent/cognito-m2m-client-id`
- `/app/lakehouse-agent/cognito-m2m-client-secret` (SecureString)
- `/app/lakehouse-agent/cognito-domain`
- `/app/lakehouse-agent/cognito-resource-server-id`
- `/app/lakehouse-agent/cognito-region`

**Test users created** (password: `TempPass123!`):
- `policyholder001@example.com`, `policyholder002@example.com` → policyholders
- `adjuster001@example.com`, `adjuster002@example.com` → adjusters
- `admin@example.com` → administrators

> **Important**: Users start in `FORCE_CHANGE_PASSWORD` state. Sign in via Streamlit UI (Step 9) once per user to complete the challenge.

#### `[OKTA]` Deploy Okta

Creates OIDC application, OBO exchange client, authorization server, groups, and test users.

```bash
cd deployment/1-okta-setup
pip install -r requirements.txt
python setup_okta.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/okta-org-url`
- `/app/lakehouse-agent/okta-auth-server-id`
- `/app/lakehouse-agent/okta-app-client-id`
- `/app/lakehouse-agent/okta-app-client-secret` (SecureString)
- `/app/lakehouse-agent/okta-obo-client-id`
- `/app/lakehouse-agent/okta-obo-client-secret` (SecureString)
- `/app/lakehouse-agent/okta-api-token` (SecureString)
- `/app/lakehouse-agent/okta-resource-server-audience`
- `/app/lakehouse-agent/okta-discovery-url`
- `/app/lakehouse-agent/okta-user-<label>-sub` (one per test user)

Verify setup:
```bash
python verify_okta_setup.py
```

#### `[AUTH0]` Deploy Auth0

Creates Regular Web App (user login), Non-interactive client with token-exchange grant (OBO), API resource server, roles, and test users.

```bash
cd deployment/1-auth0-setup
pip install -r requirements.txt
python setup_auth0.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/auth0-domain`
- `/app/lakehouse-agent/auth0-client-id`
- `/app/lakehouse-agent/auth0-client-secret` (SecureString)
- `/app/lakehouse-agent/auth0-obo-client-id`
- `/app/lakehouse-agent/auth0-obo-client-secret` (SecureString)
- `/app/lakehouse-agent/auth0-audience`
- `/app/lakehouse-agent/auth0-discovery-url`
- `/app/lakehouse-agent/auth0-policyholders-role-id`
- `/app/lakehouse-agent/auth0-adjusters-role-id`
- `/app/lakehouse-agent/auth0-administrators-role-id`
- `/app/lakehouse-agent/auth0-user-<label>-sub` (one per test user)

> **Manual steps required** after running the script:
> 1. **User-Delegated Client Grant** — Auth0 Dashboard → Applications → APIs → Machine to Machine tab
> 2. **"Add Roles to Tokens" Action** — Auth0 Dashboard → Actions → Flows → Login
>
> See the script output for detailed instructions.

Verify setup:
```bash
python verify_auth0_setup.py
```

---

### Step 2: Deploy IAM Roles for Tenant Groups

Creates IAM roles for policyholders, adjusters, and administrators with Athena/S3 permissions.

```bash
cd ../2-lakehouse-tenant-roles-setup
python setup_iam_roles.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/roles/lakehouse-policyholders-role`
- `/app/lakehouse-agent/roles/lakehouse-adjusters-role`
- `/app/lakehouse-agent/roles/lakehouse-administrators-role`

---

### Step 3: Deploy S3 Tables + Lake Formation

#### 3a. Grant Lake Formation Admin Permissions (One-time Setup)

Your AWS role needs Lake Formation administrator permissions.

**Option 1: AWS Console**
1. Go to AWS Lake Formation console
2. Navigate to "Administrative roles and tasks" → "Data lake administrators"
3. Click "Choose administrators"
4. Add your IAM role
5. Click "Save"

**Option 2: AWS CLI**
```bash
# Get current admins first (to preserve them)
aws lakeformation get-data-lake-settings --region us-east-1

# Add your role (APPEND to existing admins, don't replace)
aws lakeformation put-data-lake-settings \
  --data-lake-settings '{
    "DataLakeAdmins": [
      {"DataLakePrincipalIdentifier": "arn:aws:iam::YOUR_ACCOUNT:role/ExistingAdmin"},
      {"DataLakePrincipalIdentifier": "arn:aws:iam::YOUR_ACCOUNT:role/YourRole"}
    ]
  }' \
  --region us-east-1
```

> ⚠️ **`put-data-lake-settings` replaces the entire list** — always include existing admins.

#### 3b. Integrate S3 Tables with Lake Formation

```bash
cd ../3-s3tables-setup
python integrate_s3tables_lakeformation.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/lakeformation-role-arn`
- `/app/lakehouse-agent/s3tables-catalog-name`

#### 3c. Create S3 Tables

```bash
python setup_s3tables.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/table-bucket-name`
- `/app/lakehouse-agent/table-bucket-arn`
- `/app/lakehouse-agent/namespace`
- `/app/lakehouse-agent/catalog-name`
- `/app/lakehouse-agent/s3-bucket-name`

#### 3d. Configure Lake Formation Permissions

```bash
python setup_lakeformation_permissions.py
```

#### 3e. Load Sample Data

```bash
python load_sample_data.py
```

---

### Step 4: Deploy Claims MCP Server (CLI)

Deploys the claims MCP server using the `agentcore` CLI.

```bash
cd ../cli-deployment/claims-mcp-server
python deploy.py
```

This script:
1. Loads SSM configuration
2. Creates/updates IAM execution role
3. Generates `agentcore/aws-targets.json` with IdP-specific authorizer config
4. Runs `agentcore deploy`
5. Stores runtime ARN in SSM

**SSM Parameters created:**
- `/app/lakehouse-agent/mcp-server-runtime-arn`

---

### Step 5: Deploy Claims Gateway (GW1) Interceptors

#### 5.1 Deploy Request Interceptor

```bash
cd ../../5a-gateway-setup/interceptor-request
./deploy.sh
```

Creates:
- Lambda function with JWT validation and role mapping
- DynamoDB table `lakehouse_tenant_role_map`
- Tenant-to-role mappings with allowed tools

**SSM Parameters created:**
- `/app/lakehouse-agent/interceptor-lambda-arn`
- `/app/lakehouse-agent/interceptor-lambda-role-arn`
- `/app/lakehouse-agent/tenant-role-mapping-table`

#### 5.2 Deploy Response Interceptor

```bash
cd ../interceptor-response
./deploy.sh
```

Creates Lambda that filters tool list based on user permissions.

**SSM Parameters created:**
- `/app/lakehouse-agent/response-interceptor-lambda-arn`

---

### Step 6: Deploy Claims Gateway (GW1)

Creates the claims gateway with interceptors. Uses boto3 because the CLI cannot attach Lambda interceptors.

```bash
cd ..
python create_gateway.py --yes
```

**SSM Parameters created:**
- `/app/lakehouse-agent/gateway-id`
- `/app/lakehouse-agent/gateway-arn`
- `/app/lakehouse-agent/gateway-url`
- `/app/lakehouse-agent/gateway-name`

---

### Step 7: Deploy Notes Gateway (GW2) + OpenSearch

#### 7.1 Create OpenSearch Serverless Collection

```bash
cd ../5b-obo-gateway-setup
python 01_deploy_opensearch_collection.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/opensearch-collection-arn`
- `/app/lakehouse-agent/opensearch-collection-endpoint`

#### 7.2 Deploy OpenSearch MCP Server (CLI)

```bash
cd ../cli-deployment/opensearch-mcp-server
python deploy.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/opensearch-mcp-runtime-arn`
- `/app/lakehouse-agent/opensearch-mcp-runtime-id`

#### 7.3 Seed User Subs

**`[COGNITO]` only** (Okta/Auth0 seeded subs in Step 1):

```bash
cd ../../4b-mcp-opensearch-server
python seed_cognito_user_subs.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/cognito-user-<label>-sub` (one per test user)

#### 7.4 Load Sample Notes Data

```bash
python load_sample_opensearch_data.py
```

#### 7.5 Configure GW2 Authentication

Run **one** branch matching your IdP:

**`[OKTA]`** Create OBO credential provider:
```bash
cd ../5b-obo-gateway-setup
python 03_create_oauth_provider.py
```

SSM: `/app/lakehouse-agent/obo-credential-provider-arn`

**`[AUTH0]`** Create OBO credential provider:
```bash
cd ../5b-obo-gateway-setup
python 03_create_auth0_oauth_provider.py
```

SSM: `/app/lakehouse-agent/obo-credential-provider-arn`

**`[COGNITO]`** Deploy notes interceptor:
```bash
cd ../5a-gateway-setup/interceptor-notes
./deploy.sh
```

SSM: `/app/lakehouse-agent/notes-interceptor-lambda-arn`

#### 7.6 Create Notes Gateway (GW2)

```bash
cd ../../5b-obo-gateway-setup
python 04_create_obo_gateway.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/notes-gateway-id`
- `/app/lakehouse-agent/notes-gateway-arn`
- `/app/lakehouse-agent/notes-gateway-url`
- `/app/lakehouse-agent/notes-gateway-name`

---

### Step 8: Deploy Lakehouse Agent (CLI)

Deploys the conversational agent with two MCP clients (claims + notes).

```bash
cd ../cli-deployment/lakehouse-agent
python deploy.py
```

**SSM Parameters created:**
- `/app/lakehouse-agent/agent-runtime-arn`

---

### Step 9: Run Streamlit UI

```bash
cd ../../streamlit-ui
streamlit run streamlit_app.py
```

Access at: http://localhost:8501

---

## Quick Reference

| Step | IdP | Directory | Command |
|------|-----|-----------|---------|
| 0 | shared | repo root | `python -m utils.idp_config cognito` |
| 1 | `[COGNITO]` | `1-cognito-setup` | `python setup_cognito.py` |
| 1 | `[OKTA]` | `1-okta-setup` | `python setup_okta.py` |
| 1 | `[AUTH0]` | `1-auth0-setup` | `python setup_auth0.py` |
| 2 | shared | `2-lakehouse-tenant-roles-setup` | `python setup_iam_roles.py` |
| 3a | shared | Console/CLI | Grant LF admin |
| 3b | shared | `3-s3tables-setup` | `python integrate_s3tables_lakeformation.py` |
| 3c | shared | `3-s3tables-setup` | `python setup_s3tables.py` |
| 3d | shared | `3-s3tables-setup` | `python setup_lakeformation_permissions.py` |
| 3e | shared | `3-s3tables-setup` | `python load_sample_data.py` |
| 4 | shared | `cli-deployment/claims-mcp-server` | `python deploy.py` |
| 5.1 | shared | `5a-gateway-setup/interceptor-request` | `./deploy.sh` |
| 5.2 | shared | `5a-gateway-setup/interceptor-response` | `./deploy.sh` |
| 6 | shared | `5a-gateway-setup` | `python create_gateway.py --yes` |
| 7.1 | shared | `5b-obo-gateway-setup` | `python 01_deploy_opensearch_collection.py` |
| 7.2 | shared | `cli-deployment/opensearch-mcp-server` | `python deploy.py` |
| 7.3 | `[COGNITO]` | `4b-mcp-opensearch-server` | `python seed_cognito_user_subs.py` |
| 7.4 | shared | `4b-mcp-opensearch-server` | `python load_sample_opensearch_data.py` |
| 7.5 | `[OKTA]` | `5b-obo-gateway-setup` | `python 03_create_oauth_provider.py` |
| 7.5 | `[AUTH0]` | `5b-obo-gateway-setup` | `python 03_create_auth0_oauth_provider.py` |
| 7.5 | `[COGNITO]` | `5a-gateway-setup/interceptor-notes` | `./deploy.sh` |
| 7.6 | shared | `5b-obo-gateway-setup` | `python 04_create_obo_gateway.py` |
| 8 | shared | `cli-deployment/lakehouse-agent` | `python deploy.py` |
| 9 | shared | `streamlit-ui` | `streamlit run streamlit_app.py` |

---

## CLI Configuration Files

### agentcore.json

Defines the runtime:

```json
{
  "version": "0.1.0",
  "runtimes": [
    {
      "name": "lakehouse_mcp_server",
      "build": "Container",
      "codeLocation": "../../4a-mcp-lakehouse-server",
      "entrypoint": "opentelemetry-instrument,python,-m,server",
      "protocol": "MCP",
      "networkMode": "PUBLIC"
    }
  ]
}
```

### aws-targets.json

AWS-specific settings (generated by deploy.py):

```json
{
  "targets": {
    "aws": {
      "region": "us-east-1",
      "executionRoleArn": "arn:aws:iam::123456789:role/...",
      "envVars": {
        "AWS_REGION": "us-east-1",
        "S3_BUCKET_NAME": "my-bucket"
      },
      "authorizerConfiguration": {
        "customJWTAuthorizer": {
          "discoveryUrl": "https://...",
          "allowedClients": ["client-id"]
        }
      }
    }
  }
}
```

---

## Verify Deployment

Check all SSM parameters:

```bash
aws ssm get-parameters-by-path \
  --path /app/lakehouse-agent/ \
  --recursive \
  --query 'Parameters[*].[Name,Value]' \
  --output table
```

---

## Cleanup

Tear down in **reverse deploy order**. Run only the IdP-specific branches matching your deployment.

```bash
# Step 8: Delete Lakehouse Agent
cd cli-deployment/lakehouse-agent
python cleanup.py  # or use: cd ../../6-lakehouse-agent && python cleanup_agent.py

# Step 7.6: Delete Notes Gateway (GW2)
cd ../../5b-obo-gateway-setup
python 06_cleanup_obo_gateway.py

# Step 7.5 [COGNITO]: Delete notes interceptor
cd ../5a-gateway-setup/interceptor-notes
./cleanup.sh

# Step 7.2: Delete OpenSearch MCP Server
cd ../../cli-deployment/opensearch-mcp-server
python cleanup.py  # or use: cd ../../4b-mcp-opensearch-server && python cleanup_runtime.py

# Step 6/5: Delete Claims Gateway + interceptors
cd ../../5a-gateway-setup
python cleanup_gateway.py

# Step 4: Delete Claims MCP Server
cd ../cli-deployment/claims-mcp-server
python cleanup.py  # or use: cd ../4a-mcp-lakehouse-server && python cleanup_runtime.py

# Step 3: Delete S3 Tables
cd ../../3-s3tables-setup
python cleanup_s3tables.py

# Step 2: Delete IAM roles
cd ../2-lakehouse-tenant-roles-setup
python cleanup_iam_roles.py

# Step 1 [COGNITO]: Delete Cognito
cd ../1-cognito-setup
python cleanup_cognito.py

# Step 1 [OKTA]: Delete Okta resources
cd ../1-okta-setup
python cleanup_okta.py

# Step 1 [AUTH0]: Delete Auth0 resources
cd ../1-auth0-setup
python cleanup_auth0.py
```

> **`[OKTA]`/`[AUTH0]` Note**: Your API token survives cleanup — revoke it manually in the IdP console.

To delete remaining SSM parameters:

```bash
aws ssm delete-parameters --names $(aws ssm get-parameters-by-path \
  --path /app/lakehouse-agent/ --recursive \
  --query 'Parameters[*].Name' --output text)
```

---

## Troubleshooting

### "agentcore: command not found"

```bash
pip install bedrock-agentcore
```

### "No module named 'utils'"

Run from the correct directory — deploy.py scripts add the project root to sys.path.

### "SSM parameter not found"

Run prerequisite setup scripts (IdP, IAM roles, S3 Tables) before deploying runtimes.

### Docker build fails

Ensure Docker is running and you have ECR permissions.

### Gateway target sync failures

If targets fail with "invalid_client" errors, the OAuth credential providers may have stale credentials. Update them:

```bash
# Get current credentials from SSM
CLIENT_ID=$(aws ssm get-parameter --name /app/lakehouse-agent/okta-obo-client-id --query "Parameter.Value" --output text)
CLIENT_SECRET=$(aws ssm get-parameter --name /app/lakehouse-agent/okta-obo-client-secret --with-decryption --query "Parameter.Value" --output text)

# Update the credential provider
aws bedrock-agentcore-control update-oauth2-credential-provider \
  --name lakehouse-mcp-okta-oauth-provider \
  --credential-provider-vendor CustomOauth2 \
  --oauth2-provider-config-input "{...}"
```

### OpenSearch 403 Forbidden

Check that the AOSS data access policy includes the runtime execution role:

```bash
aws opensearchserverless get-access-policy \
  --name lakehouse-claim-notes-data-access \
  --type data \
  --region us-east-1
```

---

## Directory Structure

```
cli-deployment/
├── README.md                    # This file (complete deployment guide)
├── claims-mcp-server/           # Step 4: Claims MCP server (CLI)
│   ├── deploy.py
│   ├── cleanup.py
│   └── agentcore/
│       ├── agentcore.json
│       └── aws-targets.json     # Generated
├── opensearch-mcp-server/       # Step 7.2: OpenSearch MCP server (CLI)
│   ├── deploy.py
│   ├── cleanup.py
│   └── agentcore/
│       ├── agentcore.json
│       └── aws-targets.json     # Generated
└── lakehouse-agent/             # Step 8: Lakehouse Agent (CLI)
    ├── deploy.py
    ├── cleanup.py
    └── agentcore/
        ├── agentcore.json
        └── aws-targets.json     # Generated
```

---

## SSM Parameters Reference

All configuration is stored under `/app/lakehouse-agent/` prefix.

### Core Parameters (all IdPs)

| Parameter | Description |
|-----------|-------------|
| `idp-provider` | Current IdP: `cognito`, `okta`, or `auth0` |
| `mcp-server-runtime-arn` | Claims MCP Server ARN |
| `opensearch-mcp-runtime-arn` | OpenSearch MCP Server ARN |
| `agent-runtime-arn` | Lakehouse Agent ARN |
| `gateway-url` | Claims Gateway (GW1) URL |
| `notes-gateway-url` | Notes Gateway (GW2) URL |

### Cognito Parameters

| Parameter | Description |
|-----------|-------------|
| `cognito-user-pool-id` | User Pool ID |
| `cognito-app-client-id` | App Client ID |
| `cognito-m2m-client-id` | M2M Client ID |
| `cognito-domain` | Cognito domain URL |

### Okta Parameters

| Parameter | Description |
|-----------|-------------|
| `okta-org-url` | Okta tenant URL |
| `okta-app-client-id` | OIDC App Client ID |
| `okta-obo-client-id` | OBO Exchange Client ID |
| `okta-discovery-url` | OIDC Discovery URL |

### Auth0 Parameters

| Parameter | Description |
|-----------|-------------|
| `auth0-domain` | Auth0 tenant domain |
| `auth0-client-id` | User App Client ID |
| `auth0-obo-client-id` | OBO Exchange Client ID |
| `auth0-audience` | API audience |
| `auth0-discovery-url` | OIDC Discovery URL |
