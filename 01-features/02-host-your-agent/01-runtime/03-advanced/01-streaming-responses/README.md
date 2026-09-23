# Streaming Agent Responses

## Overview

Stream partial results from your agent in real time using Server-Sent Events (SSE). Instead of waiting for the complete response, clients receive text chunks as the agent generates them — improving perceived latency and user experience.

## How Streaming Works

The streaming behavior is controlled entirely on the **client side** — your agent code doesn't change. The difference is in the `accept` header when calling `invoke_agent_runtime`:

```python
# Non-streaming (default) — waits for complete response
response = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    payload=payload,
    accept="application/json",       # ← full response
)
body = response["response"].read()   # blocks until complete

# Streaming — receives chunks as they're generated
response = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    payload=payload,
    accept="text/event-stream",      # ← SSE streaming
)
for line in response["response"].iter_lines():
    chunk = line.decode("utf-8")
    if chunk.startswith("data:"):
        print(chunk[5:], end="", flush=True)  # display incrementally
```

### SSE Event Format

The stream follows the [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events) format:

```
data: Here is the
data: beginning of
data: the response...
```

Each `data:` line contains a chunk of the agent's response. Your client reads them incrementally.

## Agent Code

The agent code is identical to a non-streaming agent. The `@app.entrypoint` function returns a string — the SDK handles chunking it into SSE events when the client requests `text/event-stream`:

```python
@app.entrypoint
def invoke_agent(payload: dict) -> str:
    prompt = payload.get("prompt", "Hello!")
    response = agent(prompt)
    return response.message["content"][0]["text"]
```

No changes needed to support streaming — it's a client-side choice.

## Files

| File | Description |
|:-----|:------------|
| `agent.py` | Strands agent with weather and calculator tools |
| `requirements.txt` | Dependencies |
| `deploy.py` | Deploys the agent |
| `invoke.py` | Invokes with `accept="text/event-stream"` and displays chunks in real time |
| `cleanup.py` | Deletes runtime, endpoint, S3 artifact, IAM role |

## Quick Start

```bash
python deploy.py     # Deploy the agent
python invoke.py     # Watch tokens stream in real time
python cleanup.py    # Clean up
```
