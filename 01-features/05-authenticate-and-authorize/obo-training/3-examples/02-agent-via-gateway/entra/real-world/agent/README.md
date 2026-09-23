# Agent — Strands on AgentCore Runtime

The Strands agent for Use Case 2. Performs **OBO #1** (T_user → T_gateway) and then talks to the AgentCore Gateway over MCP.

## What it does, line by line

```python
@app.entrypoint
async def invoke(payload, context):
    user_jwt = context.request_headers["Authorization"].split(" ", 1)[1]

    # OBO #1: T_user → T_gateway.
    gateway_token = _obo_user_to_gateway(user_jwt)

    # Connect to Gateway with T_gateway as the Bearer.
    with _gateway_mcp_client(gateway_token) as mcp_client:
        tools = mcp_client.list_tools_sync()       # tools/list
        agent = Agent(model=..., tools=tools)
        async for event in agent.stream_async(prompt):
            yield event["data"]
```

It's intentionally short. The LLM picks `getMyProfile` from the listed tools and calls it via MCP. The Gateway transparently runs OBO #2 and calls Microsoft Graph. The agent never touches Graph and never sees T_graph.

## What it does NOT do (vs Use Case 1's agent)

- It does **not** import `requests` or call Microsoft Graph directly.
- It does **not** define a `@tool` for `get_my_profile` — the tool comes from the Gateway, not from local Python.
- The LLM never sees a Graph token (and the agent doesn't see one either).

## Required environment variables

Set by `deploy/03_patch_agentcore_json.py` and read at startup:

| Var | Purpose |
|---|---|
| `AGENT_WORKLOAD_NAME` | The AgentCore workload identity name; used by `GetWorkloadAccessTokenForJWT`. |
| `AGENT_OBO_PROVIDER_NAME` | Credential provider name used for OBO #1 (auths as AgentApp). |
| `GATEWAY_SCOPE` | Scope requested in OBO #1 (`api://GatewayApp/access_as_user`). |
| `GATEWAY_MCP_URL` | The Gateway's MCP endpoint (e.g. `https://abc.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`). |
| `AWS_REGION` | Region for the boto3 `bedrock-agentcore` client. |

## Required IAM permissions on the agent's execution role

`deploy/04_grant_agent_iam_permissions.py` attaches an inline policy with three actions:

| Action | When it's called | Why |
|---|---|---|
| `bedrock-agentcore:GetWorkloadAccessTokenForJWT` | Wraps T_user before OBO #1. | Required by AgentCore Identity. |
| `bedrock-agentcore:GetResourceOauth2Token` | Performs OBO #1 itself. | The actual exchange call. |
| `secretsmanager:GetSecretValue` | Implicit during OBO #1 — AgentCore Identity reads AgentApp's client secret from Secrets Manager. | Without it, the exchange fails before reaching Entra. |

All three are scoped to AgentCore-managed resources only — no `*` resources.

The Gateway's service role needs the **same three actions** for OBO #2 — that's set up separately in `deploy/02_create_gateway.py` (or expected to be on the role passed via `GATEWAY_SERVICE_ROLE_ARN`).
