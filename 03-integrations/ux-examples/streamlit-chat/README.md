# Chat with a selected AgentCore Runtime in Streamlit

This **single-user, loopback UI** sends `{"prompt":"..."}` to one existing IAM-authenticated AgentCore Runtime. Use a short-lived AWS profile authorized to invoke only that Runtime. No account-wide runtime/version listing, deployment, model key or cleanup permission is required. The user's browser never receives AWS credentials.

```bash
# From this directory, with Python 3.13 and uv
uv sync --frozen
export AWS_PROFILE=my-sandbox
export AGENT_RUNTIME_ARN='arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/replace-with-your-id'
uv run --frozen streamlit run app.py --server.address 127.0.0.1
```

Replace the ARN with the full ARN from the owner. Each browser session gets a new Runtime session ID; **New conversation** rotates it and clears the local chat. The server derives the Region from the ARN and uses the default endpoint. Do not publish this local app as a multi-user service or forward its port. Calls to the selected agent and its tools may incur charges. The chat shows the current exchange history; whether the agent remembers earlier turns is the agent's decision, not a guarantee of this UI.

## Which responses work?

| Runtime response | What the UI displays |
|---|---|
| `application/json`: `{"response":"answer"}` or `"answer"` | One completed message. This does **not** demonstrate streaming. |
| `text/event-stream`: SDK `data:` events containing JSON strings | Text as events arrive. The optional producer emits `🔧 Using tool: weather` as a separate string; uncheck **Show tool-use markers** to hide it. |

Unsupported objects, malformed or unfinished events, and oversized responses produce an error, not a fabricated assistant message. Text is rendered as text, not HTML. The app does not log raw prompts, response bodies or provider exceptions.

## Optional streaming producer

The [producer](example/agent.py) creates a request-local Strands agent, uses Bedrock and includes one explicitly simulated weather tool. No broad tools bundle or calculator is required. To try the producer locally, set `BEDROCK_MODEL_ID` and use your short-lived profile in a supported Region, then run `uv run --project example python example/agent.py`. A real invocation incurs inference charges. For a hosted producer, package `example/agent.py` and `example/pyproject.toml` in an operator-owned Python 3.13 Runtime with an execution role authorized for the selected model. Set `BEDROCK_MODEL_ID` in the **hosted Runtime environment**, not just in the UI shell. The UI doesn't deploy or adopt the Runtime. The previous optional Toolkit deploy commands are retired.

## Offline compatibility check

```bash
uv sync --frozen --group test
uv run --frozen --group test python -m unittest -v test_transport
```

Tests exercise the actual AgentCore HTTP adapter, a controlled Strands model that calls the real demo tool, SSE chunk boundaries, completed JSON, and Streamlit chat/reset behavior. They don't require an AWS account. For a completed-response agent, see the [customer-support getting-started sample](../../../00-getting-started/); the sample above is the streaming counterpart.
