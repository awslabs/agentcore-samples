# Private IdP Connectivity: PingFederate with AgentCore identity via VPC Lattice

> **Disclaimer:** This sample is for experimental and educational purposes only. It is not intended for production use.

This sample demonstrates how to connect **Amazon Bedrock AgentCore identity** to a privately hosted **PingFederate** identity Provider (IdP) using **Amazon VPC Lattice**, eliminating the need for the IdP to be exposed to the public internet.

The sample covers two AgentCore identity patterns:

1. **Outbound OAuth** — the agent runtime acquires OAuth tokens from the private PingFederate IdP via AgentCore identity and VPC Lattice (no public network path to the IdP).
2. **gateway inbound auth** — the agent presents its PingFederate token to an AgentCore gateway configured with CUSTOM_JWT authorization, proving that the gateway can validate JWTs from a private IdP via VPC Lattice.

The token is a security credential and is never exposed to an LLM or returned to the caller — only non-sensitive metadata (client_id, scope, expiry) and the gateway tools/list response are returned to confirm success.

## Deployment Modes

This sample supports two VPC Lattice deployment modes:

| Mode | Deploy Command | Description |
|------|---------------|-------------|
| **AgentCore-managed** (default) | `./deploy_sample.sh` | AgentCore identity creates and manages VPC Lattice resources automatically. You provide VPC and subnet IDs. Simpler setup. |
| **Self-managed** | `./deploy_sample.sh --self-managed-lattice` | You deploy VPC Lattice resources (resource gateway + configuration) via CDK. You manage the Lattice lifecycle. More control. |

## Architecture

