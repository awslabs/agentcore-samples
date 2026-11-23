# MCP Server with Keycloak DCR Authentication on AgentCore Runtime

## Overview

This tutorial demonstrates how to deploy an MCP (Model Context Protocol) server to Amazon Bedrock AgentCore Runtime with **JWT authentication** powered by Keycloak and Google OAuth. Instead of static API keys, users authenticate via their Google account, and AgentCore validates the JWT token against Keycloak's OIDC endpoint.

The authentication flow uses **Dynamic Client Registration (DCR)** with **PKCE**, meaning:

1. A local MCP proxy registers itself as a client in Keycloak using an **Initial Access Token (IAT)**
2. The user authenticates via **Google login** in the browser
3. Keycloak issues a **JWT token** containing the user's identity
4. The proxy sends this JWT to AgentCore, which **validates it** against Keycloak's JWKS endpoint
5. If valid, AgentCore executes the MCP tool and returns the result

This gives you per-user authentication with zero static credentials. Tokens expire automatically, and you can revoke access by disabling users in Keycloak.

### Why Keycloak?

- **Self-hosted**: No vendor lock-in, runs on a single EC2 instance
- **DCR support**: Clients can register themselves dynamically (no manual client creation)
- **Google IdP**: Users log in with their existing Google account
- **OIDC compliant**: Works natively with AgentCore's JWT authorizer
- **Free**: Open source, no per-user pricing

## Prerequisites

Before starting this tutorial, ensure you have:

