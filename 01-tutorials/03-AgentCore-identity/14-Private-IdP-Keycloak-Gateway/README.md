# AgentCore Identity: Private IdP (Keycloak) with Gateway

## Overview

This sample shows how to configure **AgentCore Gateway** with inbound JWT authorization from a **private Keycloak** instance, plus a Lambda-backed MCP tool target. Callers authenticate with a Keycloak JWT, and the Gateway routes MCP `tools/call` requests to a Lambda function.

### Architecture

```
Caller
  │  1. Get JWT from private Keycloak
  │  2. POST /mcp with Authorization: Bearer <JWT>
  ▼
AgentCore Gateway (MCP protocol)
  │
  │  Validates JWT via JWKS
  ▼
AgentCore Identity ──VPC Lattice──▶ Internal ALB (ACM cert) ──▶ Keycloak EC2
  │                                      (privateEndpoint)
  │  Routes tools/call
  ▼
Lambda (ban-appeal-tools)
  │
  ▼
Response: enforcement status / appeal confirmation
```

### Tutorial Details

| Information | Details |
|-------------|---------|
| Tutorial type | CLI walkthrough |
| Agent type | Gateway + Lambda tools |
| AWS Services | AgentCore Gateway, VPC Lattice, ALB, ACM, EC2, Lambda, Route53 |
| Identity Provider | Keycloak 26 (self-hosted, private VPC) |
| Auth type | Inbound JWT (client_credentials grant) |
| Estimated time | 30 minutes |
| Estimated cost | ~$75/month (VPC Lattice + EC2 + ALB) |

## Prerequisites

- AWS CLI ≥ 2.34.37
- Keycloak deployed via `../shared-keycloak-infra/` (see Runtime sample Step 1-2)
- Python 3.11+

## Step 1: Deploy Keycloak (if not already done)

Follow Steps 1-2 from `../13-Private-IdP-Keycloak-Runtime/README.md`.

## Step 2: Create the Lambda Tool

```bash
cd lambda/
zip ban_appeal.zip ban_appeal.py

aws lambda create-function \
  --function-name ban-appeal-tools \
  --runtime python3.12 \
  --handler ban_appeal.handler \
  --role arn:aws:iam::<ACCOUNT>:role/<LambdaRole> \
  --zip-file fileb://ban_appeal.zip

aws lambda add-permission \
  --function-name ban-appeal-tools \
  --statement-id agentcore \
  --action lambda:InvokeFunction \
  --principal bedrock-agentcore.amazonaws.com
```

## Step 3: Create Gateway with privateEndpoint

```bash
aws bedrock-agentcore-control create-gateway \
  --cli-input-json '{
    "name": "private-keycloak-gw",
    "roleArn": "arn:aws:iam::<ACCOUNT>:role/AgentCoreGatewayRole",
    "protocolType": "MCP",
    "authorizerType": "CUSTOM_JWT",
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
    }
  }' \
  --region us-east-1
```

## Step 4: Register Lambda as Gateway Target

```bash
aws bedrock-agentcore-control create-gateway-target \
  --gateway-identifier <GATEWAY_ID> \
  --name ban-appeal-tools \
  --target-configuration '{
    "mcp": {
      "lambda": {
        "lambdaArn": "arn:aws:lambda:us-east-1:<ACCOUNT>:function:ban-appeal-tools",
        "toolSchema": {
          "inlinePayload": [
            {"name": "check_enforcement_status", "description": "Check player ban status", "inputSchema": {"type": "object", "properties": {"player_id": {"type": "string", "description": "Player ID"}}, "required": ["player_id"]}},
            {"name": "submit_appeal", "description": "Submit a ban appeal", "inputSchema": {"type": "object", "properties": {"player_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["player_id", "reason"]}}
          ]
        }
      }
    }
  }' \
  --credential-provider-configurations '[{"credentialProviderType": "GATEWAY_IAM_ROLE"}]'
```

## Step 5: Test

```bash
python invoke.py \
  --keycloak-url https://keycloak.your-domain.example.com \
  --gateway-url https://<GATEWAY_ID>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
```

## Cleanup

```bash
aws bedrock-agentcore-control delete-gateway-target --gateway-identifier <GW_ID> --target-id <TARGET_ID>
aws bedrock-agentcore-control delete-gateway --gateway-id <GW_ID>
aws lambda delete-function --function-name ban-appeal-tools
aws cloudformation delete-stack --stack-name keycloak-private-idp
```
