"""
Exercise all MCP features: tools, resources, and prompts.

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


def mcp_rpc(client, arn: str, method: str, params: dict, rpc_id: int) -> dict:
    msg = {"jsonrpc": "2.0", "method": method, "id": rpc_id, "params": params}
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=json.dumps(msg).encode("utf-8"),
        contentType="application/json",
        accept="application/json, text/event-stream",
    )
    result = json.loads(resp["response"].read().decode("utf-8"))

    # A JSON-RPC error arrives with HTTP 200, so boto3 does not raise. Without this
    # check `result.get("result", {})` turns every failure into an empty success —
    # including "Runtime initialization time exceeded", which is what a server that
    # crashed on startup returns for every call.
    if "error" in result:
        err = result["error"]
        raise McpError(f"{method} failed [{err.get('code')}]: {err.get('message')}")

    return result


def print_tool_result(label: str, result: dict) -> bool:
    """Print a tools/call result and return True if the tool reported a failure.

    isError=True is a tool that ran and failed. It arrives inside a *successful*
    JSON-RPC response, so mcp_rpc's error-envelope check cannot catch it.
    """
    payload = result.get("result", {})
    failed = bool(payload.get("isError"))
    print(f"\n  {label}:{'  [TOOL ERROR]' if failed else ''}")
    print(f"  {json.dumps(payload, indent=4)}")
    return failed


def main():
    config = load_config()
    client = boto3.client("bedrock-agentcore", region_name=config["region"])
    arn = config["runtime_arn"]
    rpc_id = 0

    print(f"MCP Server: {arn}\n")

    # Initialize
    rpc_id += 1
    mcp_rpc(
        client,
        arn,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "tutorial", "version": "1.0"},
        },
        rpc_id,
    )

    # ── Tools ────────────────────────────────────────────────────────────
    print("═══ TOOLS ═══")
    rpc_id += 1
    result = mcp_rpc(client, arn, "tools/list", {}, rpc_id)
    for t in result.get("result", {}).get("tools", []):
        print(f"  • {t['name']}: {t.get('description', '')}")

    rpc_id += 1
    result = mcp_rpc(
        client,
        arn,
        "tools/call",
        {
            "name": "search_documents",
            "arguments": {"query": "machine learning", "max_results": 3},
        },
        rpc_id,
    )
    tool_failures = print_tool_result("search_documents('machine learning')", result)

    rpc_id += 1
    result = mcp_rpc(
        client,
        arn,
        "tools/call",
        {
            "name": "analyze_sentiment",
            "arguments": {"text": "This is a great product and I love using it every day!"},
        },
        rpc_id,
    )
    tool_failures += print_tool_result("analyze_sentiment(...)", result)

    # ── Resources ────────────────────────────────────────────────────────
    print("\n═══ RESOURCES ═══")
    rpc_id += 1
    result = mcp_rpc(client, arn, "resources/list", {}, rpc_id)
    for r in result.get("result", {}).get("resources", []):
        print(f"  • {r['uri']}: {r.get('name', '')}")

    rpc_id += 1
    result = mcp_rpc(client, arn, "resources/read", {"uri": "config://app"}, rpc_id)
    print("\n  config://app:")
    print(f"  {json.dumps(result.get('result', {}), indent=4)}")

    # ── Prompts ──────────────────────────────────────────────────────────
    print("\n═══ PROMPTS ═══")
    rpc_id += 1
    result = mcp_rpc(client, arn, "prompts/list", {}, rpc_id)
    for p in result.get("result", {}).get("prompts", []):
        print(f"  • {p['name']}: {p.get('description', '')}")

    rpc_id += 1
    result = mcp_rpc(
        client,
        arn,
        "prompts/get",
        {
            "name": "code_review",
            "arguments": {"code": "def add(a, b): return a + b", "language": "python"},
        },
        rpc_id,
    )
    print("\n  code_review prompt:")
    messages = result.get("result", {}).get("messages", [])
    for msg in messages:
        print(f"  {msg.get('content', {}).get('text', '')[:200]}")

    if tool_failures:
        print(f"\n✗ {tool_failures} tool call(s) reported an error")
        sys.exit(1)
    print("\n✓ All MCP features exercised successfully")


if __name__ == "__main__":
    try:
        main()
    except McpError as e:
        # Exit non-zero so a broken deployment cannot look like a successful run.
        print(f"\n✗ {e}")
        sys.exit(1)
