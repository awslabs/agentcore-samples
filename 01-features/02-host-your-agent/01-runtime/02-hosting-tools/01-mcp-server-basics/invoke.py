"""
Invoke an MCP server deployed on AgentCore Runtime.

Sends MCP JSON-RPC messages (tools/list, tools/call) via the
invoke_agent_runtime API. The payload is passed through directly
to the MCP server.

Usage:
    python invoke.py
"""

import json
import os
import sys

import boto3


class McpError(RuntimeError):
    """A JSON-RPC error returned by the runtime or the MCP server."""


def load_config() -> dict:
    # Read next to this file, not the current directory, so the script works when
    # invoked by an absolute path from elsewhere.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_config.json")
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: {path} not found. Run deploy.py first.")
        sys.exit(1)


def send_mcp_rpc(runtime_arn: str, method: str, params: dict, region: str, rpc_id: int = 1) -> dict:
    """Send an MCP JSON-RPC message to the deployed server.

    contentType and accept are both required: the MCP streamable-HTTP transport
    rejects a request that does not accept application/json or text/event-stream,
    and omitting them here makes the call fail with HTTP 406.
    """
    client = boto3.client("bedrock-agentcore", region_name=region)

    rpc_message = {
        "jsonrpc": "2.0",
        "method": method,
        "id": rpc_id,
        "params": params,
    }

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=json.dumps(rpc_message).encode("utf-8"),
        contentType="application/json",
        accept="application/json, text/event-stream",
    )

    body = response["response"].read().decode("utf-8")
    result = json.loads(body)

    # A JSON-RPC error arrives with HTTP 200, so boto3 does not raise. Without this
    # check `result.get("result", {})` turns every failure into an empty success —
    # including "Runtime initialization time exceeded", which is what a server that
    # crashed on startup returns for every call.
    if "error" in result:
        err = result["error"]
        raise McpError(f"{method} failed [{err.get('code')}]: {err.get('message')}")

    return result


def print_tool_result(result: dict) -> bool:
    """Print a tools/call result and return True if the tool reported a failure."""
    payload = result.get("result", {})
    # isError=True is a tool that ran and failed. It arrives inside a successful
    # JSON-RPC response, so it has to be checked separately from the error envelope.
    label = "Error " if payload.get("isError") else "Result"
    text = " | ".join(c.get("text", "") for c in payload.get("content", []))
    print(f"    {label}: {text}")
    return bool(payload.get("isError"))


def main():
    config = load_config()
    runtime_arn = config["runtime_arn"]
    region = config["region"]

    print(f"MCP Server: {runtime_arn}\n")
    failures = 0

    # 1. Initialize the MCP session.
    #
    # This server is stateless (mcp_server.py sets stateless_http=True), so every
    # request stands alone and tools/list below works without this handshake. It is
    # here because a stateful MCP server requires it, and because the negotiated
    # protocol version is worth seeing.
    print("─── Initialize")
    result = send_mcp_rpc(
        runtime_arn,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "tutorial-client", "version": "1.0.0"},
        },
        region,
        rpc_id=1,
    )
    info = result.get("result", {})
    print(f"    Server: {json.dumps(info.get('serverInfo', {}))}")
    print(f"    Protocol: {info.get('protocolVersion')}\n")

    # 2. List available tools
    print("─── tools/list")
    result = send_mcp_rpc(runtime_arn, "tools/list", {}, region, rpc_id=2)
    tools = result.get("result", {}).get("tools", [])
    for t in tools:
        print(f"    • {t['name']}: {t.get('description', '')}")
    print()

    # 3. Call tools
    calls = [
        ("add_numbers", {"a": 5, "b": 3}),
        ("multiply_numbers", {"a": 7, "b": 6}),
        ("greet", {"name": "Alice", "language": "spanish"}),
    ]
    for rpc_id, (name, arguments) in enumerate(calls, start=3):
        args = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        print(f"─── tools/call: {name}({args})")
        result = send_mcp_rpc(runtime_arn, "tools/call", {"name": name, "arguments": arguments}, region, rpc_id=rpc_id)
        if print_tool_result(result):
            failures += 1
        print()

    if failures:
        print(f"✗ {failures} tool call(s) reported an error")
        sys.exit(1)
    print("✓ All calls succeeded")


if __name__ == "__main__":
    try:
        main()
    except McpError as e:
        # Exit non-zero so a broken deployment cannot look like a successful run.
        print(f"\n✗ {e}")
        sys.exit(1)
