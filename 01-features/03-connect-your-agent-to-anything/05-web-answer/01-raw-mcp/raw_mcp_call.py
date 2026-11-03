"""Discover and invoke the AgentCore Web Answer Tool with raw MCP calls."""

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
    """Build arguments from the discovered schema instead of assuming its field name."""
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
    """Print structured content when available, otherwise print MCP text blocks."""
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
