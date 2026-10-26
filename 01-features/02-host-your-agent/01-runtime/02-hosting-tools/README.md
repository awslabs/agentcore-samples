# Hosting MCP Tools on AgentCore runtime

## Overview

AgentCore runtime can host [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers, making your tools available to any MCP-compatible client. When you set the protocol to `MCP`, AgentCore runtime expects a stateless streamable-HTTP MCP server on `0.0.0.0:8000/mcp`.

## How MCP Hosting Differs from Agent Hosting

| Aspect | Agent (HTTP) | MCP Server |
|:-------|:-------------|:-----------|
| `serverProtocol` | `HTTP` | `MCP` |
| SDK pattern | `BedrockAgentCoreApp` + `@app.entrypoint` | `FastMCP` + `mcp.run(transport="streamable-http")` |
| Port | 8080 | 8000 |
| Path | `/invocations` | `/mcp` |
| Communication | Free-form JSON | JSON-RPC 2.0 (MCP spec) |
| Client sends | Any JSON payload | MCP methods: `tools/call`, `resources/read`, etc. |

## Writing an MCP Server

Use the `mcp` Python SDK's `FastMCP` class and run it with the streamable-HTTP transport. An MCP server is **not** a `BedrockAgentCoreApp` — that class serves an HTTP agent on `POST /invocations`, while an MCP server speaks JSON-RPC over the MCP transport. The `bedrock-agentcore` SDK is not involved:

Pin the SDK below 2.0 (`mcp>=1.10.0,<2.0.0`): mcp 2.0.0 renamed `FastMCP` to `MCPServer` and moved it out of `mcp.server.fastmcp` with no compatibility alias, so the import below fails on 2.x — and because AgentCore does not run your entry point at create time, an unpinned deploy reports `READY` and only fails when you invoke it.

```python
from mcp.server.fastmcp import FastMCP

# host, port and stateless_http are the runtime's service contract, not preferences.
# json_response=True is a choice: it makes every reply a plain JSON body, which is what
# lets the client below use json.loads(). Drop it and the server answers with SSE.
mcp = FastMCP("my-tools", host="0.0.0.0", port=8000, stateless_http=True, json_response=True)

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

`stateless_http=True` matters: the runtime does not guarantee that two requests in a session reach the same process, and FastMCP's stateful default fails behind a load balancer with "Missing session ID".

## Deploying an MCP Server

The deployment is identical to agents — only `serverProtocol` and `entryPoint` change:

```python
control = boto3.client("bedrock-agentcore-control")

control.create_agent_runtime(
    agentRuntimeName="my_mcp_server",  # [a-zA-Z][a-zA-Z0-9_]{0,47} — hyphens are rejected
    agentRuntimeArtifact={
        "codeConfiguration": {
            "code": {"s3": {"bucket": "my-bucket", "prefix": "my-server/code.zip"}},
            "runtime": "PYTHON_3_12",
            "entryPoint": ["mcp_server.py"],  # ← your MCP server file
        }
    },
    roleArn=role_arn,
    networkConfiguration={"networkMode": "PUBLIC"},
    protocolConfiguration={"serverProtocol": "MCP"},  # ← MCP protocol
)
```

> **IAM note**: MCP tool servers typically don't call LLMs, so no `bedrock:InvokeModel` is needed. The role still needs the runtime's observability permissions — CloudWatch Logs, X-Ray, and `cloudwatch:PutMetricData` — because with logging alone the runtime serves traffic but silently emits no traces and no metrics. Add permissions for any AWS services your tools access (DynamoDB, S3, etc.).

## Invoking an MCP Server

Clients send MCP JSON-RPC messages through `invoke_agent_runtime`. The payload is passed through directly to your MCP server:

```python
data = boto3.client("bedrock-agentcore")

# MCP JSON-RPC message
msg = {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 1,
    "params": {"name": "add", "arguments": {"a": 5, "b": 3}},
}

response = data.invoke_agent_runtime(
    agentRuntimeArn=arn,
    payload=json.dumps(msg).encode(),
    # Both are required: the streamable-HTTP transport rejects a request that does not
    # accept application/json or text/event-stream, and omitting them fails with HTTP 406.
    contentType="application/json",
    accept="application/json, text/event-stream",
)
result = json.loads(response["response"].read().decode())
# {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "8"}]}}

# A JSON-RPC error arrives with HTTP 200, so boto3 does not raise — check for it, or a
# broken server (which returns an error for every call) looks like an empty success.
if "error" in result:
    raise RuntimeError(f"{result['error']['code']}: {result['error']['message']}")
```

## MCP Feature Support on AgentCore runtime

| Category | Feature | Spec Methods | Supported |
|:---------|:--------|:-------------|:---------:|
| **Server** | Tools | `tools/list`, `tools/call` | ✅ |
| **Server** | Resources | `resources/list`, `resources/read` | ✅ |
| **Server** | Prompts | `prompts/list`, `prompts/get` | ✅ |
| **Client** | Sampling | `sampling/createMessage` | ✅ |
| **Client** | Elicitation | `elicitation/create` | ✅ |
| **Protocol** | Lifecycle | `initialize`, `ping` | ✅ |
| **Protocol** | Transports | Streamable HTTP | ✅ |
| **Utilities** | Progress | `notifications/progress` | ✅ |
| **Utilities** | Logging | `logging/setLevel` | ✅ |

## Key Technical Details

- **Transport**: Stateless streamable HTTP. Build the server with `stateless_http=True`: the runtime does not guarantee that two requests in a session reach the same process, so the server must keep no transport state between requests. `InvokeAgentRuntime` does carry `Mcp-Session-Id` and `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` headers for grouping calls, but they do not give a stateful server process affinity
- **Port**: MCP server runs on port `8000` at path `/mcp` (this is the default for most MCP SDKs)
- **Content types**: Supports both `application/json` and `text/event-stream` responses
- **Payload passthrough**: The `invoke_agent_runtime` payload is forwarded directly to your MCP server as-is

## Tutorials

| Tutorial | What You'll Learn |
|:---------|:------------------|
| [01-mcp-server-basics](01-mcp-server-basics/) | Define tools, deploy, invoke with `tools/list` and `tools/call` |
| [02-mcp-server-features](02-mcp-server-features/) | Resources, prompts, and the full MCP feature set |
| [03-mcp-ec2-capacity-provider](03-mcp-ec2-capacity-provider/) | Run an MCP server on your own EC2 instances via a CapacityProvider |
