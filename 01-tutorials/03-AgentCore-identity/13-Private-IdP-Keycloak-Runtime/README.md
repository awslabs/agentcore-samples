# AgentCore Identity: Private IdP (Keycloak) with Runtime

## Overview

This sample shows how to configure **AgentCore Runtime** with inbound JWT authorization from a **private Keycloak** instance hosted inside your VPC. The Keycloak OIDC discovery endpoint is not publicly accessible — AgentCore Identity reaches it via VPC Lattice using the `privateEndpoint` configuration.

### Architecture

```
Caller (Lambda/App in VPC)
  │  1. Get JWT from private Keycloak
  │  2. Authorization: Bearer <Keycloak JWT>
  ▼
AgentCore Runtime
  │
  │  Validates JWT signature via JWKS
  ▼
AgentCore Identity ──VPC Lattice──▶ Internal ALB (ACM cert) ──▶ Keycloak EC2
                                         (privateEndpoint)
```

### Tutorial Details

| Information | Details |
|-------------|---------|
| Tutorial type | CLI walkthrough |
| Agent type | Single |
| Agentic Framework | Strands Agents |
| AWS Services | AgentCore Runtime, VPC Lattice, ALB, ACM, EC2, Route53 |
| Identity Provider | Keycloak 26 (self-hosted, private VPC) |
| Auth type | Inbound JWT (client_credentials grant) |
| Estimated time | 30 minutes |
| Estimated cost | ~$75/month (VPC Lattice + EC2 + ALB) |

## Prerequisites

- AWS CLI ≥ 2.34.37 (`aws --version`)
- A Route53 hosted zone you control (for ACM DNS validation)
- A VPC with at least 2 subnets in different AZs
- Python 3.11+
- AgentCore CLI (`pip install bedrock-agentcore`)

## Step 1: Deploy Keycloak Infrastructure

Deploy the shared CloudFormation template:

```bash
aws cloudformation deploy \
  --template-file ../shared-keycloak-infra/keycloak-infra.yaml \
  --stack-name keycloak-private-idp \
  --parameter-overrides \
    DomainName=keycloak.your-domain.example.com \
    HostedZoneId=Z0123456789 \
    VpcId=vpc-0abc123 \
    SubnetIds=subnet-0abc123,subnet-0def456 \
    KeycloakAdminPassword=YourSecurePassword123 \
  --capabilities CAPABILITY_IAM
```

Wait for stack completion (~5 min for ACM validation + EC2 boot).

## Step 2: Configure Keycloak

```bash
# Wait for Keycloak to boot and configure realm + client
python ../shared-keycloak-infra/setup_keycloak.py \
  --url http://<EC2-private-IP>:8080 \
  --password YourSecurePassword123
```

> **Note**: Run this from within the VPC (e.g., via SSM or a bastion) since Keycloak is only accessible internally.

## Step 3: Create AgentCore Runtime with privateEndpoint

```bash
aws bedrock-agentcore-control create-agent-runtime \
  --cli-input-json '{
    "agentRuntimeName": "private_keycloak_demo",
    "agentRuntimeArtifact": {
      "containerConfiguration": {
        "containerUri": "<YOUR_ECR_URI>:latest"
      }
    },
    "roleArn": "arn:aws:iam::<ACCOUNT>:role/AgentCoreRuntimeRole",
    "networkConfiguration": {"networkMode": "PUBLIC"},
    "authorizerConfiguration": {
      "customJWTAuthorizer": {
        "discoveryUrl": "https://keycloak.your-domain.example.com/realms/orion/.well-known/openid-configuration",
        "allowedClients": ["content-export-adapter"],
        "allowedAudience": ["account"],
        "privateEndpoint": {
          "managedVpcResource": {
            "vpcIdentifier": "vpc-0abc123",
            "subnetIds": ["subnet-0abc123", "subnet-0def456"],
            "endpointIpAddressType": "IPV4",
            "securityGroupIds": ["sg-0abc123"]
          }
        }
      }
    },
    "protocolConfiguration": {"serverProtocol": "HTTP"}
  }' \
  --region us-east-1
```

Wait for status `READY` (~5 min for VPC Lattice provisioning):

```bash
aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id <RUNTIME_ID> \
  --query 'status'
```

## Step 4: Test Invocation

```bash
python invoke.py \
  --keycloak-url https://keycloak.your-domain.example.com \
  --client-id content-export-adapter \
  --client-secret test-secret-12345 \
  --runtime-id <RUNTIME_ID>
```

## Cleanup

```bash
aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id <RUNTIME_ID>
aws cloudformation delete-stack --stack-name keycloak-private-idp
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "OIDC discovery endpoint is not valid" | Missing `privateEndpoint` | Add the `privateEndpoint` block |
| CREATE_FAILED | Self-signed cert or SG blocking | Use ACM cert; allow VPC CIDR on 443 |
| "Invalid inbound token" | Issuer mismatch | Set `KC_HOSTNAME` to match discovery URL |
| "insufficient_scope" | Audience mismatch | Set `allowedAudience` to match token's `aud` |
| CLI error "Unknown parameter privateEndpoint" | CLI too old | Upgrade to ≥ 2.34.37 |
