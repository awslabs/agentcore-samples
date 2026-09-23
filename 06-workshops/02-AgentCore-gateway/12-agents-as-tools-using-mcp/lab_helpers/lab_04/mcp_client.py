#!/usr/bin/env python3
"""
Lab 03: MCP Client Helper

Provides a simple MCP client for connecting to the AgentCore Gateway
and invoking MCP tools with Cognito JWT authentication.

Key Features:
- Cognito JWT authentication
- MCP protocol (initialize, tools/list, tools/call)
- Gateway connection management
- Simple interface for tool invocation

Usage:
    from lab_helpers.lab_03.mcp_client import MCPClient

    client = MCPClient(gateway_url, cognito_token)
    client.initialize()
    tools = client.list_tools()
    result = client.call_tool("tool_name", {"arg": "value"})
"""

import requests
import json
from typing import Dict, List, Any, Optional


class MCPClient:
    """
    MCP Client for connecting to AgentCore Gateway.

    This client handles:
    - JWT authentication with Cognito tokens
    - MCP protocol (JSON-RPC 2.0)
    - Session initialization
    - Tool discovery and invocation
    """

    def __init__(self, gateway_url: str, access_token: str, timeout: int = 900):
        """
        Initialize MCP Client.

        Args:
            gateway_url: Gateway MCP endpoint URL
            access_token: Cognito JWT access token
            timeout: Request timeout in seconds (default: 30)
        """
        self.gateway_url = gateway_url
        self.access_token = access_token
        self.timeout = timeout
        self.request_id = 0
        self.initialized = False
        self.server_info = {}

    def _next_request_id(self) -> int:
        """Generate next request ID for JSON-RPC"""
        self.request_id += 1
        return self.request_id

    def _mcp_request(self, method: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Make MCP JSON-RPC request to Gateway.

        Args:
            method: MCP method name (e.g., "initialize", "tools/list", "tools/call")
            params: Method parameters (optional)

        Returns:
            JSON-RPC response as dictionary

        Raises:
            requests.HTTPError: If HTTP request fails
            ValueError: If response contains error
        """
        request_payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": method,
        }

        if params is not None:
            request_payload["params"] = params

        response = requests.post(
            self.gateway_url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            },
            json=request_payload,
            timeout=self.timeout,
        )

        response.raise_for_status()
        result = response.json()

        # Check for JSON-RPC errors
        if "error" in result:
            error = result["error"]
            raise ValueError(f"MCP Error [{error.get('code')}]: {error.get('message')}")

        return result

    def initialize(self, client_name: str = "aiml301-mcp-client", client_version: str = "1.0.0") -> Dict[str, Any]:
        """
        Initialize MCP session with Gateway.

        This must be called before any other MCP operations.

        Args:
            client_name: Client application name
            client_version: Client version string

        Returns:
            Server info from initialize response

        Example:
            >>> client.initialize()
            {'name': 'aiml301-remediation-gateway', 'version': '1.0.0'}
        """
        print("🚀 Initializing MCP session...")

        response = self._mcp_request(
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": client_version},
            },
        )

        if "result" in response:
            self.server_info = response["result"].get("serverInfo", {})
            self.initialized = True

            print("  ✅ Session initialized")
            print(f"     Server: {self.server_info.get('name', 'Unknown')}")
            print(f"     Version: {self.server_info.get('version', 'Unknown')}")

            return self.server_info
        else:
            raise ValueError("Initialize failed: No result in response")

    def list_tools(self) -> List[Dict[str, Any]]:
        """
        List all available MCP tools from Gateway.

        Returns:
            List of tool definitions with name, description, and schema

        Example:
            >>> tools = client.list_tools()
            >>> print(f"Found {len(tools)} tools")
            >>> for tool in tools:
            >>>     print(f"  - {tool['name']}: {tool['description']}")
        """
        if not self.initialized:
            raise RuntimeError("Client not initialized. Call initialize() first.")

        print("\n🔧 Listing available tools...")

        response = self._mcp_request(method="tools/list", params={})

        if "result" in response:
            tools = response["result"].get("tools", [])
            print(f"  ✅ Found {len(tools)} tool(s)")

            for i, tool in enumerate(tools, 1):
                tool_name = tool.get("name", "unnamed")
                # Get first line of description
                description = tool.get("description", "No description")
                first_line = description.split("\n")[0]
                print(f"     {i}. {tool_name}")
                print(f"        {first_line[:80]}...")

            return tools
        else:
            raise ValueError("List tools failed: No result in response")

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Invoke an MCP tool with arguments.

        Args:
            tool_name: Name of the tool to invoke
            arguments: Tool arguments as dictionary

        Returns:
            Tool execution result

        Example:
            >>> result = client.call_tool(
            ...     "ddgs_search",
            ...     {"query": "AWS Bedrock features", "max_results": 3}
            ... )
            >>> print(result)
        """
        if not self.initialized:
            raise RuntimeError("Client not initialized. Call initialize() first.")

        print(f"\n🔨 Calling tool: {tool_name}")
        print(f"   Arguments: {json.dumps(arguments, indent=2)}")

        response = self._mcp_request(method="tools/call", params={"name": tool_name, "arguments": arguments})

        if "result" in response:
            result = response["result"]
            print("  ✅ Tool execution successful")

            # Try to extract and display content
            if "content" in result:
                for content_item in result["content"]:
                    if content_item.get("type") == "text":
                        try:
                            # Try to parse as JSON for better display
                            text_content = content_item["text"]
                            parsed = json.loads(text_content)
                            print("\n  📋 Result:")
                            print(f"     {json.dumps(parsed, indent=6)}")
                        except (json.JSONDecodeError, KeyError):
                            print(f"\n  📋 Result: {content_item['text'][:500]}...")

            return result
        else:
            raise ValueError("Tool call failed: No result in response")

    def close(self):
        """Close MCP session (cleanup if needed)"""
        self.initialized = False
        print("\n✅ MCP session closed")


def create_mcp_client(gateway_url: str, cognito_token: str) -> MCPClient:
    """
    Factory function to create and initialize MCP client.

    Args:
        gateway_url: Gateway MCP endpoint URL
        cognito_token: Cognito JWT access token

    Returns:
        Initialized MCPClient instance

    Example:
        >>> from lab_helpers.lab_03.mcp_client import create_mcp_client
        >>> client = create_mcp_client(gateway_url, token)
        >>> tools = client.list_tools()
    """
    client = MCPClient(gateway_url, cognito_token)
    client.initialize()
    return client
