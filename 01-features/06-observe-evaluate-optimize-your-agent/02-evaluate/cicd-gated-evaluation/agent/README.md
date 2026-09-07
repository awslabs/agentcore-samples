# Agent

Strands-based assistant agent deployed as an AgentCore HTTP runtime.

## How it works

The agent combines a built-in tool with MCP server tools:

**Built-in tools:**
- `calculator` — math operations (from strands-agents-tools)

**MCP tools (from the MCP server):**
- `get_current_datetime` — current date/time in any IANA timezone (public)
- `get_stock_price` — mock stock prices (requires `FinanceUser` role or `mcp/finance` scope)
- `get_employee_count` — mock department headcounts (requires `HRUser` role or `mcp/hr` scope)

## Token forwarding

- **User tokens:** If the incoming request has a JWT with a `sub` claim (user token), the agent forwards it to the MCP server so role-based access is enforced.
- **M2M tokens:** For CI pipeline calls, the agent can mint a shared M2M client-credentials token to call the MCP server. Such a token is authorized by the scopes it carries, so `MCP_OAUTH_SCOPE` must name each tool domain the agent needs; it grants no blanket access.

## Environment variables

> **Important:** AgentCore Runtime requires ARM64 container images. The Dockerfile uses `--platform=linux/arm64`. If building on x86_64 (e.g., GitHub Actions runners), you need QEMU + Docker Buildx for cross-compilation. The GitHub Actions workflow handles this with `docker/setup-qemu-action` and `docker/setup-buildx-action`.

| Variable | Description |
|---|---|
| `MODEL_ID` | Bedrock model ID (default: `au.anthropic.claude-haiku-4-5-20251001-v1:0`) |
| `MCP_SERVER_ARN` | ARN of the MCP server AgentCore runtime |
| `MCP_OAUTH_SCOPE` | OAuth scope requested for the M2M token (default: `mcp/invoke`); must name each tool domain the agent needs (e.g. `mcp/invoke mcp/finance mcp/hr`) |
| `MCP_CLIENT_ID` | Cognito M2M client ID |
| `MCP_CLIENT_SECRET` | Cognito M2M client secret |
| `MCP_TOKEN_ENDPOINT` | Cognito token endpoint URL |
| `SECRET_ARN` | Secrets Manager ARN holding `client_id`/`client_secret`/`token_endpoint`; used as a fallback when `MCP_CLIENT_SECRET` is unset |
| `GUARDRAIL_ID` | Bedrock Guardrail ID; a guardrail is applied only when both this and `GUARDRAIL_VERSION` are set |
| `GUARDRAIL_VERSION` | Published Bedrock Guardrail version (paired with `GUARDRAIL_ID`) |
| `AWS_DEFAULT_REGION` | AWS region (default: `ap-southeast-2`) |
