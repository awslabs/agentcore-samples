"""
Insurance Underwriting Agent with MCP Tools via AgentCore Gateway.

Provides an AgentSession context manager that connects a Strands agent to the
tools hosted on the AgentCore Gateway, authenticated via Cognito OAuth.
"""

import json
import os
import requests
from pathlib import Path

from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client


def load_config(config_path: str = "policy_config.json") -> dict:
    """Load policy configuration from policy_config.json."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}\nPlease run deploy.py first.")
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)
    if "gateway" not in config:
        raise ValueError("Gateway configuration missing from policy_config.json. Run deploy.py first.")
    return config


def fetch_access_token(client_id: str, client_secret: str, token_url: str) -> str:
    """Obtain an OAuth2 client_credentials access token from Cognito."""
    response = requests.post(
        token_url,
        data=f"grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Failed to get access token: {response.text}")
    return response.json()["access_token"]


def list_available_tools(gateway_url: str, access_token: str) -> list:
    """List tools currently visible through the Gateway (policy-filtered)."""
    try:
        mcp_client = MCPClient(
            lambda: streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {access_token}"})
        )
        with mcp_client:
            tools_list = mcp_client.list_tools_sync()
            return [(t.tool_name, getattr(t, "description", "")) for t in tools_list]
    except Exception as exc:
        print(f"  Could not list tools: {exc}")
        return []


class AgentSession:
    """
    Context manager for an insurance underwriting agent session.

    The agent connects to tools hosted on the AgentCore Gateway via MCP.
    Gateway policies control which tools are visible and callable.

    Usage:
        with AgentSession() as session:
            response = session.invoke("Create application for US region with $500K coverage")
    """

    def __init__(self, model_id: str = "us.amazon.nova-lite-v1:0", verbose: bool = True):
        self.model_id = model_id
        self.verbose = verbose
        self.mcp_client = None
        self.agent = None
        self.config = None

    def __enter__(self):
        self.config = load_config()
        gateway_cfg = self.config["gateway"]
        client_info = gateway_cfg["client_info"]

        region = self.config.get("region")
        os.environ["AWS_DEFAULT_REGION"] = region

        if self.verbose:
            print(f"  Gateway: {gateway_cfg.get('gateway_id', 'N/A')}")
            print(f"  Region:  {region}")

        access_token = fetch_access_token(
            client_info["client_id"],
            client_info["client_secret"],
            client_info["token_endpoint"],
        )

        if self.verbose:
            tools = list_available_tools(gateway_cfg["gateway_url"], access_token)
            print(f"  Available tools ({len(tools)}):")
            for name, desc in tools:
                print(f"    • {name}")

        bedrock_model = BedrockModel(model_id=self.model_id, streaming=True)
        self.mcp_client = MCPClient(
            lambda: streamablehttp_client(
                gateway_cfg["gateway_url"],
                headers={"Authorization": f"Bearer {access_token}"},
            )
        )
        self.mcp_client.__enter__()
        tools = self.mcp_client.list_tools_sync()

        system_prompt = (
            "You are a helpful AI assistant for insurance underwriting operations. "
            "You have access to tools provided by the gateway. The gateway enforces "
            "Cedar policies that may restrict tool access based on your identity and "
            "request parameters. Use only the tools provided — do not fabricate data. "
            "When a tool call is denied by policy, explain the denial to the user."
        )
        self.agent = Agent(model=bedrock_model, tools=tools, system_prompt=system_prompt)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.mcp_client:
            try:
                self.mcp_client.__exit__(exc_type, exc_val, exc_tb)
            except Exception:
                pass

    def invoke(self, prompt: str) -> str:
        """Invoke the agent with a prompt and return the response text."""
        print(f"\n  Prompt: {prompt}")
        try:
            response = self.agent(prompt)
            content = response.message.get("content", str(response)) if hasattr(response, "message") else str(response)
            print(f"  Response: {content}")
            return content
        except Exception as exc:
            msg = f"Error: {exc}"
            print(f"  {msg}")
            return msg
