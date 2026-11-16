"""Invoke SpecStream through the deployed Amazon Bedrock AgentCore Gateway.

In 'openapi' mode (default), connects a Strands agent to the Gateway over
SigV4-authenticated MCP. The Gateway injects the stored SpecStream API key.

In 'mcp-passthrough' mode, runs the MCP streamable-http handshake
(initialize -> notifications/initialized -> tools/list, and optionally tools/call)
against {gateway_url}/{target_name}, forwarding SPECSTREAM_API_KEY when set.

Usage:
    python invoke.py
    python invoke.py --query "What is Amazon Bedrock AgentCore Gateway?"
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from config import (
    AWS_REGION,
    MCP_PROTOCOL_VERSION,
    MODEL_ID,
    STATE_FILE,
    build_passthrough_headers,
    load_env_file,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for invocation."""
    parser = argparse.ArgumentParser(description="Invoke SpecStream via AgentCore Gateway")
    parser.add_argument(
        "--query",
        default=None,
        help="Optional single query to run instead of the default sample prompts.",
    )
    return parser.parse_args(argv)


def parse_sse_or_json(text: str) -> dict:
    """Extract a JSON-RPC payload from an SSE or plain JSON MCP response."""
    if "data:" in text:
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
    return json.loads(text) if text.strip() else {}


def _invoke_openapi_agent(state: dict, query: str | None) -> int:
    """Run a Strands agent connected to the OpenAPI SpecStream Gateway target."""
    from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
    from strands import Agent
    from strands.models import BedrockModel
    from strands.tools.mcp import MCPClient

    gateway_url = state["gateway_url"]
    region = state.get("region", AWS_REGION)

    print(f"Gateway: {gateway_url}")
    print(f"Model:   {MODEL_ID}")

    mcp_client = MCPClient(
        lambda: aws_iam_streamablehttp_client(
            endpoint=gateway_url,
            aws_region=region,
            aws_service="bedrock-agentcore",
        )
    )

    prompts = (
        [query]
        if query
        else [
            "Search the web for the latest release notes of Amazon Bedrock AgentCore and cite your sources.",
            "Extract the main text from https://specstream.ai/agents.md and summarize the Search API tiers.",
        ]
    )

    model = BedrockModel(model_id=MODEL_ID, region_name=region)
    with mcp_client:
        tools = mcp_client.list_tools_sync()
        print(f"Loaded {len(tools)} MCP tool(s) from gateway.")
        agent = Agent(model=model, tools=tools)
        for prompt in prompts:
            print("-" * 60)
            print(f"Prompt: {prompt}")
            print("-" * 60)
            response = agent(prompt)
            print(response)
            print()

    return 0


def _invoke_passthrough(state: dict, query: str | None) -> int:
    """Call SpecStream remote MCP through the gateway HTTP passthrough target."""
    import requests

    gateway_url = state["gateway_url"].rstrip("/")
    target_name = state["target_name"]
    url = f"{gateway_url}/{target_name}"
    api_key = os.environ.get("SPECSTREAM_API_KEY")

    headers = build_passthrough_headers(api_key=api_key, protocol_version=MCP_PROTOCOL_VERSION)
    print(f"Passthrough URL: {url}")
    print(f"SpecStream key:  {'set' if api_key else 'not set'}")

    init_resp = requests.post(
        url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "agentcore-specstream-sample", "version": "1.0"},
            },
        },
        timeout=60,
    )
    init_resp.raise_for_status()
    session_id = init_resp.headers.get("mcp-session-id")
    headers = build_passthrough_headers(
        api_key=api_key,
        session_id=session_id,
        protocol_version=MCP_PROTOCOL_VERSION,
    )

    notif_resp = requests.post(
        url,
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        timeout=60,
    )
    notif_resp.raise_for_status()

    list_resp = requests.post(
        url,
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        timeout=60,
    )
    list_resp.raise_for_status()
    tools_payload = parse_sse_or_json(list_resp.text)
    tools = tools_payload.get("result", {}).get("tools", [])
    print(f"Available MCP tools ({len(tools)}):")
    for tool in tools:
        print(f"  - {tool.get('name')}: {tool.get('description', '').splitlines()[0]}")

    if query:
        call_resp = requests.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "search_web", "arguments": {"q": query}},
            },
            timeout=60,
        )
        call_resp.raise_for_status()
        print("\nsearch_web result:")
        print(json.dumps(parse_sse_or_json(call_resp.text), indent=2))

    return 0


def main(argv: list[str] | None = None) -> int:
    """Load deployment state and run the selected invocation mode."""
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    args = parse_args(argv)

    if not os.path.exists(STATE_FILE):
        print(f"ERROR: No deployment state found at {STATE_FILE}. Run 'python deploy.py' first.")
        return 1

    with open(STATE_FILE, encoding="utf-8") as f:
        state = json.load(f)

    if state.get("mode") == "mcp-passthrough":
        return _invoke_passthrough(state, args.query)
    return _invoke_openapi_agent(state, args.query)


if __name__ == "__main__":
    sys.exit(main())
