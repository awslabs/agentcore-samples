#!/usr/bin/env python3
"""
Authentication MCP Server

Provides authentication and identity verification tools via Model Context Protocol.
All tools return static JSON responses for demonstration purposes.
"""

import asyncio
import json
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server
server = Server("auth-tools")


# ============================================================================
# Tool Functions
# ============================================================================


def authenticate_user(username: str, account_number: str) -> str:
    """
    Authenticate a user with their username and account number.

    Args:
        username: The customer's username
        account_number: The customer's account number

    Returns:
        Authentication status and user information
    """
    response = {
        "status": "success",
        "authenticated": True,
        "user": {
            "username": username,
            "account_number": account_number,
            "full_name": "John Doe",
            "customer_id": "CUST-001",
            "member_since": "2020-01-15",
            "account_type": "Premium Checking",
        },
        "session_token": "tok_demo_abc123xyz789",  # pragma: allowlist secret
        "message": f"Welcome back, {username}! Authentication successful.",
    }

    return json.dumps(response, indent=2)


def verify_identity(customer_id: str, last_four_ssn: str) -> str:
    """
    Verify customer identity using customer ID and last 4 digits of SSN.

    Args:
        customer_id: The customer's unique identifier
        last_four_ssn: Last 4 digits of Social Security Number

    Returns:
        Identity verification status
    """
    response = {
        "status": "success",
        "verified": True,
        "customer_id": customer_id,
        "verification_level": "high",
        "verification_methods": ["SSN", "Account History", "Device Recognition"],
        "message": "Identity verified successfully. You may proceed with sensitive operations.",
    }

    return json.dumps(response, indent=2)


# ============================================================================
# MCP Server Configuration
# ============================================================================

TOOLS = [
    Tool(
        name="authenticate_user",
        description="Authenticate a user with their username and account number",
        inputSchema={
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "The customer's username",
                },
                "account_number": {
                    "type": "string",
                    "description": "The customer's account number",
                },
            },
            "required": ["username", "account_number"],
        },
    ),
    Tool(
        name="verify_identity",
        description="Verify customer identity using customer ID and last 4 digits of SSN",
        inputSchema={
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "The customer's unique identifier",
                },
                "last_four_ssn": {
                    "type": "string",
                    "description": "Last 4 digits of Social Security Number",
                },
            },
            "required": ["customer_id", "last_four_ssn"],
        },
    ),
]

TOOL_FUNCTIONS = {
    "authenticate_user": authenticate_user,
    "verify_identity": verify_identity,
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools"""
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Call a tool with the given arguments"""
    if name not in TOOL_FUNCTIONS:
        raise ValueError(f"Unknown tool: {name}")

    try:
        func = TOOL_FUNCTIONS[name]
        result = func(**arguments)
        return [TextContent(type="text", text=result)]
    except Exception as e:
        logger.error(f"Error calling tool {name}: {e}")
        raise


async def main():
    """Run the MCP server"""
    logger.info("Starting Authentication Tools MCP Server")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
