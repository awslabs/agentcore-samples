# MCP Server Advanced Features

## Overview

Beyond basic tools, MCP servers can expose **resources** (data sources) and **prompts** (reusable templates). This example demonstrates both on AgentCore runtime.

> MCP also defines **sampling** and **elicitation** (server-initiated requests back to the client). This sample does not implement them: it sets `json_response=True`, which reduces each reply to a single JSON body, so anything that is not a response or an error — progress notifications and server-initiated requests included — is dropped. Demonstrating sampling would mean serving `text/event-stream` and using a client that can hold the stream open.

> **If you're new to MCP on AgentCore**, start with the [MCP Server Basics](../01-mcp-server-basics/) example first.

## MCP Capabilities Demonstrated

### Tools — functions the LLM can call

Tools are the most common MCP feature. They let clients discover and execute functions:

```python
@mcp.tool()
def search_documents(query: str, max_results: Annotated[int, Field(ge=1, le=5)] = 5) -> str:
    """Search a document database."""
    # Your search logic here
    return json.dumps(results)
```

Clients call tools via `tools/call`:
```json
{"jsonrpc": "2.0", "method": "tools/call", "id": 1,
 "params": {"name": "search_documents", "arguments": {"query": "machine learning"}}}
```

### Resources — data the client can read

Resources expose data at URIs. Unlike tools, resources are read-only and don't take arbitrary arguments:

```python
@mcp.resource("config://app")
def get_app_config() -> str:
    """Application configuration settings."""
    return json.dumps({"version": "2.1.0", "environment": "production"})

@mcp.resource("data://system-status")
def get_system_status() -> str:
    """Current system health metrics."""
    return json.dumps({"status": "healthy", "uptime_hours": 142.5})
```

Clients discover resources with `resources/list` and read them with `resources/read`:
```json
{"jsonrpc": "2.0", "method": "resources/read", "id": 1, "params": {"uri": "config://app"}}
```

### Prompts — reusable templates

Prompts are parameterized templates that clients can retrieve and fill in. They're useful for standardizing how LLMs interact with your tools:

```python
@mcp.prompt()
def code_review(code: str, language: str = "python") -> str:
    """Generate a code review prompt."""
    return (
        f"Review this {language} code for bugs, performance, and security:\n\n"
        f"```{language}\n{code}\n```"
    )
```

Clients discover prompts with `prompts/list` and get them with `prompts/get`:
```json
{"jsonrpc": "2.0", "method": "prompts/get", "id": 1,
 "params": {"name": "code_review", "arguments": {"code": "def add(a, b): return a + b"}}}
```

## Invoking All Features

The `invoke.py` script exercises every MCP feature through `invoke_agent_runtime`:

```python
# Helper to send MCP JSON-RPC messages
def mcp_rpc(client, arn, method, params, rpc_id):
    msg = {"jsonrpc": "2.0", "method": method, "id": rpc_id, "params": params}
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=json.dumps(msg).encode(),
        contentType="application/json",
        accept="application/json, text/event-stream",
    )
    result = json.loads(resp["response"].read().decode())

    # A JSON-RPC error arrives with HTTP 200, so boto3 does not raise. Check for it, or a
    # server that failed to start returns an error for every call and still looks empty.
    if "error" in result:
        raise RuntimeError(f"{result['error']['code']}: {result['error']['message']}")
    return result

# Initialize session
mcp_rpc(client, arn, "initialize", {...}, 1)

# Tools
mcp_rpc(client, arn, "tools/list", {}, 2)
mcp_rpc(client, arn, "tools/call", {"name": "search_documents", "arguments": {...}}, 3)

# Resources
mcp_rpc(client, arn, "resources/list", {}, 4)
mcp_rpc(client, arn, "resources/read", {"uri": "config://app"}, 5)

# Prompts
mcp_rpc(client, arn, "prompts/list", {}, 6)
mcp_rpc(client, arn, "prompts/get", {"name": "code_review", "arguments": {...}}, 7)
```

