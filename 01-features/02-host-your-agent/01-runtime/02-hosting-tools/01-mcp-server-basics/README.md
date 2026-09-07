# MCP Server Basics

## Overview

Create and deploy a basic MCP server with tools to AgentCore runtime. Once deployed, the tools are reachable through the `invoke_agent_runtime` API, which is what `invoke.py` here uses.

![MCP server on AgentCore runtime](images/hosting_mcp_server.png)

```
┌─────────────┐   MCP RPC (JSON-RPC)   ┌──────────────────────────┐
│  MCP Client  │ ──────────────────────▶│  AgentCore runtime       │
│  (any MCP    │◀────────────────────── │  (MCP protocol)          │
│   client)    │   tool results         │  ┌──────────────────┐    │
└─────────────┘                         │  │  MCP Server      │    │
                                        │  │  (FastMCP)       │    │
                                        │  └──────────────────┘    │
                                        └──────────────────────────┘
```

> **Note on clients**: a deployed runtime is reached with SigV4-signed AWS API calls, so an off-the-shelf MCP client (Claude Desktop, Cursor, Kiro) cannot point at it directly without something to sign requests on its behalf. See [03-advanced/10-mcp-dynamic-client-registration](../../03-advanced/10-mcp-dynamic-client-registration/) for inbound auth.

## Prerequisites

See the [Prerequisites in the runtime README](../../README.md) — Python 3.12+, `uv` on PATH (for building arm64 packages), AWS credentials, and `boto3`. `deploy.py` also shells out to `zip`.

AgentCore is regional and there is no default region, so set one:

```bash
export AWS_REGION=us-west-2
```

## Step 1: Write the MCP Server (`mcp_server.py`)

An MCP server on AgentCore is a plain [`FastMCP`](https://github.com/modelcontextprotocol/python-sdk) server. It is **not** a `BedrockAgentCoreApp`: that class serves an HTTP agent on `POST /invocations`, whereas an MCP server speaks JSON-RPC over the MCP streamable-HTTP transport. The `bedrock-agentcore` SDK is not involved at all, and is not a dependency of this sample.

```python
from typing import Literal

from mcp.server.fastmcp import FastMCP

# host, port, path and stateless_http are the runtime's service contract, not preferences.
mcp = FastMCP("basic-tools", host="0.0.0.0", port=8000, stateless_http=True, json_response=True)

@mcp.tool()
def add_numbers(a: float, b: float) -> float:
    """Add two numbers together."""
    return a + b

@mcp.tool()
def greet(name: str, language: Literal["english", "spanish", "french"] = "english") -> str:
    """Greet someone in English, Spanish or French."""
    greetings = {
        "english": f"Hello, {name}!",
        "spanish": f"¡Hola, {name}!",
        "french": f"Bonjour, {name}!",
    }
    return greetings[language]

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

What the runtime requires, and what is your choice:

| Setting | Why |
|:--------|:----|
| `port=8000` | Fixed by the runtime for MCP (HTTP agents use 8080, A2A uses 9000). |
| `host="0.0.0.0"` | The runtime's health check has to reach the server; `127.0.0.1` fails. |
| `stateless_http=True` | Two requests in a session are not guaranteed to reach the same process. FastMCP's stateful default fails with "Missing session ID". |
| path `/mcp` | FastMCP's streamable-http default, which is what the runtime expects. |
| `json_response=True` | **Your choice, not a requirement.** It makes every reply a plain JSON body so `invoke.py` can `json.loads()` it. The runtime accepts `text/event-stream` too. Trade-off: in JSON mode anything that is not a response or error is dropped, so progress notifications and server-initiated requests (sampling, elicitation) never reach the client. |

Key differences from hosting an agent:
- Use a `FastMCP` server and `mcp.run(transport="streamable-http")`, not `BedrockAgentCoreApp` and `@app.entrypoint`
- The MCP server listens on port **8000** at path `/mcp` (not port 8080 at `/invocations`)
- Communication uses **JSON-RPC** (MCP protocol), not free-form JSON

Tool authoring notes worth copying:
- Keep docstrings to one line. FastMCP publishes the whole docstring as the tool's `description`, so `Args:`/`Returns:` blocks are sent to every client on every `tools/list`, duplicating the generated `inputSchema`.
- Use `Literal[...]` for closed sets of values. It puts an `enum` in the schema; a bare `str` tells the model nothing and lets an unknown value fall through silently.

## Step 2: Deploy with MCP Protocol

The deployment is the same as agents, with one key difference — `serverProtocol` is `MCP`:

```python
control.create_agent_runtime(
    agentRuntimeName="basic_mcp_server",  # must match [a-zA-Z][a-zA-Z0-9_]{0,47} — no hyphens
    agentRuntimeArtifact={
        "codeConfiguration": {
            "code": {"s3": {"bucket": bucket, "prefix": "basic_mcp_server/code.zip"}},
            "runtime": "PYTHON_3_12",
            "entryPoint": ["mcp_server.py"],  # ← your MCP server file
        }
    },
    roleArn=role_arn,
    networkConfiguration={"networkMode": "PUBLIC"},
    protocolConfiguration={"serverProtocol": "MCP"},  # ← MCP protocol
)
```

No `create_agent_runtime_endpoint` call is needed: AgentCore provisions a `DEFAULT` endpoint along with the runtime, and that is the one an invoke with no `qualifier` reaches. Creating a second endpoint just adds a resource and its own log group.

> **Verify the deployment, don't assume it.** AgentCore does not execute your entry point when it creates the runtime, so a server that crashes on import still reports `READY`. `deploy.py` therefore ends with a `tools/list` smoke test — without one, a broken server looks like a successful deploy and only fails later, at invoke time.

> **IAM note**: MCP tool servers don't call Bedrock models, so no `bedrock:InvokeModel` is needed. The role still needs the runtime's observability permissions (CloudWatch Logs, X-Ray, and `cloudwatch:PutMetricData`) — with logging alone the runtime serves traffic but silently emits no traces and no metrics. If your tools call AWS services (DynamoDB, S3, etc.), add those permissions too.

## Step 3: Invoke with MCP JSON-RPC Messages

MCP uses [JSON-RPC 2.0](https://www.jsonrpc.org/specification). The `invoke_agent_runtime` payload is passed through directly to your MCP server.

Always pass both `contentType` and `accept`. The streamable-HTTP transport rejects a request whose `Accept` does not include `application/json` or `text/event-stream` with **HTTP 406**, and one without a JSON `Content-Type` with **HTTP 400**.

### Initialize the session

```python
client = boto3.client("bedrock-agentcore")

init_msg = {
    "jsonrpc": "2.0",
    "method": "initialize",
    "id": 1,
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "my-client", "version": "1.0"},
    },
}

response = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    payload=json.dumps(init_msg).encode(),
    contentType="application/json",
    accept="application/json, text/event-stream",
)
# Returns server info and capabilities
```

Because this server is stateless, each call stands alone and `tools/list` works without the handshake first. A stateful MCP server would require it.

### List available tools

```python
list_msg = {"jsonrpc": "2.0", "method": "tools/list", "id": 2, "params": {}}

response = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    payload=json.dumps(list_msg).encode(),
    contentType="application/json",
    accept="application/json, text/event-stream",
)
# Returns: {"result": {"tools": [{"name": "add_numbers", "description": "...", "inputSchema": {...}}, ...]}}
```

### Call a tool

```python
call_msg = {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 3,
    "params": {
        "name": "add_numbers",
        "arguments": {"a": 5, "b": 3},
    },
}

response = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    payload=json.dumps(call_msg).encode(),
    contentType="application/json",
    accept="application/json, text/event-stream",
)
# Returns: {"result": {"content": [{"type": "text", "text": "8.0"}],
#                      "structuredContent": {"result": 8.0}, "isError": false}}
```

### Always check for errors

A JSON-RPC error comes back with **HTTP 200**, so boto3 does not raise and `result.get("result", {})` would silently return `{}`:

```python
result = json.loads(response["response"].read().decode())
if "error" in result:
    raise RuntimeError(f"{result['error']['code']}: {result['error']['message']}")
```

This matters: a server that failed to start returns `{"error": {"code": -32010, "message": "Runtime initialization time exceeded..."}}` for every call. Without the check, a completely broken deployment prints empty results and exits 0.

A tool that ran and failed is different — it returns a successful response with `isError: true`, so check that separately.

### Sessions

`runtimeSessionId` is optional, and `invoke.py` omits it. The service then mints a fresh one per request — it comes back as `response["runtimeSessionId"]` — so each call is its own session. Pass your own value instead to group calls into one session you can name and later end with `StopRuntimeSession`. The minimum length is **33 characters**, so a short value like `"my-session"` is rejected by client-side validation before the request is sent.

### MCP Methods Reference

| Method | Purpose | Params |
|:-------|:--------|:-------|
| `initialize` | Start session, exchange capabilities | `protocolVersion`, `capabilities`, `clientInfo` |
| `tools/list` | Discover available tools | (none) |
| `tools/call` | Execute a tool | `name`, `arguments` |
| `resources/list` | List available resources | (none) |
| `resources/read` | Read a resource | `uri` |
| `prompts/list` | List available prompts | (none) |
| `prompts/get` | Get a prompt template | `name`, `arguments` |

This sample registers only tools, so `resources/list` and `prompts/list` return empty lists. See [02-mcp-server-features](../02-mcp-server-features/) for resources and prompts.

## Files

| File | Description |
|:-----|:------------|
| `mcp_server.py` | MCP server with `add_numbers`, `multiply_numbers`, and `greet` tools |
| `requirements.txt` | Local deps: `boto3`, plus `requirements-server.txt` |
| `requirements-server.txt` | The server's own deps (`mcp`, pinned `<2.0.0`) — this is what gets vendored into the zip |
| `deploy.py` | Zips code, uploads to S3, creates runtime with `serverProtocol='MCP'`, smoke-tests it |
| `invoke.py` | Sends MCP JSON-RPC messages (`initialize`, `tools/list`, `tools/call`) |
| `cleanup.py` | Deletes runtime, S3 artifact, log groups, IAM role |

## Quick Start

```bash
pip install -r requirements.txt
```

Test locally, in two terminals — `mcp_server.py` runs in the foreground:

```bash
# Terminal 1: start the server
python mcp_server.py
```

```bash
# Terminal 2: call it. The Accept header is required; without it the server returns 406.
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}'
```

Then deploy and invoke:

```bash
python deploy.py
python invoke.py
python cleanup.py
```
