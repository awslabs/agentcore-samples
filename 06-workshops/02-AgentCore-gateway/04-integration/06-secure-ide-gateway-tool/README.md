# VS Code + AgentCore Gateway: Secure IDE Tool Access with Figma

## Overview

This sample connects an IDE — **Visual Studio Code** (with GitHub Copilot) or **Kiro** — to **Amazon Bedrock AgentCore Gateway**, giving it access to **Figma** as an MCP tool with **OAuth 2.0 three-legged authorization (3LO)** for user-delegated access.

A serverless proxy layer (API Gateway + Lambda) sits between VS Code and the AgentCore Gateway. No local servers are required — developers configure a single URL in VS Code and authenticate through the browser.

**Requires**: `MCP-Protocol-Version: 2025-11-25` (adds URL elicitation support).

## Architecture

![VS Code + AgentCore Gateway Serverless OAuth Proxy](generated-diagrams/vscode-agentcore-serverless-proxy.png)

**Flow summary:**
1. VS Code connects to the API Gateway endpoint via MCP/HTTP
2. The IDP Lambda serves OAuth metadata and a login page; the user authenticates against Cognito
3. The MCP Proxy Lambda forwards authenticated tool requests to AgentCore Gateway
4. When Figma access is needed, the gateway returns a 3LO elicitation (`-32042`)
5. The proxy rewrites the elicitation URL so the callback routes through our API Gateway
6. The user grants consent in the browser via Figma OAuth
7. The Callback Lambda receives the authorization code and calls `CompleteResourceTokenAuth`
8. AgentCore Gateway can now call the Figma API on behalf of the user

## Two OAuth Flows

This sample involves two independent OAuth flows:

| Flow | Purpose | Direction | When |
|------|---------|-----------|------|
| **Inbound Auth** | VS Code authenticates to the proxy | VS Code &rarr; Cognito &rarr; Proxy | On MCP server connection |
| **Outbound Auth (3LO)** | AgentCore accesses Figma on behalf of the user | AgentCore &rarr; Figma &rarr; User consent | On first Figma tool call |

Cognito handles only inbound auth. The 3LO tokens for Figma are managed entirely by AgentCore Identity.

### Token Lifetime and Consent Persistence

AgentCore Identity manages 3LO tokens automatically: after the user completes consent, AgentCore stores the access token and refresh token and refreshes transparently on expiration. Re-consent is required only if the user revokes access in Figma, the refresh token expires from inactivity, or the app's requested scopes change.

## Components

| Component | Purpose | Source |
|-----------|---------|--------|
| **API Gateway** (HTTP API) | Public HTTPS endpoint for VS Code | [cdk-stack.ts](cdk/lib/cdk-stack.ts) |
| **IDP Lambda** | OAuth authorization server facade (metadata, login, token, DCR) | [idp_lambda.py](lambda/idp_lambda.py) |
| **MCP Proxy Lambda** | Forwards MCP requests to AgentCore Gateway, rewrites elicitation URLs | [mcp_lambda.py](lambda/mcp_lambda.py) |
| **Callback Lambda** | 3LO callback handling, `CompleteResourceTokenAuth`, session verification | [callback_lambda.py](lambda/callback_lambda.py) |
| **Cognito User Pool** | JWT tokens for inbound authentication | CDK |
| **AgentCore Gateway** | AWS-managed MCP gateway with Figma target | CDK + notebook |
| **DynamoDB Table** | Short-lived auth codes and elicitation sessions | CDK |

**Note on terminology**: "API Gateway" refers to Amazon API Gateway (the HTTP API fronting the Lambdas). "AgentCore Gateway" refers to the AWS-managed MCP server that routes tool calls to Figma.

## Design Choices

### Why a Proxy Layer Is Needed

VS Code's MCP client expects standard OAuth endpoints (`/.well-known/oauth-authorization-server`, `/authorize`, `/token`) at the MCP server URL. AgentCore Gateway validates incoming JWTs but does not act as an OAuth Authorization Server. The proxy provides this facade:

- **IDP Lambda** proxies the OAuth authorization flow to Cognito while serving a custom login page, handling PKCE validation, issuing authorization codes, and serving RFC 9728 Protected Resource Metadata (`/.well-known/oauth-protected-resource`) — the `resource` identifier must match the URL the client connects to (the proxy URL), not the underlying gateway URL.
- **MCP Proxy Lambda** adds the `Authorization` header and forwards requests to the gateway. On a `401` from the gateway it rewrites the `resource_metadata` URL in the `WWW-Authenticate` header so it points back at our API Gateway instead of the gateway's own domain.
- **Callback Lambda** handles the 3LO redirect: when the user completes Figma consent, the OAuth callback must be received server-side so that `CompleteResourceTokenAuth` can be called with the correct user identity.

