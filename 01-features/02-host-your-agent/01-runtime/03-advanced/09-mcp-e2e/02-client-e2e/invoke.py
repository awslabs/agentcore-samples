"""
Invoke the MCP client features e2e agent using JSON-RPC messages.

Demonstrates: initialize, tools/list, tools/call with elicitation
(add_expense_interactive) and sampling (analyze_spending).

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

    print("═══ MCP Client Features E2E Demo ═══")
    print(f"Runtime: {arn}\n")

    # 1. Initialize
    # Note: elicitation and sampling require a bidirectional streaming connection.
    # For stateless HTTP invocation via AgentCore's invoke_agent_runtime, use empty
    # capabilities. In a production client, implement SSE stream handling to respond
    # to server-initiated elicitation/sampling requests.
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

    # 3. Add an expense (non-interactive)
    print("── tools/call: add_expense ──")
    result = send_jsonrpc(
        client,
        arn,
        "tools/call",
        {
            "name": "add_expense",
            "arguments": {
                "user_alias": "demo_user",
                "amount": 25.00,
                "description": "Coffee and snacks",
                "category": "food",
            },
        },
        msg_id=3,
    )
    print(f"  {json.dumps(result, indent=2)[:500]}\n")

    # 4. Interactive expense (elicitation) — requires bidirectional client
    # The add_expense_interactive tool uses ctx.elicit() to interactively gather
    # expense details. Elicitation is a bidirectional MCP capability: the server
    # sends elicitation requests back to the client mid-invocation and waits for
    # responses. This requires a persistent SSE session (not stateless HTTP).
    # With stateless invoke_agent_runtime, this call would hang indefinitely.
    # To test elicitation, use a full MCP client that supports bidirectional streams.
    print("── tools/call: add_expense_interactive (elicitation — skipped) ──")
    print("  Note: Elicitation requires a bidirectional MCP client with SSE session")
    print("  support. The add_expense_interactive tool is deployed and available,")
    print("  but cannot be demoed via stateless invoke_agent_runtime.\n")

    # 5. Analyze spending (sampling) — server delegates LLM call to client
    print("── tools/call: analyze_spending (sampling) ──")
    result = send_jsonrpc(
        client,
        arn,
        "tools/call",
        {
            "name": "analyze_spending",
            "arguments": {"user_alias": "demo_user"},
        },
        msg_id=5,
    )
    print(f"  {json.dumps(result, indent=2)[:500]}\n")

    print("✓ Demo complete. Run 'python cleanup.py' to delete the runtime.")


if __name__ == "__main__":
    main()
