"""
Invoke the MCP progress notifications e2e agent using JSON-RPC messages.

Demonstrates: initialize, tools/list, tools/call (generate_report with
progress notifications).

Usage:
    python deploy.py   # deploy first
    python invoke.py   # run this demo
"""

import json
import sys

import boto3


def load_config() -> dict:
    try:
        with open("runtime_config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)


def send_jsonrpc(client, arn: str, method: str, params: dict = None, msg_id: int = 1) -> dict:
    """Send a JSON-RPC message to the MCP server."""
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params:
        message["params"] = params

    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=json.dumps(message).encode("utf-8"),
        contentType="application/json",
        accept="application/json, text/event-stream",
    )
    body = response["response"].read().decode("utf-8")
    # Handle SSE response: extract JSON from data: lines
    for line in body.splitlines():
        if line.startswith("data:"):
            data = line[5:].strip()
            if data:
                return json.loads(data)
    # Fall back to direct JSON parse
    return json.loads(body) if body else {}


def main():
    config = load_config()
    arn = config["runtime_arn"]
    region = config["region"]
    client = boto3.client("bedrock-agentcore", region_name=region)

    print("═══ MCP Progress Notifications Demo ═══")
    print(f"Runtime: {arn}\n")

    # 1. Initialize
    print("── Initialize ──")
    result = send_jsonrpc(
        client,
        arn,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "demo-client", "version": "1.0.0"},
        },
        msg_id=1,
    )
    print(f"  {json.dumps(result, indent=2)[:500]}\n")

    # 2. List tools
    print("── tools/list ──")
    result = send_jsonrpc(client, arn, "tools/list", msg_id=2)
    print(f"  {json.dumps(result, indent=2)[:500]}\n")

    # 3. First, add some sample data so the report has something to show
    print("── Seeding sample data (add_expense via direct DynamoDB) ──")
    print("  Note: In production, use the 01-server-e2e to add expenses first.\n")

    # 4. Generate report with progress notifications
    print("── tools/call: generate_report (with progress notifications) ──")
    print("  The server sends progress updates at each of 5 steps.\n")
    result = send_jsonrpc(
        client,
        arn,
        "tools/call",
        {
            "name": "generate_report",
            "arguments": {"user_alias": "demo_user"},
        },
        msg_id=3,
    )
    print(f"  {json.dumps(result, indent=2)[:800]}\n")

    print("✓ Demo complete. Run 'python cleanup.py' to delete the runtime.")


if __name__ == "__main__":
    main()