### Single DynamoDB Table for Auth Codes and Elicitation Sessions

Both the IDP auth codes and the 3LO elicitation sessions share a single DynamoDB table. This is intentional — both record types have the same shape and lifecycle:

- **Short-lived**: 5-minute TTL
- **Single-use**: consumed via `delete_item` with `ConditionExpression` (atomic delete-and-read)
- **Keyed by an opaque string** as the partition key

The `elicitation:` prefix on elicitation keys (e.g., `elicitation:urn:ietf:params:oauth:request_uri:...`) acts as a namespace, preventing collisions with UUID-based auth codes. Splitting into two tables would double the IAM grants (across 3 Lambdas), environment variables, and CDK resources for no practical benefit.

**IDP auth codes** (written by `idp_lambda.py`, consumed by `idp_lambda.py`):
```
{code: "uuid", access_token: "...", id_token: "...", code_challenge: "...", ttl: now+300}
```

**Elicitation sessions** (written by `mcp_lambda.py`, consumed by `callback_lambda.py`):
```
{code: "elicitation:{session_id}", user_token: "...", ttl: now+300}
```

### Cookie-Based Session for the Callback Flow

When the user completes 3LO consent, Figma redirects to `/oauth2/callback` on our API Gateway. The Callback Lambda needs to know *which user* initiated the flow so it can call `CompleteResourceTokenAuth` with the correct identity. Two mechanisms work together:

1. **DynamoDB lookup**: The MCP Proxy Lambda stores the user's bearer token keyed by elicitation session ID when it rewrites the elicitation URL. The Callback Lambda reads and deletes this entry.
2. **Cookie verification**: The Callback Lambda also reads the user's `access_token` cookie (set during login), verifies the JWT signature against Cognito's JWKS, and checks that the `sub` claim matches the stored token's `sub`. This prevents a user from completing another user's 3LO flow.

If the cookie is missing or expired, the Callback Lambda redirects to `/authorize` with a `return_to` parameter so the user can re-authenticate and resume the callback.

### Custom Login Page Instead of Cognito Hosted UI

The IDP Lambda serves its own login page rather than redirecting to the Cognito Hosted UI. This allows:

- Setting `HttpOnly; Secure; SameSite=Lax` cookies for `access_token` and `refresh_token` in the login response, which are needed later by the Callback Lambda
- Handling the `NEW_PASSWORD_REQUIRED` challenge inline (Cognito creates users with temporary passwords)
- Keeping the entire flow on the same origin (API Gateway domain), avoiding cross-origin cookie issues

### Dynamic Client Registration (DCR)

The IDP Lambda implements the `/register` endpoint (RFC 7591). VS Code's MCP client calls this to discover the `client_id` before starting the OAuth flow. The implementation is a thin passthrough — it returns the pre-configured Cognito app client ID rather than creating new clients, since all VS Code instances share the same public client.

### Elicitation URL Rewriting

When AgentCore Gateway returns a `-32042` elicitation error (requesting user consent), the elicitation URL points to `bedrock-agentcore.{region}.amazonaws.com`. The MCP Proxy Lambda rewrites this URL to route the OAuth callback through our API Gateway's `/oauth2/callback` endpoint instead. This is necessary because:

1. The callback needs to happen server-side (Lambda calls `CompleteResourceTokenAuth`)
2. The callback needs access to the user's session (cookie + DynamoDB lookup)
3. Figma's DCR (Dynamic Client Registration) at the MCP endpoint registers our API Gateway callback URL as the `redirect_uri`

### PKCE Validation

The IDP Lambda implements PKCE (RFC 7636) with S256 challenge method. VS Code's MCP client sends a `code_challenge` during `/authorize` and a `code_verifier` during `/token`. The auth code stored in DynamoDB includes the challenge, and the token endpoint verifies the verifier against it before returning tokens. This prevents authorization code interception attacks, which is important since the VS Code client is a public client (no client secret).

### redirect_uri Allowlist

`/authorize` and `/token` validate `redirect_uri` against an allowlist stored in an SSM `StringList` parameter (`/agentcore-figma/redirect-uri-allowlist`), rather than accepting any value the caller supplies. Without this check, an attacker could send a victim a crafted `/authorize` link pointing at an off-site `redirect_uri`; the victim would log in on the genuine IDP page and their authorization code would be delivered to the attacker's server instead of back to VS Code. PKCE alone does not stop this, since the attacker can generate their own `code_verifier`/`code_challenge` pair for the crafted link.