```
                        ┌──────────────────────┐
                        │  AgentCore runtime    │
                        │  (agent)              │
                        └──────┬───────┬───────┘
           1. Get token        │       │  2. Call tools/list
           (outbound OAuth)    │       │  (Bearer token)
                               ▼       ▼
┌──────────────────────────┐  ┌──────────────────────────┐
│  AgentCore identity      │  │  AgentCore gateway       │
│  (credential provider    │  │  (CUSTOM_JWT auth with   │
│   with privateEndpoint)  │  │   privateEndpoint)       │
└────────────┬─────────────┘  └────────────┬─────────────┘
             │                              │
             │  VPC Lattice                 │  VPC Lattice
             │  (private connectivity)      │  (JWKS validation)
             ▼                              ▼
┌─────────────────────────────────────────────────────────┐
│  Your VPC (private subnets)                              │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Internal ALB (HTTPS:443)                        │    │
│  │  + Private Hosted Zone (ping.example.com → ALB)  │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         ▼                                │
│  ┌─────────────────────────────────────────────────┐    │
│  │  PingFederate (ECS Fargate)                      │    │
│  │  OAuth2/OIDC identity Provider                   │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## Why Private IdP Connectivity?

Many enterprises run their identity Providers (IdPs) in private networks with no public internet exposure. AgentCore identity needs to communicate with the IdP to perform OAuth2 flows (token acquisition, discovery, JWKS retrieval).

**VPC Lattice** solves this by providing private, secure, unidirectional network connectivity from AgentCore identity to your IdP without requiring:
- A public-facing load balancer
- VPN or Direct Connect
- VPC peering
- NAT gateways for the IdP

## Key Concepts

### Private Hosted Zone

A critical requirement: the VPC Lattice resource gateway resolves the discovery URL domain **from within your VPC**. You must create a Route 53 **private hosted zone** that maps your certificate domain (e.g., `ping.example.com`) to the internal ALB. This CDK sample creates the private hosted zone automatically.

Without the private hosted zone, AgentCore identity will fail with "HTTP request failed against private endpoint" because the domain cannot be resolved within the VPC.

### VPC Lattice Resource gateway

A **Resource gateway** is a set of Elastic Network Interfaces (ENIs) deployed in the private subnets of the VPC where your IdP runs. It serves as the ingress point for Lattice traffic into the VPC.

### VPC Lattice Resource Configuration

A **Resource Configuration** describes the target resource (your PingFederate ALB) so that Lattice knows where to route traffic. It specifies the DNS name, port, and protocol. The `rcfg-xxx` ID is what you provide to AgentCore identity in self-managed mode.

### AgentCore identity Private Endpoint

The `privateEndpoint` attribute on the OAuth2 credential provider tells AgentCore identity to reach the IdP through VPC Lattice instead of the public internet:

- **AgentCore-managed mode**: provide `managedVpcResource` with VPC ID and subnet IDs — AgentCore creates the Lattice resources for you.
- **Self-managed mode**: provide `selfManagedLatticeResource` with the `rcfg-xxx` resource configuration ID from your CDK-deployed Lattice resources.

## What Gets Deployed

### CDK Stacks

| Stack | Resources | Always Deployed? |
|-------|-----------|-----------------|
| **PrivateIdpVpcStack** | VPC with public/private subnets (2 AZs, 1 NAT gateway) | Yes |
| **PrivateIdpPingFederateStack** | ECR repo, ECS Fargate service, internal ALB, Route 53 private hosted zone, Lambda custom resource (configures PingFederate OAuth/OIDC), Secrets Manager | Yes |
| **PrivateIdpGatewayInfraStack** | MCP Echo Lambda (gateway target), IAM role for the gateway | Yes |
| **PrivateIdpLatticeStack** | VPC Lattice resource gateway + resource configuration | Only with `--self-managed-lattice` |

### Manual Steps (after CDK deployment)

1. **Credential provider** — created via AWS CLI with `privateEndpoint` configuration
2. **gateway** — created via AWS CLI with CUSTOM_JWT auth and `privateEndpoint` for JWKS validation
3. **gateway target** — MCP Echo Lambda added as a gateway target via AWS CLI
4. **runtime** — deployed via [agentcore-cli](https://github.com/aws/agentcore-cli) using the code in `agent/`

## Prerequisites

- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) v2.27+
- [AWS CDK](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html) v2 (`npm install -g aws-cdk`)
- [uv](https://docs.astral.sh/uv/) for Python dependency management
- [Python 3.12+](https://www.python.org/downloads/)
- [Docker](https://docs.docker.com/get-docker/) (for building/pushing the PingFederate container image)
- [agentcore-cli](https://github.com/aws/agentcore-cli) (`npm install -g @aws/agentcore@0.30.0`)
- [Node.js 20+](https://nodejs.org/) (for agentcore-cli and CDK)
- **PingFederate DevOps credentials** — [sign up here](https://devops.pingidentity.com/get-started/devopsRegistration/)
- **A publicly trusted ACM certificate** — AgentCore identity requires a publicly trusted TLS certificate to connect via VPC Lattice. The ALB itself remains internal.
- AWS account with permissions to create VPC, ECS, VPC Lattice, Route 53, AgentCore identity, and AgentCore runtime resources

## Setup

### 1. Configure

```bash
cd 06-workshops/03-AgentCore-identity/08-IDP-examples/PingFederate
```

Create a `.env` file:

```bash
cat <<EOF > .env
PING_IDENTITY_DEVOPS_USER=your-email@example.com
PING_IDENTITY_DEVOPS_KEY=your-devops-key
CERTIFICATE_ARN=arn:aws:acm:us-east-1:123456789012:certificate/abc-123
PING_DOMAIN=ping.example.com
EOF
```

| Variable | Description |
|----------|-------------|
| `PING_IDENTITY_DEVOPS_USER` | PingFederate DevOps email |
| `PING_IDENTITY_DEVOPS_KEY` | PingFederate DevOps key |
| `CERTIFICATE_ARN` | ARN of a **publicly trusted** ACM certificate for your domain |
| `PING_DOMAIN` | Domain name matching the certificate (e.g., `ping.example.com`) |

The deployment region is determined by `AWS_REGION` in your shell environment (or your AWS CLI default region). If `AWS_REGION` is not set, it defaults to `us-east-1`.

### 2. Deploy infrastructure

```bash
./deploy_sample.sh                    # AgentCore-managed Lattice (default)
./deploy_sample.sh --self-managed-lattice  # Self-managed Lattice
```

The deployment takes approximately 15–20 minutes. The script will:
1. Validate prerequisites
2. Install Python dependencies
3. Bootstrap CDK
4. Deploy all stacks
5. Output the AWS CLI commands to create the credential provider, gateway, and gateway target

### 3. Create the AgentCore identity credential provider

After deployment, the script outputs the exact AWS CLI command. Choose based on your deployment mode:

**AgentCore-managed mode** (default):

```bash
aws bedrock-agentcore-control create-oauth2-credential-provider \
    --name "ping-private-idp" \
    --credential-provider-vendor "CustomOauth2" \
    --oauth2-provider-config-input '{
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {
                "discoveryUrl": "https://ping.example.com/.well-known/openid-configuration"
            },
            "clientId": "agentcore-client",
            "clientSecret": "agentcore-test-secret-12345",
            "privateEndpoint": {
                "managedVpcResource": {
                    "vpcIdentifier": "vpc-xxx",
                    "subnetIds": ["subnet-xxx", "subnet-yyy"],
                    "endpointIpAddressType": "IPV4"
                }
            }
        }
    }'
