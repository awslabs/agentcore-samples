# The agent

[`agent.py`](agent.py) is a Strands agent that runs on AgentCore Runtime. It is
the one file you copy into the CLI-scaffolded project at deploy time:

```bash
cp agent/agent.py "$AGENT_RUNTIME_NAME"/app/"$AGENT_RUNTIME_NAME"/main.py
```

Do **not** copy [`requirements.txt`](requirements.txt) alongside it. The scaffold
tracks the runtime's dependencies in `app/<name>/pyproject.toml` with a
`uv.lock`, and already declares everything `agent.py` imports. That file is here
to document the dependency set for anyone deploying the agent another way.

## What it does, and deliberately does not

It reads the caller's JWT from the `Authorization` header and forwards it to the
gateway **unchanged** as the MCP Bearer credential. It performs no token
exchange, holds no GitHub credential, and never calls GitHub directly — the
gateway does that, using the authorization the user granted on the consent
portal.

That is why `requirements.txt` here is short: no boto3, no AgentCore Identity
calls. The only AWS-shaped thing the agent needs is the Bedrock access the
Runtime execution role already has.

## The consent path

`tools/list` always succeeds, because the gateway target was created with its
tool schema supplied upfront. Only `tools/call` needs the user's GitHub token,
and if there is none the gateway replies with a URL-mode elicitation — JSON-RPC
error `-32042` — instead of failing.

The agent turns that into a plain instruction naming the **consent portal**, not
the raw AgentCore Identity authorize URL in the elicitation payload. Following
that raw URL would skip the portal's session binding; the portal calls
`CompleteResourceTokenAuth` on the user's behalf.

Because the elicitation's propagation path through the MCP client is not a
public contract, detection covers both shapes it can arrive in:

- a `toolResult` with `status: "error"` on the Strands message history
  (`consent_needed_in_history`), and
- a raised exception, unwrapped through the anyio task groups the MCP client
  runs inside (`unwrap`).

Matching requires an **error** status, so a successful tool result that happens
to mention the code does not trip it. The system prompt also tells the model to
relay such an error verbatim rather than retrying or working around it.

Every detection logs a line starting `CONSENT_REQUIRED`, which is what makes
this observable:

```bash
agentcore logs --since 10m --query "CONSENT_REQUIRED"
```

## Environment

Set by [`../deploy/05_patch_agentcore_json.py`](../deploy/05_patch_agentcore_json.py):

| Variable | Purpose |
| :--- | :--- |
| `GATEWAY_MCP_URL` | The gateway's `/mcp` endpoint |
| `PORTAL_URL` | Shown to users who have not consented yet |
| `AWS_REGION` | Region for the Bedrock model |
| `MODEL_ID` | Optional override; defaults to Claude Sonnet 4.5 |

The same script adds `Authorization` to the runtime's `requestHeaderAllowlist`.
Without that the header is stripped before the handler runs and the agent has
nothing to forward.