`/token` also requires and validates `client_id` (this deployment only provisions one public client, so this is an exact match), and re-checks that any `redirect_uri` supplied at `/token` matches what was used at `/authorize`. See [Managing the redirect_uri allowlist](#managing-the-redirecturi-allowlist) below for how to update it.

## Infrastructure (CDK)

The infrastructure is defined in [cdk/lib/cdk-stack.ts](cdk/lib/cdk-stack.ts) and deploys:

- Cognito User Pool with two app clients:
  - **VS Code client** — authorization code grant, no secret (public client), with PKCE
  - **M2M client** — client credentials grant, with secret (for testing)
- Resource Server with `mcp.read` and `mcp.write` scopes
- DynamoDB table with TTL (`ttl` attribute)
- Three Lambda functions with minimal IAM roles
- HTTP API Gateway with routes for OAuth, MCP proxy, and callbacks
- AgentCore Gateway with Cognito JWT authorizer

The Figma credential provider and gateway target are created via the [setup notebook](01_gateway_secure_3lo_auth.ipynb) after CDK deployment, because they require interactive steps (Figma DCR registration, fetching the Figma MCP tool schemas).

## Setup

### Prerequisites

- Node.js 18+ and pnpm (for CDK)
- Python 3.10+ (for the notebook and Lambda code)
- Docker (required for Lambda bundling during `cdk deploy`)
- AWS credentials with permissions for Lambda, API Gateway, Cognito, IAM, DynamoDB, and Bedrock AgentCore
- Figma account
- VS Code 1.107+ with GitHub Copilot, or Kiro IDE

### Step 1: Deploy the CDK Stack

```bash
cd cdk
pnpm install
pnpm cdk deploy
```

Copy the stack outputs — you will need them in the next step.

### Step 2: Run the Setup Notebook

Open [01_gateway_secure_3lo_auth.ipynb](01_gateway_secure_3lo_auth.ipynb) and follow the steps:

1. Paste the CDK stack outputs into the config cell
2. Create a Cognito test user (`vscode-user@example.com`)
3. Create the Figma credential provider with placeholder credentials, complete Figma's Dynamic Client Registration using the provider's callback URL as the redirect URI, then update the credential provider with the real `client_id`/`client_secret` returned by Figma
4. Obtain a short-lived Figma access token via a local, one-off OAuth consent (`USER_FEDERATION` flow on `http://localhost:8085`) — this is only used to call the Figma MCP server and fetch its tool schemas, it is not the end-user 3LO flow
5. Create the gateway target pointing to `https://mcp.figma.com/mcp`, using the fetched tool schemas and a `defaultReturnUrl` of `<ApiEndpoint>oauth2/callback` so future end-user consent is completed by the Callback Lambda
6. Print the VS Code and Kiro IDE MCP configuration

### Step 3: Configure Your IDE

VS Code — add to `.vscode/mcp.json` (values from CDK output):

```json
{
  "servers": {
    "figma-agentcore": {
      "type": "http",
      "url": "https://<api-gateway-id>.execute-api.<region>.amazonaws.com/mcp",
      "headers": {
        "MCP-Protocol-Version": "2025-11-25"
      }
    }
  }
}
```

Kiro IDE — add to your MCP config:

```json
{
  "mcpServers": {
    "figma-agentcore": {
      "url": "https://<api-gateway-id>.execute-api.<region>.amazonaws.com/mcp",
      "headers": {
        "MCP-Protocol-Version": "2025-11-25"
      },
      "disabled": false
    }
  }
}
```

### Step 4: Connect and Use

1. Reload your IDE
2. When prompted, sign in with the Cognito user credentials
3. Use Figma tools — 3LO consent will be triggered on first use
4. After granting Figma consent in the browser, retry the tool call

## Troubleshooting

### "Cannot initiate authorization code grant flow"
The gateway is not receiving the `MCP-Protocol-Version: 2025-11-25` header. Add `"headers": {"MCP-Protocol-Version": "2025-11-25"}` to your `mcp.json` config.

### "redirect_mismatch" from Cognito
The callback URL is not registered in the Cognito app client. Verify the CDK stack deployed correctly and the callback URLs include your API Gateway endpoint.

### Lambda timeout errors
Increase the Lambda timeout in CDK or check that the AgentCore Gateway target is in `ACTIVE` status (not `FAILED`).

### 3LO completed but tool still fails
VS Code does not auto-retry after 3LO completion. Invoke the tool again after completing consent in the browser.

### "Session Expired" on callback page
The elicitation session entry in DynamoDB has a 5-minute TTL. If the user takes too long to complete Figma consent, the session expires. Retry the tool call to generate a new elicitation.

## Files

| File | Description |
|------|-------------|
| [cdk/lib/cdk-stack.ts](cdk/lib/cdk-stack.ts) | CDK stack — all AWS infrastructure |
| [lambda/idp_lambda.py](lambda/idp_lambda.py) | IDP Lambda — OAuth endpoints, login page, PKCE |
| [lambda/mcp_lambda.py](lambda/mcp_lambda.py) | MCP Proxy Lambda — gateway forwarding, elicitation rewriting |
| [lambda/callback_lambda.py](lambda/callback_lambda.py) | Callback Lambda — 3LO completion, session verification |
| [01_gateway_secure_3lo_auth.ipynb](01_gateway_secure_3lo_auth.ipynb) | Setup notebook — credential provider, Figma DCR, gateway target, IDE config |
| [utils.py](utils.py) | Optional helper functions (Cognito user pool / resource server / M2M client setup, IAM role creation, gateway and pool cleanup) — not called by the current notebook, kept for scripting ad hoc setups |
| [SECURITY-REVIEW.md](SECURITY-REVIEW.md) | Security review notes from an earlier iteration of this sample; several findings have since been addressed (see below) |

## Security

This sample handles OAuth tokens end-to-end (inbound Cognito auth, outbound Figma 3LO), so review [SECURITY-REVIEW.md](SECURITY-REVIEW.md) before adapting it for production use. The current implementation addresses the localStorage, unverified-JWT, token-in-URL, IAM over-permissioning, and missing-redirect-uri-validation findings from that review — tokens live in `HttpOnly` cookies, the Callback Lambda verifies JWT signatures against Cognito's JWKS, the Lambda roles are scoped to specific AgentCore/Secrets Manager/KMS resource ARNs instead of `Resource: "*"`, and `/authorize`/`/token` validate `redirect_uri` against a managed allowlist plus `client_id`. There is still no rate limiting on the auth endpoints (`/login`, `/token`, `/authorize`) — add API Gateway throttling or a WAF rule in front of the HTTP API before running this outside a personal/test account.

### Managing the redirect_uri allowlist

The allowlist lives in the SSM `StringList` parameter output as `RedirectUriAllowlistParamOutput` (default path: `/agentcore-figma/redirect-uri-allowlist`). It's pre-populated at deploy time with the same redirect URIs already trusted by the VS Code Cognito app client: the loopback ports VS Code's built-in OAuth client uses (`http://127.0.0.1:33418`, `http://localhost:33418`), this stack's own `/callback` endpoint, and the hosted `vscode.dev`/`insiders.vscode.dev` redirects.

To view the current allowlist:

```bash
aws ssm get-parameter --name /agentcore-figma/redirect-uri-allowlist \
  --query 'Parameter.Value' --output text
```

To add a redirect URI (e.g. for a different IDE or a custom loopback port), fetch the current list, append the new value, and write it back — `put-parameter` on a `StringList` replaces the whole value, it does not append:

```bash
CURRENT=$(aws ssm get-parameter --name /agentcore-figma/redirect-uri-allowlist --query 'Parameter.Value' --output text)
aws ssm put-parameter --name /agentcore-figma/redirect-uri-allowlist \
  --type StringList --overwrite \
  --value "$CURRENT,https://your-new-redirect-uri"
```

Changes take effect on the IDP Lambda's next cold start (the value is cached for the lifetime of the Lambda execution environment, generally under 15 minutes). To force an immediate refresh, redeploy the Lambda or wait for it to recycle.

Because `cdk deploy` sets the parameter's value declaratively from the `callbackUrls` list in `cdk-stack.ts`, any manual changes made via `aws ssm put-parameter` will be overwritten on the next `cdk deploy`. To persist a redirect URI permanently, add it to the `callbackUrls` array in [cdk/lib/cdk-stack.ts](cdk/lib/cdk-stack.ts) instead (this also keeps it in sync with the Cognito app client's own callback URL list) and redeploy.

## Cleanup

1. Run the cleanup cell in the notebook to delete the Figma credential provider and gateway target
2. Destroy the CDK stack:
   ```bash
   cd cdk
   pnpm cdk destroy
   ```

## References

- [MCP Specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- [VS Code MCP Documentation](https://code.visualstudio.com/docs/copilot/customization/mcp-servers)
- [AgentCore Gateway Documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
- [Figma MCP Server](https://mcp.figma.com)
- [RFC 7636 — PKCE](https://datatracker.ietf.org/doc/html/rfc7636)
- [RFC 7591 — Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591)
- [RFC 9728 — Protected Resource Metadata](https://datatracker.ietf.org/doc/html/rfc9728)
