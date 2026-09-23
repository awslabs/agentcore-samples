#!/usr/bin/env python3
"""
Banking MCP Server

Provides banking and account management tools via Model Context Protocol.
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
server = Server("banking-tools")


# ============================================================================
# Tool Functions
# ============================================================================


def get_account_balance(account_number: str) -> str:
    """
    Get the current balance for a bank account.

    Args:
        account_number: The account number to check

    Returns:
        Account balance information
    """
    response = {
        "status": "success",
        "account_number": account_number,
        "account_type": "Premium Checking",
        "balances": {"available": 15234.56, "current": 15734.56, "pending": 500.00},
        "currency": "USD",
        "last_updated": "2024-03-04T10:30:00Z",
        "message": "Your available balance is $15,234.56",
    }

    return json.dumps(response, indent=2)


def get_recent_transactions(account_number: str, limit: int = 5) -> str:
    """
    Get recent transactions for an account.

    Args:
        account_number: The account number to check
        limit: Number of transactions to return (default: 5)

    Returns:
        List of recent transactions
    """
    response = {
        "status": "success",
        "account_number": account_number,
        "transactions": [
            {
                "id": "TXN-001",
                "date": "2024-03-04",
                "description": "Amazon.com Purchase",
                "amount": -89.99,
                "type": "debit",
                "category": "Shopping",
                "balance_after": 15234.56,
            },
            {
                "id": "TXN-002",
                "date": "2024-03-03",
                "description": "Salary Deposit",
                "amount": 5000.00,
                "type": "credit",
                "category": "Income",
                "balance_after": 15324.55,
            },
            {
                "id": "TXN-003",
                "date": "2024-03-02",
                "description": "Grocery Store",
                "amount": -156.78,
                "type": "debit",
                "category": "Food & Dining",
                "balance_after": 10324.55,
            },
            {
                "id": "TXN-004",
                "date": "2024-03-01",
                "description": "Electric Bill Payment",
                "amount": -125.00,
                "type": "debit",
                "category": "Utilities",
                "balance_after": 10481.33,
            },
            {
                "id": "TXN-005",
                "date": "2024-02-28",
                "description": "ATM Withdrawal",
                "amount": -200.00,
                "type": "debit",
                "category": "Cash",
                "balance_after": 10606.33,
            },
        ][:limit],
        "message": f"Showing {min(limit, 5)} most recent transactions",
    }

    return json.dumps(response, indent=2)


def transfer_funds(from_account: str, to_account: str, amount: float) -> str:
    """
    Transfer funds between accounts.

    Args:
        from_account: Source account number
        to_account: Destination account number
        amount: Amount to transfer

    Returns:
        Transfer confirmation
    """
    response = {
        "status": "success",
        "transfer_id": "TRF-20240304-001",
        "from_account": from_account,
        "to_account": to_account,
        "amount": amount,
        "currency": "USD",
        "timestamp": "2024-03-04T10:35:00Z",
        "estimated_completion": "2024-03-04T10:35:30Z",
        "fee": 0.00,
        "new_balance": 15234.56 - amount,
        "message": f"Successfully transferred ${amount:.2f} from account {from_account} to {to_account}",
    }

    return json.dumps(response, indent=2)


def get_account_summary(customer_id: str) -> str:
    """
    Get a summary of all accounts for a customer.

    Args:
        customer_id: The customer's unique identifier

    Returns:
        Summary of all customer accounts
    """
    response = {
        "status": "success",
        "customer_id": customer_id,
        "customer_name": "John Doe",
        "accounts": [
            {
                "account_number": "1234567890",
                "account_type": "Premium Checking",
                "balance": 15234.56,
                "status": "active",
                "opened_date": "2020-01-15",
            },
            {
                "account_number": "1234567891",
                "account_type": "Savings Account",
                "balance": 45678.90,
                "status": "active",
                "interest_rate": 2.5,
                "opened_date": "2020-01-15",
            },
            {
                "account_number": "1234567892",
                "account_type": "Credit Card",
                "balance": -2345.67,
                "credit_limit": 10000.00,
                "available_credit": 7654.33,
                "status": "active",
                "opened_date": "2020-06-01",
            },
        ],
        "total_assets": 60913.46,
        "total_liabilities": 2345.67,
        "net_worth": 58567.79,
        "message": "Account summary retrieved successfully",
    }

    return json.dumps(response, indent=2)


# ============================================================================
# MCP Server Configuration
# ============================================================================

TOOLS = [
    Tool(
        name="get_account_balance",
        description="Get the current balance for a bank account",
        inputSchema={
            "type": "object",
            "properties": {
                "account_number": {
                    "type": "string",
                    "description": "The account number to check",
                }
            },
            "required": ["account_number"],
        },
    ),
    Tool(
        name="get_recent_transactions",
        description="Get recent transactions for an account",
        inputSchema={
            "type": "object",
            "properties": {
                "account_number": {
                    "type": "string",
                    "description": "The account number to check",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of transactions to return",
                    "default": 5,
                },
            },
            "required": ["account_number"],
        },
    ),
    Tool(
        name="transfer_funds",
        description="Transfer funds between accounts",
        inputSchema={
            "type": "object",
            "properties": {
                "from_account": {
                    "type": "string",
                    "description": "Source account number",
                },
                "to_account": {
                    "type": "string",
                    "description": "Destination account number",
                },
                "amount": {"type": "number", "description": "Amount to transfer"},
            },
            "required": ["from_account", "to_account", "amount"],
        },
    ),
    Tool(
        name="get_account_summary",
        description="Get a summary of all accounts for a customer",
        inputSchema={
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "The customer's unique identifier",
                }
            },
            "required": ["customer_id"],
        },
    ),
]

TOOL_FUNCTIONS = {
    "get_account_balance": get_account_balance,
    "get_recent_transactions": get_recent_transactions,
    "transfer_funds": transfer_funds,
    "get_account_summary": get_account_summary,
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
    logger.info("Starting Banking Tools MCP Server")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
