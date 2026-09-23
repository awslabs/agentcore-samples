"""
Invoke the MCP server e2e agent using JSON-RPC messages.

Demonstrates: initialize, tools/list, tools/call (add_expense, get_balance),
resources/list, resources/read, prompts/list, prompts/get.

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

    print("═══ MCP Server E2E Demo ═══")
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

    # 3. Add an expense
    print("── tools/call: add_expense ──")
    result = send_jsonrpc(
        client,
        arn,
        "tools/call",
        {
            "name": "add_expense",
            "arguments": {
                "user_alias": "demo_user",
                "amount": 42.50,
                "description": "Lunch meeting",
                "category": "food",
            },
        },
        msg_id=3,
    )
    print(f"  {json.dumps(result, indent=2)[:500]}\n")

    # 4. Get balance
    print("── tools/call: get_balance ──")
    result = send_jsonrpc(
        client,
        arn,
        "tools/call",
        {
            "name": "get_balance",
            "arguments": {"user_alias": "demo_user"},
        },
        msg_id=4,
    )
    print(f"  {json.dumps(result, indent=2)[:500]}\n")

    # 5. List resources
    print("── resources/list ──")
    result = send_jsonrpc(client, arn, "resources/list", msg_id=5)
    print(f"  {json.dumps(result, indent=2)[:500]}\n")

    # 6. List prompts
    print("── prompts/list ──")
    result = send_jsonrpc(client, arn, "prompts/list", msg_id=6)
    print(f"  {json.dumps(result, indent=2)[:500]}\n")

    print("✓ Demo complete. Run 'python cleanup.py' to delete the runtime.")


if __name__ == "__main__":
    main()