```

**Self-managed mode** (`--self-managed-lattice`):

```bash
aws bedrock-agentcore-control create-oauth2-credential-provider \
    --name "ping-private-idp" \
    --credential-provider-vendor "CustomOauth2" \
    --oauth2-provider-config-input '{
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {
                "discoveryUrl": "https://ping.example.com/.well-known/openid-configuration"
            },
            "clientId": "agentcore-client",
            "clientSecret": "agentcore-test-secret-12345",
            "privateEndpoint": {
                "selfManagedLatticeResource": {
                    "resourceConfigurationIdentifier": "rcfg-xxx"
                }
            }
        }
    }'
```

### 4. Verify the credential provider

Wait ~3 minutes for the credential provider to become READY:

```bash
aws bedrock-agentcore-control get-oauth2-credential-provider \
    --name "ping-private-idp" \
    --query '{name: name, status: status}'
```

### 5. Create the AgentCore gateway

The gateway uses CUSTOM_JWT inbound auth with PingFederate as the token issuer. The `privateEndpoint` tells the gateway to validate JWTs by reaching PingFederate's JWKS endpoint via VPC Lattice (private connectivity).

The deploy script outputs the exact command with your stack values pre-filled. The command uses the IAM role and VPC configuration from the `PrivateIdpGatewayInfraStack`:

```bash
aws bedrock-agentcore-control create-gateway \
    --name "PingGateway" \
    --protocol-type "MCP" \
    --role-arn "GATEWAY_ROLE_ARN"  \
    --authorizer-type "CUSTOM_JWT" \
    --authorizer-configuration '{
        "customJWTAuthorizer": {
            "discoveryUrl": "https://ping.example.com/.well-known/openid-configuration",
            "allowedClients": ["agentcore-client"],
            "privateEndpoint": {
                "managedVpcResource": {
                    "vpcIdentifier": "vpc-xxx",
                    "subnetIds": ["subnet-xxx", "subnet-yyy"],
                    "endpointIpAddressType": "IPV4"
                }
            }
        }
    }' \
    --exception-level "DEBUG"
```

Wait ~2–3 minutes for the gateway to become READY:

```bash
aws bedrock-agentcore-control list-gateways \
    --query 'items[?name==`PingGateway`].{id:gatewayId,status:status,url:gatewayUrl}'
```

### 6. Add the MCP Echo Lambda target

Once the gateway is READY, add the Lambda target. Replace `GATEWAY_ID` with the `gatewayId` from step 5:

```bash
aws bedrock-agentcore-control create-gateway-target \
    --gateway-identifier GATEWAY_ID \
    --name "McpEchoTarget" \
    --target-configuration '{
        "mcp": {
            "lambda": {
                "lambdaArn": "MCP_ECHO_LAMBDA_ARN",
                "toolSchema": {
                    "inlinePayload": [
                        {
                            "name": "get_time",
                            "description": "Get the current UTC time",
                            "inputSchema": { "type": "object", "properties": {}, "required": [] }
                        },
                        {
                            "name": "echo",
                            "description": "Echo a message back",
                            "inputSchema": {
                                "type": "object",
                                "properties": { "message": { "type": "string", "description": "Message to echo" } },
                                "required": ["message"]
                            }
                        }
                    ]
                }
            }
        }
    }' \
    --credential-provider-configurations '[{"credentialProviderType": "GATEWAY_IAM_ROLE"}]'