All MCP JSON-RPC messages are passed through `invoke_agent_runtime` directly to your MCP server. AgentCore runtime handles session isolation via the `Mcp-Session-Id` header.

## What Runtime V2 gives you

`deploy.py` sets `platformVersion="V2"` on `create_agent_runtime`. AgentCore prepares
the execution environment once, snapshots it, and every new environment **resumes
from that snapshot** instead of loading the server's code and dependencies from
scratch. That gives every session a consistently fast, predictable start instead of
paying the code-load cost on each one — the main source of cold-start variance for a
zip-deployed server like this one.

```python
platformVersion="V2",
```

**This field must be set explicitly.** Omitting it does not give you Runtime V2 —
nothing in the create response tells you which platform version you got, so
`deploy.py` confirms it with `get_agent_runtime` after the runtime reaches `READY`.

A few things worth knowing about how it works:

- **`GetAgentRuntime` is the only operation that returns `platformVersion`.** Neither
  `CreateAgentRuntime` nor `UpdateAgentRuntime` echoes it back, which is why
  `deploy.py` reads it back explicitly rather than assuming the create call's input
  was honored.
- **The snapshot is prepared during the create call**, so `create_agent_runtime` and
  endpoint creation both take a few minutes rather than seconds — that time buys the
  consistently fast starts every session gets afterwards. `CREATING` for several
  minutes is expected, not a hang; budget for it if you script around this sample.
- **Anything captured at startup is frozen into the snapshot** and restored later,
  possibly hours after create. `mcp_server.py` in this sample has no startup-captured
  state to worry about: `get_timestamp` and the `data://system-status` resource both
  call `datetime.now()` inside the function body, per request, not at import time. If
  you extend this server: resolve credentials per request via the SDK's normal chain
  rather than caching them at import, and avoid the `random` module for anything that
  must be unique per session (its module-level state is captured in the snapshot and
  repeats across environments resumed from it) — use `uuid.uuid4()` or `secrets`
  instead.
- **Requires `boto3>=1.43.95`.** Older SDKs have no `platformVersion` field in the
  service model at all.
- **Runtime V2 is available in a subset of AgentCore's regions.** At GA, that's
  `us-east-1`, `us-east-2`, `us-west-2`, `eu-west-1`, and `ap-northeast-1` — a
  narrower list than AgentCore Runtime's own region coverage, and one that is
  expected to expand over time, so check current availability before picking a
  region.

## Files

| File | Description |
|:-----|:------------|
| `mcp_server.py` | MCP server with tools (`search_documents`, `analyze_sentiment`, `get_timestamp`), resources (`config://app`, `data://system-status`), and prompts (`code_review`, `summarize_document`) |
| `requirements.txt` | Local deps: `boto3`, plus `requirements-server.txt` |
| `requirements-server.txt` | The server's own deps (`mcp`, pinned `<2.0.0`) — this is what gets vendored into the zip |
| `deploy.py` | Same deployment pattern with `serverProtocol='MCP'` and `platformVersion='V2'`, plus a `tools/list` smoke test |
| `invoke.py` | Exercises all MCP features: `tools/list`, `tools/call`, `resources/list`, `resources/read`, `prompts/list`, `prompts/get` |
| `cleanup.py` | Deletes runtime, S3 artifact, log groups, IAM role |

## Prerequisites

See the [Prerequisites in the runtime README](../../README.md) — Python 3.12+, `uv` on PATH (for building arm64 packages), AWS credentials, and `boto3`. `deploy.py` also shells out to `zip`.

AgentCore is regional and there is no default region, so set one:

```bash
export AWS_REGION=us-west-2
```

## Quick Start

```bash
pip install -r requirements.txt

python deploy.py     # Deploy MCP server, then smoke-test it
python invoke.py     # Exercise all MCP features
python cleanup.py    # Clean up
```

To try the server locally first, run it in one terminal and call it from another — see the [two-terminal example in MCP Server Basics](../01-mcp-server-basics/README.md#quick-start).
