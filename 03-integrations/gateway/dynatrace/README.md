# Discover Dynatrace tools through AgentCore Gateway

Keep two trust boundaries separate: the developer’s MCP client authenticates **to Gateway** with a short-lived Cognito token; Gateway uses an **AgentCore Identity OAuth provider** to authenticate to the [remote MCP server in your Dynatrace tenant](https://docs.dynatrace.com/docs/dynatrace-intelligence/dynatrace-mcp). This is tool discovery, not the [separate Dynatrace telemetry exporter](../../3p-observability/dynatrace/).

![Gateway and Dynatrace MCP server](images/dynatrace-mcp-server-target.png)

## Before you start

Use the [shared Gateway setup and cleanup walkthrough](../../../02-use-cases/03-coding-assistants/claude-code-gateway-mcp-server/README.md). It uses boto3 and creates a dedicated Gateway and **one** target. No Python Starter Toolkit, Strands agent or model invocation is required to provision it. If you use Claude Code as the MCP client, its normal authentication and charges apply.

In addition to the shared walkthrough's inbound Cognito settings, have the owner of your Dynatrace tenant and AWS account provide:

- `DYNATRACE_TENANT_ID`: the single tenant label from the approved `https://<tenant>.apps.dynatrace.com` environment. Other domains and self-hosted MCP endpoints are outside this walkthrough.
- `DYNATRACE_PROVIDER_ARN`: an **existing AgentCore Identity OAuth2 credential provider ARN** in the Gateway's account and Region, configured for Dynatrace and the **client credentials** grant. Use the [Dynatrace OAuth client guide](https://docs.dynatrace.com/docs/manage/identity-access-management/access-tokens-and-oauth-clients/oauth-clients) with the tenant owner. This is not a Cognito ARN or a Dynatrace API token. A tenant using only interactive authorization-code flow needs a different outbound policy; don't substitute a developer's session token.
- A Gateway service role authorized to retrieve the credential for **that specific provider and its backing secret**. Review [Gateway outbound authorization](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-outbound-auth.html) with the owner. The sample does not create or modify this role, the provider, or its secret.

Verify the **tenant, provider, and role together** before deploying: even a valid Dynatrace hostname can be the wrong tenant. Never put a client secret or an access token in these settings. The target requests only `mcp-gateway:servers:read` and `mcp-gateway:servers:invoke` for discovery. Reading actual problems, metrics or logs may need additional tenant permissions; this tutorial does not request broad storage or write scopes.

## Create, discover, clean up

1. Follow **Create a dedicated project** in the shared walkthrough, starting in its directory, not in this one. Export the two non-secret Dynatrace settings above. At the create step, use `.venv/bin/python "$sample/gateway.py" create --target dynatrace` **instead of** the default AWS Knowledge command. Do not add this target to a Gateway shared with another application.
2. Follow **Connect and search**. The project-local MCP entry points at your Gateway, never directly at the tenant; the client sees only its inbound Cognito token. Inspect the tool list and try **“Find the available Dynatrace tools for investigating problems; describe them without invoking them.”** You can also use the [Gateway semantic search tool](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-semantic-search.html) with an authenticated MCP client of your choice. Review the tool and required scopes before allowing any tenant operation.
3. Follow **Cleanup** in the shared walkthrough. Only the recorded target and Gateway are removed. The approved IAM role, Cognito resources, Identity provider, and Dynatrace client remain with their owners. The new receipt-based cleanup does not adopt deployments created with the older notebook.