```

The deploy script outputs this command with the actual Lambda ARN pre-filled.

### 7. Deploy the runtime

The `agent/` directory contains a complete [agentcore-cli](https://github.com/aws/agentcore-cli) project — no scaffolding required. The deploy script automatically configures `aws-targets.json` with your account ID and region.

Before deploying, configure the gateway URL so the agent knows where to send authenticated requests.
Open `agent/private-idp-ping-agent/agentcore/agentcore.json` and add `GATEWAY_URL` to the `envVars` array
in the runtime definition:

```json
"envVars": [
  {
    "name": "GATEWAY_URL",
    "value": "https://YOUR-GATEWAY-ID.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
  }
]
```

Replace `YOUR-GATEWAY-ID` with the `gatewayId` from step 5. The full URL follows the pattern
`https://<gatewayId>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`.

Then deploy:

```bash
cd agent/private-idp-ping-agent
agentcore deploy -y
```

> **Note:** You do **not** need to run `agentcore create`. The project structure and CDK config are already committed. `agentcore deploy` resolves your account and region from your configured AWS credentials.

### 8. Test the runtime

```bash
agentcore invoke --prompt "test"
```

Expected output:

```json
{
  "success": true,
  "claims": {
    "scope": "openid",
    "client_id": "agentcore-client",
    "iss": "https://ping.example.com",
    "iat": 1234567890,
    "exp": 1234575090
  },
  "gateway": {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
      "tools": [
        {
          "name": "McpEchoTarget___echo",
          "description": "Echo a message back"
        },
        {
          "name": "McpEchoTarget___get_time",
          "description": "Get the current UTC time"
        }
      ]
    }
  }
}
```

The runtime:
1. Acquires an OAuth token from PingFederate via AgentCore identity (outbound OAuth over VPC Lattice)
2. Presents the token to AgentCore gateway as a Bearer token (inbound JWT auth)
3. The gateway validates the JWT by fetching PingFederate's JWKS over VPC Lattice (private connectivity)
4. Returns the tools/list response from the MCP Echo Lambda target

## Cleanup

### 1. Delete the gateway and credential provider

```bash
# Delete the gateway (this also deletes its targets)
aws bedrock-agentcore-control delete-gateway --gateway-identifier GATEWAY_ID

# Delete the credential provider
aws bedrock-agentcore-control delete-oauth2-credential-provider \
    --name "ping-private-idp"
```

### 2. Delete the runtime

```bash
cd agent/private-idp-ping-agent
agentcore destroy -y
cd ../..
```

### 3. Destroy CDK stacks

```bash
./cleanup_sample.sh
```

The cleanup script deletes stacks in order: PrivateIdpLatticeStack → PrivateIdpGatewayInfraStack → PrivateIdpPingFederateStack → PrivateIdpVpcStack.

> **Note:** VPC Lattice ENIs (both self-managed and AgentCore-managed) can take up to 8 hours to be released by AWS. If PrivateIdpVpcStack deletion fails, wait and retry with `uv run cdk destroy PrivateIdpVpcStack --force`.

## runtime Project Structure

```
agent/private-idp-ping-agent/
├── agentcore/
│   ├── agentcore.json      # runtime config + credential provider declaration
│   ├── aws-targets.json    # Deployment target (empty — resolved from credentials)
│   ├── .gitignore
│   └── cdk/                # CDK infrastructure (committed, ready to deploy)
└── app/
    └── private-idp-ping-agent/
        ├── main.py         # runtime with @requires_access_token
        └── pyproject.toml  # Python dependencies
```

