# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Routing Tools for Coordinator Agent.

MCP-compatible tool definitions for intent-based routing to specialized agents.
These tools help the coordinator determine which agent should handle a request.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_routing_tools(router: Any, user_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get MCP tool definitions for routing operations.

    Args:
        router: SubAgentRouter instance
        user_context: User authentication context

    Returns:
        List of tool definitions in Claude's tool format
    """
    tools = [
        {
            "name": "route_to_accounts_agent",
            "description": (
                "Route a query to the Accounts Agent for account-related questions. "
                "Use this when the customer asks about:\n"
                "- Account balances\n"
                "- Account types (checking, savings, etc.)\n"
                "- Account numbers or details\n"
                "- Opening or closing accounts\n"
                "- Account statements\n"
                "- Account fees or interest rates"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The customer's account-related question or request",
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Optional specific account ID if mentioned",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "route_to_profile_agent",
            "description": (
                "Route a query to the Customer Profile Agent for profile-related questions. "
                "Use this when the customer asks about:\n"
                "- Personal information (name, address, phone, email)\n"
                "- Updating contact details\n"
                "- Communication preferences\n"
                "- Account settings\n"
                "- Notification preferences\n"
                "- Language preferences"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The customer's profile-related question or request",
                    }
                },
                "required": ["query"],
            },
        },
        {
            "name": "get_available_agents",
            "description": (
                "Get a list of available specialized agents and their capabilities. "
                "Use this to inform the customer about what services are available."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
    ]

    logger.info(f"Generated {len(tools)} routing tools")
    return tools


# Tool execution handlers


async def execute_route_to_accounts_agent(
    router: Any,
    user_context: Dict[str, Any],
    query: str,
    account_id: Optional[str] = None,
) -> str:
    """
    Execute route_to_accounts_agent tool.

    Args:
        router: SubAgentRouter instance
        user_context: User authentication context
        query: Customer query
        account_id: Optional account ID

    Returns:
        JSON response from accounts agent
    """
    tool_input = {"query": query, "user_context": user_context}

    if account_id:
        tool_input["account_id"] = account_id

    return await router.route_to_accounts("accounts_query", tool_input)


async def execute_route_to_profile_agent(router: Any, user_context: Dict[str, Any], query: str) -> str:
    """
    Execute route_to_profile_agent tool.

    Args:
        router: SubAgentRouter instance
        user_context: User authentication context
        query: Customer query

    Returns:
        JSON response from profile agent
    """
    tool_input = {"query": query, "user_context": user_context}

    return await router.route_to_profile("profile_query", tool_input)


async def execute_get_available_agents(router: Any, user_context: Dict[str, Any]) -> str:
    """
    Execute get_available_agents tool.

    Args:
        router: SubAgentRouter instance
        user_context: User authentication context

    Returns:
        JSON response with available agents
    """
    import json

    available = router.get_available_agents()
    config = router.validate_agent_configuration()

    agent_descriptions = {
        "profile": {
            "name": "Customer Profile Agent",
            "description": "Handles customer profile information, contact details, and preferences",
            "capabilities": [
                "View profile information",
                "Update address",
                "Update phone number",
                "Update email",
                "Manage communication preferences",
            ],
        },
        "accounts": {
            "name": "Accounts Agent",
            "description": "Manages bank accounts and account information",
            "capabilities": [
                "View account balances",
                "View account details",
                "List all accounts",
                "View account statements",
                "View account fees",
            ],
        },
    }

    available_agents = {
        agent: {
            **agent_descriptions[agent],
            "status": "available" if config[agent] else "not_configured",
        }
        for agent in agent_descriptions.keys()
    }

    return json.dumps(
        {
            "available_agents": available_agents,
            "configured_count": len(available),
            "total_agents": len(agent_descriptions),
        },
        indent=2,
    )


# Intent classification helper


def classify_intent(query: str) -> str:
    """
    Classify user intent from query text.

    Args:
        query: User query string

    Returns:
        Detected intent (profile, accounts, unknown)
    """
    query_lower = query.lower()

    # Profile keywords
    profile_keywords = [
        "profile",
        "address",
        "phone",
        "email",
        "contact",
        "preferences",
        "settings",
        "notification",
        "update my",
        "change my",
    ]

    # Account keywords
    account_keywords = [
        "balance",
        "account",
        "checking",
        "savings",
        "statement",
        "interest",
        "fee",
        "open account",
        "close account",
    ]

    # Count keyword matches
    profile_score = sum(1 for kw in profile_keywords if kw in query_lower)
    account_score = sum(1 for kw in account_keywords if kw in query_lower)

    # Determine highest score
    scores = {"profile": profile_score, "accounts": account_score}

    max_score = max(scores.values())

    if max_score == 0:
        return "unknown"

    # Return intent with highest score
    return max(scores, key=scores.get)
