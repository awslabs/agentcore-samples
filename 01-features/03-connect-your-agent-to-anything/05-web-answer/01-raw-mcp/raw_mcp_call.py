"""
Discover and invoke the AgentCore Web Answer Tool with raw MCP calls.

This example exercises the MCP protocol directly without an agent framework:
  1. Connect to AgentCore Gateway using MCP Streamable HTTP
  2. Call tools/list to discover WebAnswer and its live input schema
  3. Map the user question to the input field advertised by that schema
  4. Call tools/call and display the grounded answer and citations

Use this script to verify the Gateway target and inspect the managed tool's
response before integrating it with an agent framework.

Prerequisites:
    pip install -r ../requirements.txt
    Run setup_gateway.py and load the generated .env.web-answer file

Environment variables required:
    AGENTCORE_GATEWAY_URL  — Gateway MCP endpoint
    COGNITO_DOMAIN         — Cognito domain prefix
    COGNITO_CLIENT_ID      — Cognito machine-to-machine app client ID
    COGNITO_CLIENT_SECRET  — Cognito app client secret
    COGNITO_SCOPE          — OAuth scope string

Usage:
    python raw_mcp_call.py
    python raw_mcp_call.py --question "What is the current Node.js LTS release?"
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strands.tools.mcp.mcp_client import MCPClient
from utils.gateway_auth import create_streamable_http_transport

DEFAULT_QUESTION = "What is the latest stable release of Python, and when was it released?"


def parse_args():
    parser = argparse.ArgumentParser(description="Invoke the Web Answer Tool through an AgentCore Gateway")
    parser.add_argument(
        "--question",
        default=DEFAULT_QUESTION,
        help=f"Question to answer (default: '{DEFAULT_QUESTION}')",
    )
    return parser.parse_args()


def input_arguments(tool, question: str) -> dict:
    """Build a WebAnswer invocation payload from its discovered input schema.

    The managed tool schema may expose the natural-language input as either
    ``question`` or ``query``. Inspecting the live schema keeps this example
    compatible with both representations instead of hard-coding one field.

    Args:
        tool: Discovered MCP tool whose ``tool_spec`` contains ``inputSchema``.
        question: Natural-language question to send to Web Answer.

    Returns:
        A one-field argument dictionary accepted by the discovered tool.

    Raises:
        ValueError: If the schema exposes neither ``question`` nor ``query``.
    """
    schema = tool.tool_spec.get("inputSchema", {})
    # Strands represents a Bedrock tool schema as {"json": <JSON Schema>}.
    schema = schema.get("json", schema)
    properties = schema.get("properties", {})
    for field_name in ("question", "query"):
        if field_name in properties:
            return {field_name: question}
    raise ValueError(
        f"WebAnswer exposes neither a 'question' nor a 'query' input. Discovered schema: {json.dumps(schema)}"
    )


def display_result(result) -> None:
    """Print a WebAnswer result in its most structured available form.

    ``structuredContent`` is preferred because it preserves the managed tool's
    response fields. If it is absent, the function reads standard MCP content
    blocks, pretty-prints JSON text, and otherwise prints text or unknown blocks
    without discarding them.

    Args:
        result: Value returned by ``MCPClient.call_tool_sync``. It may be a
            mapping, a list of content blocks, or an MCP result object.
    """
    if isinstance(result, dict) and result.get("structuredContent"):
        print(json.dumps(result["structuredContent"], indent=2))
        return

    if isinstance(result, dict):
        content_blocks = result.get("content", [result])
    elif isinstance(result, list):
        content_blocks = result
    else:
        content_blocks = getattr(result, "content", [result])

    for block in content_blocks:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text is None:
            print(block)
            continue
        try:
            print(json.dumps(json.loads(text), indent=2))
        except (json.JSONDecodeError, TypeError):
            print(text)


def main():
    args = parse_args()

    print("=" * 60)
    print("AgentCore Web Answer Tool — Raw MCP Calls")
    print("=" * 60)

    mcp_client = MCPClient(create_streamable_http_transport())
    with mcp_client:
        print("\n[1] Discovering tools (tools/list)...\n")
        tools = mcp_client.list_tools_sync()
        print(f"  Found {len(tools)} tool(s):\n")
        for tool in tools:
            spec = tool.tool_spec
            print(f"  Name:        {spec['name']}")
            print(f"  Description: {spec.get('description', 'N/A')}")
            print(f"  Input:       {json.dumps(spec.get('inputSchema', {}), indent=2)}\n")

        answer_tools = [tool for tool in tools if "WebAnswer" in tool.tool_name]
        if not answer_tools:
            raise RuntimeError("WebAnswer tool not found. Check the Gateway target status.")

        tool = answer_tools[0]
        print("=" * 60)
        print(f"[2] Calling {tool.tool_name}: '{args.question}'")
        print("=" * 60 + "\n")
        result = mcp_client.call_tool_sync(
            "raw-mcp-demo",
            tool.tool_name,
            input_arguments(tool, args.question),
        )

        print("[3] Grounded answer and citations:\n")
        display_result(result)

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