The runtime uses:
- **`@requires_access_token`** decorator from `bedrock_agentcore.identity` to obtain OAuth tokens via the credential provider
- **`BedrockAgentCoreApp`** from `bedrock_agentcore.runtime` for the runtime lifecycle
- The **`GATEWAY_URL`** environment variable to call the AgentCore gateway with the acquired token

No LLM or agent framework is required — this sample focuses purely on proving private IdP connectivity. The token is handled securely within the decorated function and never exposed beyond it.

> **Note:** The credential provider and gateway are created manually via AWS CLI because the agentcore-cli does not yet support `privateEndpoint` parameters. The `agentcore.json` declares the credential provider name for the runtime to reference.

## How It Works

```python
from bedrock_agentcore.identity import requires_access_token
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

CREDENTIAL_PROVIDER_NAME = "ping-private-idp"
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")

@requires_access_token(
    provider_name=CREDENTIAL_PROVIDER_NAME,
    scopes=["openid"],
    auth_flow="M2M",
)
def fetch_token_from_private_idp(*, access_token: str) -> dict:
    # The decorator handles:
    # 1. Obtaining a workload identity token for this runtime
    # 2. Exchanging it for an OAuth access token via the credential provider
    # 3. The credential provider reaches PingFederate over VPC Lattice
    # 4. Injecting the resulting access_token into this function

    # Use the token to call AgentCore gateway (inbound JWT auth)
    if GATEWAY_URL:
        gateway_result = call_gateway(access_token)
    ...
```

The `@requires_access_token` decorator abstracts the entire token acquisition flow. Your code simply declares which credential provider and scopes it needs — the SDK handles workload identity, the OAuth exchange, and private network routing via VPC Lattice.

The gateway call demonstrates inbound auth — the same PingFederate token is presented as a `Bearer` token to the gateway, which validates it by fetching PingFederate's JWKS over VPC Lattice.

## PingFederate Configuration

During deployment, a Lambda custom resource (`lambda/configure_pingfed/index.py`) running inside the VPC configures PingFederate via the Admin API:

- **RSA Signing Key** for JWT token signing
- **JWT Access Token Manager** using RS256
- **OAuth Authorization Server** with scopes: `openid`, `profile`, `email`
- **OIDC policy** with standard claims
- **OAuth Client** (`agentcore-client`) configured for client credentials grant
- **Server settings** with the correct base URL for OIDC discovery

The client ID (`agentcore-client`) and secret (`agentcore-test-secret-12345`) are defined in `lambda/configure_pingfed/index.py`. For production use, rotate these values and store them securely.

## Cost Considerations

This sample creates resources that incur AWS charges:

| Resource | Approximate Cost |
|----------|-----------------|
| NAT gateway | ~$32/month + data transfer |
| ECS Fargate (2 vCPU, 4 GB) | ~$70/month |
| Application Load Balancer | ~$16/month + LCU |
| VPC Lattice (self-managed only) | Based on data processed |
| EFS | Based on storage used |

**Run `./cleanup_sample.sh` immediately after testing to avoid ongoing charges.**

## Troubleshooting

### "HTTP request failed against private endpoint"

This typically means the discovery URL domain cannot be resolved within the VPC. Verify:
1. The private hosted zone exists and is associated with the VPC
2. The A record in the private zone points to the internal ALB
3. The domain in the discovery URL matches the private hosted zone name

The CDK stack creates the private hosted zone automatically. If you're adapting this for an existing IdP, ensure you have a private hosted zone mapping the IdP's domain to its internal endpoint.

### VPC Stack deletion fails

VPC Lattice ENIs can take up to 8 hours to release. Wait and retry:

```bash
uv run cdk destroy PrivateIdpVpcStack --force
```

To check ENI status:

```bash
VPC_ID=$(aws cloudformation describe-stacks --stack-name PrivateIdpVpcStack \
    --query 'Stacks[0].Outputs[?OutputKey==`VpcId`].OutputValue' --output text)
aws ec2 describe-network-interfaces --filters Name=vpc-id,Values=$VPC_ID
```

## Note

PingFederate is not an AWS service. Please refer to PingIdentity documentation for costs and licensing. The PingFederate container image is pulled from Docker Hub under the PingIdentity DevOps program.
