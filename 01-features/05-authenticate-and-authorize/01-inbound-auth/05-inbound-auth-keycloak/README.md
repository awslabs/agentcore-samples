# Connect AgentCore Identity to a private Keycloak IdP that uses a private CA

> **Disclaimer:** This sample is for experimental and educational purposes. It is not intended for production use.

| Information         | Details                                                                                    |
|:--------------------|:-------------------------------------------------------------------------------------------|
| Tutorial type       | Step-by-step                                                                               |
| Agent type          | Single (no LLM)                                                                            |
| Agentic Framework   | None (`bedrock-agentcore` SDK)                                                             |
| Tutorial components | AgentCore Identity, AgentCore Gateway, AgentCore Runtime, VPC Lattice, AWS Private CA, ACM |
| Example complexity  | Advanced                                                                                   |
| SDK used            | AWS CDK, boto3, AgentCore CLI                                                              |
| Credential provider | OAuth2 `CustomOauth2` with `privateEndpoint` and `certificateConfigurations`               |

## Overview

This sample deploys Keycloak in a VPC behind an internal Application Load Balancer (ALB) with a TLS certificate from AWS Private CA. Amazon Bedrock AgentCore reaches Keycloak through Amazon VPC Lattice (`privateEndpoint`) and trusts the certificate through the CA certificate that you provide (`certificateConfigurations`).

The sample shows two flows:

1. **Outbound OAuth** – The agent runtime gets a Keycloak access token (`client_credentials` grant) from an AgentCore Identity credential provider.
2. **Inbound JWT authorization** – The agent calls an AgentCore Gateway with the token. The gateway validates it against Keycloak's discovery document and JWKS over the same private path.

The agent returns the token claims and the gateway's `tools/list` response. It doesn't return the token.

## Architecture

```
                        ┌──────────────────────┐
                        │  AgentCore Runtime   │
                        └──────┬───────┬───────┘
          1. Get token (M2M)   │       │  2. tools/list (Bearer token)
                               ▼       ▼
┌────────────────────────────────┐  ┌────────────────────────────────┐
│  AgentCore Identity            │  │  AgentCore Gateway             │
│  credential provider           │  │  CUSTOM_JWT authorizer         │
│  privateEndpoint +             │  │  privateEndpoint +             │
│  certificateConfigurations     │  │  certificateConfigurations     │
└───────────────┬────────────────┘  └───────────────┬────────────────┘
                │ VPC Lattice (managed)             │ VPC Lattice (managed)
                ▼                                   ▼
┌───────────────────────────────────────────────────────────────────┐
│  VPC (private subnets)                                            │
│  Route 53 private hosted zone                                     │
│    keycloak.agentcore-sample.example.com ──► internal ALB (:443)  │
│                                              certificate: ACM,    │
│                                              issued by Private CA │
│                                              ▼                    │
│                                     Keycloak (ECS Fargate, :8080) │
└───────────────────────────────────────────────────────────────────┘
  Secrets Manager: CA certificate (PEM) ◄── certificateConfigurations
```

### Resources

| Created by | Resources |
|:-----------|:----------|
| `PrivateIdpKeycloakVpcStack` | VPC with private and public subnets in two Availability Zones, one NAT gateway |
| `PrivateIdpKeycloakStack` | Root CA in AWS Private CA (general-purpose mode), ACM private certificate, internal ALB, Keycloak on ECS Fargate, Route 53 private hosted zone, security groups, Secrets Manager secrets for the CA certificate, Keycloak admin password, and client secret |
| `PrivateIdpKeycloakGatewayInfraStack` | MCP Echo Lambda function (gateway target), gateway IAM role |
| `setup_agentcore.py` | OAuth2 credential provider, gateway, gateway target |
| `agentcore deploy` | Agent runtime |

`setup_agentcore.py` uses boto3 because the AgentCore CLI doesn't support `privateEndpoint` or `certificateConfigurations`.

The sample uses general-purpose mode because ACM doesn't issue certificates from CAs in short-lived certificate mode. To use an existing corporate CA instead, store its root certificate in Secrets Manager or Amazon S3 and skip the AWS Private CA resources.

### Network configuration

- The ALB security group allows inbound HTTPS from the VPC Lattice endpoint security group.
- The Keycloak security group allows inbound traffic from the ALB security group.
- The Keycloak hostname is a record in a private hosted zone associated with the VPC.
- ECS injects the admin password and client secret from Secrets Manager at container start. The templates contain secret references.

## Prerequisites

- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) 2.27 or later
- [AWS CDK](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html) v2
- [uv](https://docs.astral.sh/uv/) and Python 3.12 or later
- Node.js 20 or later
- [AgentCore CLI](https://github.com/aws/agentcore-cli) 0.30.0 (`npm install -g @aws/agentcore@0.30.0`)
- boto3 and botocore 1.44.2 or later, which add `certificateConfigurations`. `uv sync` installs them.
- IAM permissions to create the resources listed in [Resources](#resources), plus:
  - `secretsmanager:GetSecretValue` on the CA certificate secret
  - `iam:CreateServiceLinkedRole` for the AgentCore Identity service-linked role

### Cost

AWS Private CA in general-purpose mode costs USD 400 per CA per month, prorated (about USD 13 per day). Fargate, the ALB, and the NAT gateway add about USD 1–2 per day. For details, see [AWS Private CA pricing](https://aws.amazon.com/private-ca/pricing/).

## Setup

1. (Optional) To change the defaults, create a `.env` file with any of these variables.

   | Variable | Default | Description |
   |:---------|:--------|:------------|
   | `KEYCLOAK_DOMAIN` | `keycloak.agentcore-sample.example.com` | Keycloak hostname. AgentCore rejects URLs with some private TLDs, such as `.internal`. |
   | `KEYCLOAK_IMAGE` | `quay.io/keycloak/keycloak:26.8.2` | Keycloak container image |
   | `KEYCLOAK_REALM` | `agentcore` | Realm imported at startup |
   | `KEYCLOAK_CLIENT_ID` | `agentcore-m2m` | Confidential client for the `client_credentials` grant |
   | `KEYCLOAK_AUDIENCE` | `agentcore-gateway` | Token audience that the gateway accepts |

   The sample deploys to `AWS_REGION`, or to your AWS CLI default Region.

2. Run the deploy script. Deployment takes about 25 minutes.

   ```bash
   cd 01-features/05-authenticate-and-authorize/01-inbound-auth/05-inbound-auth-keycloak
   ./deploy_sample.sh
   ```

   The script deploys the CDK stacks, runs `setup_agentcore.py`, deploys the agent runtime, and invokes it.

### Credential provider and gateway configuration

`setup_agentcore.py` adds the same two settings to the credential provider and the gateway authorizer:

```python
"privateEndpoint": {
    "managedVpcResource": {
        "vpcIdentifier": "vpc-xxx",
        "subnetIds": ["subnet-xxx", "subnet-yyy"],
        "endpointIpAddressType": "IPV4",
        "securityGroupIds": ["sg-xxx"],
    }
},
"certificateConfigurations": [
    {"secretsManager": {"secretArn": "arn:aws:secretsmanager:us-east-1:111122223333:secret:CaCertificateSecret-xxx"}}
],
```

- `certificateConfigurations` takes one entry, either `s3` or `secretsManager`. The secret is a plaintext PEM string with CA certificates and no private key.
- An `UpdateOauth2CredentialProvider` call without `certificateConfigurations` removes the CA. `setup_agentcore.py` includes it when it updates the provider.
- `agentcore.json` declares the credential provider without a `discoveryUrl`, so `agentcore deploy` doesn't update the provider.

## Usage

```bash
cd agent/private-idp-keycloak-agent
agentcore invoke --prompt "test"
```

### Sample prompts

The agent ignores the prompt. Use any value, such as `"test"`.

### Expected output (abridged)

```json
{
  "success": true,
  "claims": {
    "iss": "https://keycloak.agentcore-sample.example.com/realms/agentcore",
    "aud": ["agentcore-gateway", "account"],
    "azp": "agentcore-m2m",
    "scope": "openid email profile"
  },
  "gateway": {
    "result": {
      "tools": [
        {"name": "McpEchoTarget___echo", "description": "Echo a message back"},
        {"name": "McpEchoTarget___get_time", "description": "Get the current UTC time"}
      ]
    }
  }
}
```

### Test a request without a token

```bash
GATEWAY_URL=$(python3 -c 'import json; print(json.load(open("agentcore_state.json"))["gatewayUrl"])')
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$GATEWAY_URL" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

The gateway returns `401`.

## Troubleshooting

| Symptom | Possible cause |
|:--------|:---------------|
| `DiscoveryUrl is not a valid URL` | The hostname TLD isn't accepted, for example `.internal`. |
| `GetResourceOauth2Token` returns `HTTP request failed against private endpoint` | The CA certificate is missing or doesn't match the chain, the certificate doesn't match the hostname, the private hosted zone isn't associated with the VPC, or a security group blocks port 443. |
| Gateway status is `FAILED` with `Failed to validate discovery url` | Same causes as the previous row. |
| Credential provider status is `CREATE_FAILED` | Your IAM identity can't read the CA secret, or the secret isn't a plaintext PEM CA certificate. |
| ALB targets are unhealthy | Keycloak is still starting. Check the `PrivateIdpKeycloakStack-LogGroup*` log group. |

In testing, a credential provider without `certificateConfigurations` reached `READY` but failed at token time with `HTTP request failed against private endpoint`, and a gateway without it went to `FAILED`.

## Clean up

```bash
./cleanup_sample.sh
```

The script deletes the agent runtime stack, the gateway, the credential provider, and the CDK stacks. AWS Private CA keeps a deleted CA in the `DELETED` state for 7 days, and you can restore it during that period.

VPC Lattice network interfaces can take up to 8 hours to release. If `PrivateIdpKeycloakVpcStack` fails to delete, run this command later:

```bash
uv run cdk destroy PrivateIdpKeycloakVpcStack --force
```

## Resources

- [Connect to private identity providers](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-private-idp.html)
- [CustomOauth2ProviderConfigInput](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CustomOauth2ProviderConfigInput.html)
- [CustomJWTAuthorizerConfiguration](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CustomJWTAuthorizerConfiguration.html)
- [AWS Private CA User Guide](https://docs.aws.amazon.com/privateca/latest/userguide/PcaWelcome.html)
- [Keycloak server configuration](https://www.keycloak.org/server/all-config)
- [PingFederate private IdP sample](../04-inbound-auth-pingfederate/)
