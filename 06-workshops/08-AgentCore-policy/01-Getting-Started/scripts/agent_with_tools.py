"""
Agent with Tools Module

This module provides functions to create and interact with an agent
that has access to the insurance underwriting tools via AgentCore Gateway.
"""

import json
import os
import requests
from pathlib import Path

from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client


def load_config():
    """Load configuration from config.json"""
    config_path = Path(__file__).parent.parent / "config.json"

    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\nPlease run deploy_lambdas.py and setup_gateway.py first."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Validate required fields
    if "gateway" not in config:
        raise ValueError("Gateway configuration not found in config.json\nPlease run setup_gateway.py first.")

    return config


def create_streamable_http_transport(mcp_url: str, access_token: str):
    """Create streamable HTTP transport for MCP client"""
    return streamablehttp_client(mcp_url, headers={"Authorization": f"Bearer {access_token}"})


def fetch_access_token(client_id, client_secret, token_url):
    """Get access token from Cognito"""
    response = requests.post(
        token_url,
        data=f"grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )

    if response.status_code != 200:
        raise Exception(f"Failed to get access token: {response.text}")

    return response.json()["access_token"]


def list_available_tools(gateway_url: str, access_token: str):
    """List all available tools from the gateway"""
    try:
        mcp_client = MCPClient(lambda: create_streamable_http_transport(gateway_url, access_token))
        with mcp_client:
            tools_list = mcp_client.list_tools_sync()
            # MCPAgentTool may not have description attribute, use getattr with default
            return [(tool.tool_name, getattr(tool, "description", "")) for tool in tools_list]
    except Exception as e:
        print(f"⚠️  Could not list tools: {e}")
        return []


class AgentSession:
    """
    Context manager for agent sessions that properly handles MCP client lifecycle.

    Usage:
        with AgentSession() as session:
            response = session.invoke("What tools do you have?")
    """

    def __init__(self, model_id="us.amazon.nova-lite-v1:0", verbose=True):
        self.model_id = model_id
        self.verbose = verbose
        self.mcp_client = None
        self.agent = None
        self.config = None
        self.gateway_url = None
        self.access_token = None

    def __enter__(self):
        """Setup the agent session"""
        # Load configuration
        if self.verbose:
            print("📦 Loading configuration...")
        self.config = load_config()

        gateway_config = self.config["gateway"]
        client_info = gateway_config["client_info"]

        CLIENT_ID = client_info["client_id"]
        CLIENT_SECRET = client_info["client_secret"]
        TOKEN_URL = client_info["token_endpoint"]
        self.gateway_url = gateway_config["gateway_url"]
        region = self.config.get("region")

        # Set AWS region
        os.environ["AWS_DEFAULT_REGION"] = region

        if self.verbose:
            print("✅ Configuration loaded")
            print(f"   Gateway: {gateway_config.get('gateway_name', 'N/A')}")
            print(f"   Region: {region}")

        # Get access token
        if self.verbose:
            print("\n🔑 Authenticating...")
        self.access_token = fetch_access_token(CLIENT_ID, CLIENT_SECRET, TOKEN_URL)
        if self.verbose:
            print("✅ Authentication successful")

        # List available tools
        if self.verbose:
            print("\n📋 Listing available tools...")
        tool_info = list_available_tools(self.gateway_url, self.access_token)

        if tool_info and self.verbose:
            print(f"✅ Found {len(tool_info)} tool(s):")
            for tool_name, tool_desc in tool_info:
                print(f"   • {tool_name}")
                if tool_desc:
                    print(f"     {tool_desc}")

        # Setup Bedrock model
        if self.verbose:
            print(f"\n🤖 Setting up model: {self.model_id}")
        bedrockmodel = BedrockModel(
            model_id=self.model_id,
            streaming=True,
        )

        # Create MCP client
        self.mcp_client = MCPClient(lambda: create_streamable_http_transport(self.gateway_url, self.access_token))

        # Enter MCP client context
        self.mcp_client.__enter__()

        # Get tools from MCP client
        tools = self.mcp_client.list_tools_sync()

        # Create agent with system prompt
        system_prompt = """You are a helpful AI assistant for insurance underwriting operations.

You have access to tools from the gateway. The gateway is configured with policies which restrict 
tool access. Only use the tools provided by the gateway. Do not make up any information.

When using tools, show which tool you invoked, what you're doing and the results.
If a tool call fails, explain the error clearly to the user."""

        self.agent = Agent(model=bedrockmodel, tools=tools, system_prompt=system_prompt)

        if self.verbose:
            print("✅ Agent ready!\n")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cleanup the agent session"""
        if self.mcp_client:
            try:
                self.mcp_client.__exit__(exc_type, exc_val, exc_tb)
                if self.verbose:
                    print("✅ Agent session closed")
            except Exception as e:
                if self.verbose:
                    print(f"⚠️  Error closing agent session: {e}")

    def invoke(self, prompt, verbose=None):
        """
        Invoke the agent with a prompt.

        Args:
            prompt: The user prompt/question
            verbose: Whether to print the prompt (default: use session verbose setting)

        Returns:
            str: The agent's response
        """
        if verbose is None:
            verbose = self.verbose

        if verbose:
            print(f"💬 Prompt: {prompt}\n")
            print("🤔 Thinking...\n")

        try:
            response = self.agent(prompt)

            # Extract response content
            if hasattr(response, "message"):
                content = response.message.get("content", str(response))
            else:
                content = str(response)

            if verbose:
                print(f"🤖 Agent: {content}\n")

            return content

        except Exception as e:
            error_msg = f"Error: {e}"
            if verbose:
                print(f"❌ {error_msg}\n")
            return error_msg


# Example usage function
def example_usage():
    """Example of how to use this module"""
    print("=" * 70)
    print("🚀 Insurance Underwriting Agent Example")
    print("=" * 70)
    print()

    # Use the agent session context manager
    with AgentSession() as session:
        # Example prompts
        prompts = [
            "What tools do you have access to?",
            "Create an application for US region with $50000 coverage",
        ]

        print("=" * 70)
        print("📝 Running example prompts...")
        print("=" * 70)
        print()

        for prompt in prompts:
            session.invoke(prompt)
            print("-" * 70)
            print()

    print("✅ Done!")


if __name__ == "__main__":
    example_usage()
