# Agent — Strands on AgentCore Runtime (Okta)

The Strands agent for Use Case 2 (Okta variant). Performs **OBO #1** (T_user -> T_gateway) using Okta's RFC 8693 Token Exchange grant, then talks to the AgentCore Gateway over MCP.

## What it does, line by line

```python
@app.entrypoint
async def invoke(payload, context):
    user_jwt = context.request_headers["Authorization"].split(" ", 1)[1]

    # OBO #1: T_user -> T_gateway via Okta Token Exchange.
    gateway_token = _obo_user_to_gateway(user_jwt)

    # Connect to Gateway with T_gateway as the Bearer.
    with _gateway_mcp_client(gateway_token) as mcp_client:
        tools = mcp_client.list_tools_sync()       # tools/list
        agent = Agent(model=..., tools=tools)
        async for event in agent.stream_async(prompt):
            yield event["data"]
```

The Gateway then transparently runs OBO #2 (also Token Exchange) and calls the mock downstream API. The agent never touches the downstream and never sees T_downstream.

## What it does NOT do (vs Use Case 1's Okta agent)

- It does **not** import `requests` or call the downstream URL directly.
- It does **not** define a `@tool` for `callDownstreamApi` — that tool comes from the Gateway, not from local Python.
- The LLM never sees T_downstream (and the agent doesn't see one either).

## Okta-specific parameters on `get_resource_oauth2_token`

Both bits are required for the exchange to succeed against Okta:

| Argument | Value | Why |
|---|---|---|
| `customParameters` | `{"subject_token_type": "urn:ietf:params:oauth:token-type:access_token"}` | RFC 8693 requires the client to declare what kind of token is being exchanged. Okta returns HTTP 400 without it. AgentCore Identity does not auto-add it for CustomOauth2 providers. |
| `audiences` | `[OKTA_AUDIENCE]` (typically `api://default`) | Okta's default auth server mints every token with this audience. Missing this can produce tokens with the wrong `aud` and the Gateway rejects them. |
| `scopes` | `[GATEWAY_SCOPE]` (`gateway.access`) | Custom scope defined on the default auth server. Okta refuses OIDC scopes (`openid`, `profile`, `email`) on Token Exchange, so a custom scope is required. |
| `oauth2Flow` | `ON_BEHALF_OF_TOKEN_EXCHANGE` | Tells AgentCore Identity to use OBO semantics (as opposed to `USER_FEDERATION` for the initial sign-in exchange). |

## Required environment variables

Set by `deploy/03_patch_agentcore_json.py` and read at startup:

| Var | Purpose |
|---|---|
| `AGENT_WORKLOAD_NAME` | The AgentCore workload identity name; used by `GetWorkloadAccessTokenForJWT`. |
| `AGENT_OBO_PROVIDER_NAME` | Credential provider name used for OBO #1 (auths as AgentApp). |
| `GATEWAY_SCOPE` | Scope requested in OBO #1 (`gateway.access`). |
| `GATEWAY_MCP_URL` | The Gateway's MCP endpoint. |
| `OKTA_AUDIENCE` | Passed as `audiences=[...]` on the exchange call. Typically `api://default`. |
| `AWS_REGION` | Region for the boto3 `bedrock-agentcore` client. |

## Required IAM permissions on the agent's execution role

`deploy/04_grant_agent_iam_permissions.py` attaches an inline policy with three actions:

| Action | When it's called | Why |
|---|---|---|
| `bedrock-agentcore:GetWorkloadAccessTokenForJWT` | Wraps T_user before OBO #1. | Required by AgentCore Identity. |
| `bedrock-agentcore:GetResourceOauth2Token` | Performs OBO #1 itself. | The actual exchange call. |
| `secretsmanager:GetSecretValue` | Implicit during OBO #1 — AgentCore Identity reads AgentApp's client secret from Secrets Manager. | Without it, the exchange fails before reaching Okta. |

All three are scoped to AgentCore-managed resources only — no `*` resources.

The Gateway's service role needs the **same three actions** for OBO #2 — that's set up separately in `deploy/02_create_gateway.py` (or expected to be on the role passed via `GATEWAY_SERVICE_ROLE_ARN`).

## OBOTRACE log lines

The agent emits five ASCII-prefixed structured log lines for the LEARNING_GUIDE:

1. `OBOTRACE: T_user received. aud=… cid=… sub=… scp=… uid=…` — the JWT that cleared inbound auth
2. `OBOTRACE: OBO 1 start. Exchanging T_user for T_gateway via AgentCore Identity.`
3. `OBOTRACE: OBO 1 complete. T_gateway minted. aud=… cid=… sub=… scp=… uid=…` — the token from Okta after the exchange
4. `OBOTRACE: MCP session opened to Gateway. About to list tools.`
5. `OBOTRACE: Gateway MCP tools discovered: […] (count=1)`

Use `python deploy/show_obo_trace.py` from `real-world/` to see these grouped per invocation with client-id annotations from `.env`.
