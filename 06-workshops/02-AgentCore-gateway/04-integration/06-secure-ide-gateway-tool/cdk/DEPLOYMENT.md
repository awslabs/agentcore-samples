# Secure IDE Gateway Tool (Figma) — CDK Deployment Guide

This CDK stack deploys the serverless OAuth proxy that connects an IDE (VS Code or Kiro)
to an **Amazon Bedrock AgentCore Gateway** exposing **Figma** as an MCP tool, with OAuth 2.0
three-legged authorization (3LO) for user-delegated access. No local proxy servers are required.

This guide covers the CDK deployment mechanics. For the end-to-end architecture, OAuth flows,
and design rationale, see the [sample README](../README.md).

## What the stack creates

Defined in [lib/cdk-stack.ts](lib/cdk-stack.ts):

1. **Cognito User Pool** — inbound JWT authentication, with two app clients:
   - VS Code client — authorization code grant, public (no secret), PKCE
   - M2M client — client credentials grant, with secret (for testing)
   - Resource server with `mcp.read` / `mcp.write` scopes
2. **Three Lambda functions** — IDP (`idp_lambda.py`), MCP Proxy (`mcp_lambda.py`), and Callback (`callback_lambda.py`), each with a least-privilege IAM role
3. **HTTP API Gateway** — public HTTPS endpoint fronting the Lambdas
4. **DynamoDB table** — short-lived (5-minute TTL) auth codes and elicitation sessions
5. **AgentCore Gateway** — AWS-managed MCP gateway with a Cognito JWT authorizer
6. **SSM parameters** — client id, callback URL, gateway URL, and the `redirect_uri` allowlist

The Figma credential provider and the gateway target are created **after** deployment via the
[setup notebook](../01_gateway_secure_3lo_auth.ipynb), since they require interactive steps
(Figma Dynamic Client Registration and fetching the Figma MCP tool schemas).

## Prerequisites

- AWS CLI configured with credentials that can manage Lambda, API Gateway, Cognito, IAM, DynamoDB, SSM, and Bedrock AgentCore
- Node.js 18+ and [pnpm](https://pnpm.io/)
- AWS CDK CLI (`pnpm add -g aws-cdk`, or use the bundled `pnpm cdk`)
- Docker (required for Lambda bundling during `cdk deploy`)

## Installation

From this `cdk/` directory:

```bash
cd 06-workshops/02-AgentCore-gateway/04-integration/06-secure-ide-gateway-tool/cdk
pnpm install
```

Bootstrap CDK (first time per account/region only):

```bash
pnpm cdk bootstrap
```

## Deployment

```bash
pnpm cdk deploy
```

The stack name is `FigmaMCP`. Copy the stack outputs — the setup notebook needs them.

### Stack outputs

| Output | Description |
| --- | --- |
| `ApiEndpoint` | API Gateway URL the IDE connects to (append `mcp` for the MCP endpoint) |
| `UserPoolId` / `UserPoolArn` | Cognito User Pool identifiers |
| `CognitoDomain` / `CognitoDomainUrl` | Cognito hosted domain |
| `DiscoveryUrl` | OIDC discovery URL |
| `VSCodeClientId` | Public client ID for the IDE OAuth flow |
| `M2MClientId` | Client ID for machine-to-machine testing |
| `IdpLambdaName` / `McpLambdaName` / `CallbackLambdaName` | Lambda function names |
| `Gateway` | AgentCore Gateway ID |
| `VSCodeMcpConfig` | Ready-to-use MCP configuration JSON |
| `RedirectUriAllowlistParamOutput` | SSM parameter holding the OAuth `redirect_uri` allowlist |

## Post-deployment

The Cognito test user, Figma credential provider, and gateway target are all created by the
[setup notebook](../01_gateway_secure_3lo_auth.ipynb) — run it after `cdk deploy` and paste the
stack outputs into its config cell. The notebook then prints the VS Code and Kiro MCP configuration.

Unlike earlier versions of this sample, you do **not** need to manually create the Cognito user,
wire up `GATEWAY_URL`, or create the gateway — the stack creates the gateway and wires the Lambda
environment via SSM parameters, and the notebook handles the Figma-specific setup.

## API Gateway routes

| Method | Path | Handler | Description |
| --- | --- | --- | --- |
| GET | `/.well-known/oauth-authorization-server` | IDP | OAuth server metadata |
| GET | `/.well-known/oauth-protected-resource` | IDP | RFC 9728 protected-resource metadata |
| GET | `/authorize` | IDP | OAuth authorization + login page |
| POST | `/token` | IDP | Token exchange (PKCE + `redirect_uri`/`client_id` validation) |
| POST | `/register` | IDP | Dynamic Client Registration (RFC 7591) |
| POST | `/login` | IDP | Login form submission |
| ANY | `/mcp` | MCP Proxy | Forwards MCP requests to AgentCore Gateway |
| GET | `/ping` | Callback | Health check |
| GET, POST | `/oauth2/callback` | Callback | 3LO callback → `CompleteResourceTokenAuth` |

## Testing

```bash
API_ENDPOINT="<ApiEndpoint from output>"

# OAuth server metadata
curl "$API_ENDPOINT.well-known/oauth-authorization-server"

# Health check
curl "$API_ENDPOINT/ping"
```

Then configure your IDE with the `VSCodeMcpConfig` output (see the [sample README](../README.md)
for VS Code and Kiro config snippets), reload the IDE, sign in with the Cognito user, and invoke a
Figma tool — 3LO consent triggers on first use.

## Cleanup

1. Run the cleanup cell in the notebook to delete the Figma credential provider and gateway target.
2. Destroy the stack:

   ```bash
   pnpm cdk destroy
   ```

CloudWatch log groups are not deleted automatically — remove them manually if desired.

## Troubleshooting

- **Lambda errors** — `aws logs tail /aws/lambda/<function-name> --follow`
- **`redirect_mismatch` from Cognito** — the callback URL isn't registered on the Cognito app client; confirm the stack deployed cleanly.
- **Lambda timeouts** — check the AgentCore Gateway target is `ACTIVE` (not `FAILED`).
- **3LO completed but tool still fails** — the IDE does not auto-retry; invoke the tool again after consent.

See the [sample README](../README.md) troubleshooting section for more, including the
`MCP-Protocol-Version: 2025-11-25` header requirement and the `redirect_uri` allowlist.

## References

- [Sample README](../README.md) — full architecture and OAuth flow details
- [Setup notebook](../01_gateway_secure_3lo_auth.ipynb)
- [AgentCore Gateway documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
- [MCP Specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