- AWS CLI configured with appropriate permissions
- Python 3.11+ installed
- [UV](https://docs.astral.sh/uv/) installed (`brew install uv`)
- A custom domain name (registered in Route 53 or external registrar like GoDaddy)
- A [Google Cloud project](https://console.cloud.google.com/apis/credentials) for OAuth credentials

### ⚠️ Why a Custom Domain is Required

The ALB gets an auto-generated DNS (e.g., `keycloak-alb-xxx.elb.amazonaws.com`) that **cannot** have an ACM/TLS certificate. Without HTTPS, the entire authentication chain breaks:

| Component | Why HTTPS is required |
|---|---|
| **ACM** | Does not issue certificates for `*.elb.amazonaws.com` domains |
| **Google OAuth** | Rejects redirect URIs without HTTPS |
| **AgentCore** | Cannot validate JWT tokens — OIDC discovery URL must be HTTPS |
| **Keycloak** | Token issuer (`iss` claim) won't match if not using a consistent HTTPS domain |

If you don't have a domain, you can register one via [Route 53](https://console.aws.amazon.com/route53/home#DomainRegistration) (~$12/year for `.com`).

## Getting Started

The deployment is split into 6 phases. Follow the step-by-step guide below.

Before starting, create the `.env` file that will hold all configuration values. You'll fill it in progressively as you complete each phase:

```bash
cat > .env <<'EOF'
# Phase 2 — filled after domain configuration
KEYCLOAK_URL=
KEYCLOAK_REALM=main
REGION=us-east-1

# Phase 4 — filled after Keycloak configuration
INITIAL_ACCESS_TOKEN=

# Phase 5 — filled after AgentCore deploy
AGENT_ARN=

# Phase 6 — MCP Proxy settings
CALLBACK_PORT=3031
REDIRECT_URI=http://localhost:3031/callback
EOF
```

> Each phase below indicates when to update a value in `.env` with a 📝 marker.

### Phase 1 — Deploy Infrastructure (CloudFormation)

> Provisions an EC2 instance running Keycloak behind an Application Load Balancer. The admin password is auto-generated and stored in AWS Secrets Manager.

```bash
aws cloudformation create-stack \
  --stack-name keycloak \
  --template-body file://keycloak-complete-stack.yaml \
  --parameters \
    'ParameterKey=VpcId,ParameterValue=<YOUR_VPC_ID>' \
    'ParameterKey=PublicSubnetIds,ParameterValue=<SUBNET_AZ1>\,<SUBNET_AZ2>' \
  --capabilities CAPABILITY_IAM \
  --region us-east-1
```

> **Tip**: Use the default VPC. Find the VPC ID and subnets with:
> ```bash
> # Get default VPC ID
> aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text
> # List subnets with AZs
> aws ec2 describe-subnets --filters "Name=vpc-id,Values=<VPC_ID>" --query "Subnets[].{Id:SubnetId,AZ:AvailabilityZone}" --output table
> ```
>
> **Warning**: Not all AZs support `t4g` instances (e.g., `us-east-1e` does not). If the stack fails with "instance type not supported in your requested Availability Zone", retry with subnets in `us-east-1a` or `us-east-1b`.

Wait for completion (~3 min):
```bash
aws cloudformation wait stack-create-complete --stack-name keycloak
aws cloudformation describe-stacks --stack-name keycloak --query "Stacks[0].Outputs"
```

Note the `LoadBalancerDNS`, `InstanceId`, and `KeycloakAdminSecretArn` from the outputs.

> The admin password is auto-generated. Retrieve it with:
> ```bash
> aws secretsmanager get-secret-value --secret-id <KeycloakAdminSecretArn> --query SecretString --output text
> ```

### Phase 2 — Configure Domain + HTTPS

> Connects your custom domain to the ALB and enables HTTPS with a TLS certificate. This is critical because Google OAuth, AgentCore JWT validation, and Keycloak token issuance all require HTTPS.

#### 2a. Create Route 53 Hosted Zone (if domain is at external registrar)

```bash
aws route53 create-hosted-zone \
  --name yourdomain.com \
  --caller-reference "keycloak-$(date +%Y%m%d%H%M%S)"
```

Note the 4 nameservers from the output (`DelegationSet.NameServers`) and the **Hosted Zone ID** (`HostedZone.Id`), then update the nameservers at your registrar:

- **GoDaddy**: Domain → DNS → Nameservers → "I'll use my own nameservers" → enter the 4 NS values
- **Namecheap**: Domain List → Manage → Nameservers → Custom DNS → enter the 4 NS values

> **Important**: DNS propagation can take minutes to 48 hours. The ACM certificate validation won't complete until nameservers propagate.

#### 2b. Request ACM Certificate

```bash
aws acm request-certificate \
  --domain-name yourdomain.com \
  --subject-alternative-names "*.yourdomain.com" \
  --validation-method DNS \
  --region us-east-1 \
  --query "CertificateArn" --output text
```

#### 2c. Validate Certificate via DNS

```bash
# Get the CNAME validation record
aws acm describe-certificate \
  --certificate-arn <CERT_ARN> \
  --query "Certificate.DomainValidationOptions[0].ResourceRecord"

# Create the CNAME in Route 53
aws route53 change-resource-record-sets --hosted-zone-id <ZONE_ID> --change-batch '{
  "Changes": [{"Action":"CREATE","ResourceRecordSet":{
    "Name":"<CNAME_NAME>","Type":"CNAME","TTL":300,
    "ResourceRecords":[{"Value":"<CNAME_VALUE>"}]
  }}]
}'

# Wait for validation (~5-30 min after DNS propagation)
aws acm wait certificate-validated --certificate-arn <CERT_ARN>
```

#### 2d. Point Domain to ALB

```bash
aws route53 change-resource-record-sets --hosted-zone-id <ZONE_ID> --change-batch '{
  "Changes": [{"Action":"CREATE","ResourceRecordSet":{
    "Name":"keycloak.yourdomain.com","Type":"CNAME","TTL":300,
    "ResourceRecords":[{"Value":"<ALB_DNS>"}]
  }}]
}'
```

#### 2e. Add HTTPS Listener to ALB

```bash
ALB_ARN=$(aws cloudformation describe-stack-resource --stack-name keycloak --logical-resource-id LoadBalancer --query "StackResourceDetail.PhysicalResourceId" --output text)
TG_ARN=$(aws cloudformation describe-stack-resource --stack-name keycloak --logical-resource-id TargetGroup --query "StackResourceDetail.PhysicalResourceId" --output text)

aws elbv2 create-listener \
  --load-balancer-arn $ALB_ARN \
  --protocol HTTPS --port 443 \
  --certificates CertificateArn=<CERT_ARN> \
  --default-actions Type=forward,TargetGroupArn=$TG_ARN
```

Verify: `https://keycloak.yourdomain.com` should show the Keycloak welcome page.

📝 **Update `.env`**: Set `KEYCLOAK_URL=https://keycloak.yourdomain.com`

### Phase 3 — Google OAuth Credentials

> Creates OAuth credentials in Google Cloud so Keycloak can delegate authentication to Google. This is the only manual step.

1. Go to [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
2. Configure [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent) (External, scopes: `email`, `profile`, `openid`)
3. Create **OAuth 2.0 Client ID** (Web application)
4. Add redirect URI: `https://keycloak.yourdomain.com/realms/main/broker/google/endpoint`
5. Copy the **Client ID** and **Client Secret**

### Phase 4 — Configure Keycloak (via SSM)

> Configures Keycloak remotely (via AWS SSM, no SSH needed) to create a realm, connect Google as an identity provider, and generate an Initial Access Token (IAT) for DCR.

#### 4a. Create realm, set frontend URL, configure Google IdP

```bash
INSTANCE_ID=<from Phase 1 output>

aws ssm send-command \
  --instance-ids $INSTANCE_ID \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=[
    "KC_PASS=$(aws secretsmanager get-secret-value --secret-id <SECRET_NAME> --region us-east-1 --query SecretString --output text)",
    "/opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080 --realm master --user admin --password \"$KC_PASS\"",
    "/opt/keycloak/bin/kcadm.sh create realms -s realm=main -s enabled=true",
    "/opt/keycloak/bin/kcadm.sh update realms/main -s attributes.frontendUrl=https://keycloak.yourdomain.com",
    "/opt/keycloak/bin/kcadm.sh create identity-provider/instances -r main -s alias=google -s providerId=google -s enabled=true -s config.clientId=<GOOGLE_CLIENT_ID> -s config.clientSecret=<GOOGLE_CLIENT_SECRET> -s config.defaultScope=\"openid email profile\""
  ]' \
  --region us-east-1
```

#### 4b. Generate Initial Access Token (IAT)

> `kcadm.sh create clients-initial-access` outputs the token to stderr, making it hard to capture via SSM. Use the REST API instead:

```bash
aws ssm send-command \
  --instance-ids $INSTANCE_ID \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=[
    "KC_PASS=$(aws secretsmanager get-secret-value --secret-id <SECRET_NAME> --region us-east-1 --query SecretString --output text)",
    "TOKEN=$(curl -s -X POST http://localhost:8080/realms/master/protocol/openid-connect/token -d client_id=admin-cli -d username=admin -d \"password=$KC_PASS\" -d grant_type=password | python3 -c \"import sys,json;print(json.load(sys.stdin)['"'"'access_token'"'"'])\")",
    "curl -s -X POST http://localhost:8080/admin/realms/main/clients-initial-access -H \"Authorization: Bearer $TOKEN\" -H \"Content-Type: application/json\" -d \"{\\\"count\\\":1000,\\\"expiration\\\":31536000}\""
  ]' \
  --region us-east-1
```

Get the output:
```bash
aws ssm get-command-invocation \
  --command-id <COMMAND_ID> \
  --instance-id $INSTANCE_ID \
  --query "StandardOutputContent" --output text
```

The response will be JSON like: `{"id":"...","token":"eyJ...","count":1000,...}`. Save the `token` value.

📝 **Update `.env`**: Set `INITIAL_ACCESS_TOKEN=<token value from JSON response>`

### Phase 5 — Deploy AgentCore Runtime

> Deploys your MCP server to Amazon Bedrock AgentCore with a JWT authorizer. AgentCore will only accept requests with a valid JWT token issued by your Keycloak instance.

> **Note**: If you've deployed before in a different account/region, clear the cached `agent_id` and `agent_arn` in `.bedrock_agentcore.yaml` (set them to `null`) to force creation of a new agent.

```bash
cd strands_simple_agent
AWS_DEFAULT_REGION=us-east-1 uv run python deploy_mcp_keycloak.py
```

📝 **Update `.env`**: Set `AGENT_ARN=<Agent ARN from output>`

### Phase 6 — Test

> Runs an end-to-end test: DCR → Google OAuth → JWT → AgentCore → MCP tool call.

```bash
uv run python test_mcp_keycloak.py
```

Expected output:
```
📝 Registering client via DCR...
✅ Client: mcp-test-XXXXXX
🌐 Opening browser for login...
🔐 Using PKCE for security
✅ Token obtained!

=== Tools ===
  - add_numbers, multiply_numbers, greet_user

=== Test: add_numbers(10, 20) ===
Result: 30
```

## What You'll Learn

* How to deploy Keycloak on EC2 behind an ALB with HTTPS
* How to configure Keycloak DCR with Google as an identity provider
* How to deploy an MCP server to AgentCore with JWT authentication
* How to build a local MCP proxy that handles OAuth + DCR transparently

### Tutorial Details

| Information | Details |
|:---|:---|
| Tutorial type | MCP Server with JWT Authentication |
| Auth method | Keycloak DCR + Google OAuth + PKCE |
| Tutorial components | CloudFormation, Route 53, ACM, Keycloak, AgentCore Runtime |
| Tutorial vertical | Security / Identity |
| Example complexity | Advanced |
| SDK used | Amazon BedrockAgentCore Starter Toolkit, MCP SDK, FastMCP |

### Tutorial Architecture

```
IDE (Kiro) → MCP Proxy (local) → Keycloak (Google OAuth) → AgentCore (remote MCP)
                          │
                          ├─ 1. DCR: Register client dynamically (IAT)
                          ├─ 2. Browser: Google OAuth login
                          ├─ 3. Keycloak: Issues JWT token (cached locally)
                          └─ 4. AgentCore: Validates JWT via JWKS endpoint
```

### Tutorial Key Features

* Self-hosted OIDC identity provider (Keycloak) on EC2
* Dynamic Client Registration — no manual client creation needed
* Google OAuth login — users authenticate with their existing Google account
* JWT validation on AgentCore — zero static API keys
* Auto-generated admin password via AWS Secrets Manager
* Full HTTPS with ACM wildcard certificate

### Deployment Summary

| Phase | What | How | Manual? |
|-------|------|-----|---------|
| 1 | Infrastructure | CloudFormation CLI | No |
| 2 | Domain + HTTPS | Route 53 + ACM + ALB CLI (+ registrar NS update) | No* |
| 3 | Google OAuth | Google Cloud Console | **Yes** |
| 4 | Keycloak config | SSM + kcadm.sh + REST API | No |
| 5 | AgentCore | deploy script | No |
| 6 | Test | end-to-end script | No |

> \* Phase 2 requires updating nameservers at your domain registrar if the domain is not registered in Route 53.

## Files

| File | Description |
|------|-------------|
| `keycloak-complete-stack.yaml` | CloudFormation template: EC2 + ALB (Phase 1) |
| `deploy_mcp_keycloak.py` | Deploy MCP server to AgentCore with JWT authorizer (Phase 5) |
| `mcp_server.py` | MCP server with tools (FastMCP) — deployed to AgentCore |
| `mcp_proxy.py` | Local proxy — handles DCR + OAuth + token caching |
| `test_mcp_keycloak.py` | End-to-end test script |
| `pyproject.toml` | Package config for `uvx --from git+` installation |
| `.env` | Environment variables (not committed) |

## IDE Integration

### Kiro IDE

Add to `.kiro/mcp.json`:

```json
{
  "mcpServers": {
    "keycloak-mcp": {
      "command": "uvx",
      "args": [
        "--from",
        "git+ssh://git@ssh.gitlab.aws.dev/balafabi/agentcore-runtime-mcp-auth-keycloak-dcr.git#subdirectory=strands_simple_agent",
        "mcp_proxy"
      ],
      "env": {
        "KEYCLOAK_URL": "https://keycloak.yourdomain.com",
        "KEYCLOAK_REALM": "main",
        "INITIAL_ACCESS_TOKEN": "<IAT from Phase 4>",
        "REGION": "us-east-1",
        "AGENT_ARN": "<Agent ARN from Phase 5>",
        "CALLBACK_PORT": "3031",
        "REDIRECT_URI": "http://localhost:3031/callback"
      }
    }
  }
}
```

### Kiro CLI

Add to `~/.kiro/agents/<your-agent>.json`:

```json
{
  "mcpServers": {
    "keycloak-mcp": {
      "command": "uvx",
      "args": [
        "--from",
        "git+ssh://git@ssh.gitlab.aws.dev/balafabi/agentcore-runtime-mcp-auth-keycloak-dcr.git#subdirectory=strands_simple_agent",
        "mcp_proxy"
      ],
      "env": {
        "KEYCLOAK_URL": "https://keycloak.yourdomain.com",
        "KEYCLOAK_REALM": "main",
        "INITIAL_ACCESS_TOKEN": "<IAT from Phase 4>",
        "REGION": "us-east-1",
        "AGENT_ARN": "<Agent ARN from Phase 5>",
        "CALLBACK_PORT": "3031",
        "REDIRECT_URI": "http://localhost:3031/callback"
      },
      "autoApprove": ["list_tools", "call_tool"]
    }
  }
}
```

> Both configurations use `uvx --from git+ssh://...` to install and run the MCP proxy directly from the repository. Environment variables are passed inline — no `.env` file needed on the client side.

## Proxy Tools

| Tool | Description |
|------|-------------|
| `list_tools()` | Lists available tools on the remote AgentCore server |
| `call_tool(tool_name, arguments)` | Calls any tool on the remote server |
