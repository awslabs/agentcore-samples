# Hosting Agents with HTTP Protocol

The HTTP protocol is the default and most common way to host agents on AgentCore runtime. Your agent receives JSON payloads on `POST /invocations` and returns JSON responses (or streams via Server-Sent Events).

## Examples

| Example | Framework | Model | What It Teaches |
|:--------|:----------|:------|:----------------|
| [01-strands-bedrock](01-strands-bedrock/) | Strands Agents | Amazon Bedrock (Claude) | **Start here** — full walkthrough of every API parameter |
| [02-langgraph-bedrock](02-langgraph-bedrock/) | LangGraph | Amazon Bedrock (Claude) | Framework agnosticism — same deployment, different agent code |
| [03-strands-openai](03-strands-openai/) | Strands Agents | OpenAI (GPT-4) | Model agnosticism — environment variables, external LLM providers |

## The Agent Contract

AgentCore runtime expects your code to expose two HTTP endpoints:

| Endpoint | Method | Purpose | Handled By |
|:---------|:-------|:--------|:-----------|
| `/invocations` | POST | Agent interaction — receives JSON, returns JSON or SSE | `@app.entrypoint` decorator |
| `/ping` | GET | Health check — must return 200 | Automatic (SDK) |

The `bedrock-agentcore` SDK creates both endpoints when you use `BedrockAgentCoreApp`:

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

@app.entrypoint
def my_agent(payload: dict) -> str:
    """Called for every POST /invocations request.

    Args:
        payload: The JSON body from the client (parsed dict).

    Returns:
        A string response sent back to the client.
    """
    return "Hello from my agent!"

if __name__ == "__main__":
    app.run()  # Starts on 0.0.0.0:8080
```

## The Deployment Pattern

Every example follows the same structure and deployment flow:

```
my-agent/
├── agent.py           # Agent logic with @app.entrypoint
├── requirements.txt   # Python dependencies (installed automatically by AgentCore)
├── deploy.py          # Zip code → S3 → create_agent_runtime → create_agent_runtime_endpoint
├── invoke.py          # invoke_agent_runtime with JSON payload
└── cleanup.py         # delete endpoint → delete runtime → delete S3 → delete IAM role
```

The deployment steps are:

1. **Create IAM role** — trust policy for `bedrock-agentcore.amazonaws.com`, inline policy for model access + logging
2. **Zip and upload** — `agent.py` + `requirements.txt` → zip → S3 bucket
3. **Create runtime** — `create_agent_runtime()` with `codeConfiguration` pointing to S3
4. **Wait for READY** — poll `get_agent_runtime()` until status is `READY`
5. **Create endpoint** — `create_agent_runtime_endpoint()` to make the agent invocable
6. **Wait for endpoint READY** — poll `list_agent_runtime_endpoints()`

The `deploy.py` in each example handles all of this. The `invoke.py` calls `invoke_agent_runtime()` on the data plane. The `cleanup.py` tears everything down in reverse order.
