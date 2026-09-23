# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Profile Agent Tools for Coordinator.

MCP-compatible tool definitions for interacting with the Customer Profile Agent.
These tools allow the coordinator to route profile-related requests to the
specialized profile agent.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def get_profile_tools(router: Any, user_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get MCP tool definitions for profile agent operations.

    Args:
        router: SubAgentRouter instance for routing to profile agent
        user_context: User authentication context

    Returns:
        List of tool definitions in Claude's tool format
    """
    # Check if user has required permissions (fine-grained + legacy backward compat)
    permissions = user_context.get("permissions", [])
    perm_set = set(permissions)
    has_read = bool(
        perm_set
        & {
            "profile:personal:read",
            "profile:preferences:read",
        }
    )
    has_write = bool(
        perm_set
        & {
            "profile:personal:write",
            "profile:preferences:write",
        }
    )

    tools = []

    # Read-only tools (require profile:personal:read or profile:preferences:read)
    if has_read:
        tools.extend(
            [
                {
                    "name": "profile_get_customer_profile",
                    "description": (
                        "Retrieve the complete customer profile information including "
                        "personal details, contact information, and preferences. "
                        "Use this when the customer asks about their profile, "
                        "personal information, or account details."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "include_preferences": {
                                "type": "boolean",
                                "description": "Include customer preferences in the response",
                                "default": True,
                            },
                            "include_contact": {
                                "type": "boolean",
                                "description": "Include contact information in the response",
                                "default": True,
                            },
                        },
                        "required": [],
                    },
                }
            ]
        )

    # Write tools (require profile:personal:write or profile:preferences:write)
    if has_write:
        tools.extend(
            [
                {
                    "name": "profile_update_customer_address",
                    "description": (
                        "Update the customer's address information. Use this when "
                        "the customer wants to change their mailing address, street address, "
                        "city, state, or postal code."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "street_address": {
                                "type": "string",
                                "description": "Street address (number and street name)",
                            },
                            "street_address_2": {
                                "type": "string",
                                "description": "Additional address line (apartment, suite, etc.)",
                            },
                            "city": {"type": "string", "description": "City name"},
                            "state": {
                                "type": "string",
                                "description": "State or province (2-letter code preferred)",
                            },
                            "postal_code": {
                                "type": "string",
                                "description": "ZIP or postal code",
                            },
                            "country": {
                                "type": "string",
                                "description": "Country name or 2-letter country code",
                                "default": "US",
                            },
                        },
                        "required": ["street_address", "city", "state", "postal_code"],
                    },
                },
                {
                    "name": "profile_update_customer_phone",
                    "description": (
                        "Update the customer's phone number. Use this when the customer "
                        "wants to change their contact phone number. Phone numbers should "
                        "be in E.164 format when possible (e.g., +1-555-123-4567)."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "phone_number": {
                                "type": "string",
                                "description": "New phone number (preferably in E.164 format)",
                            },
                            "phone_type": {
                                "type": "string",
                                "description": "Type of phone number",
                                "enum": ["mobile", "home", "work"],
                                "default": "mobile",
                            },
                            "is_primary": {
                                "type": "boolean",
                                "description": "Whether this should be the primary contact number",
                                "default": True,
                            },
                        },
                        "required": ["phone_number"],
                    },
                },
                {
                    "name": "profile_update_customer_preferences",
                    "description": (
                        "Update customer communication and service preferences. "
                        "Use this when the customer wants to change their notification "
                        "preferences, communication channels, or service settings."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "email_notifications": {
                                "type": "boolean",
                                "description": "Enable or disable email notifications",
                            },
                            "sms_notifications": {
                                "type": "boolean",
                                "description": "Enable or disable SMS notifications",
                            },
                            "push_notifications": {
                                "type": "boolean",
                                "description": "Enable or disable push notifications",
                            },
                            "marketing_communications": {
                                "type": "boolean",
                                "description": "Opt in or out of marketing communications",
                            },
                            "preferred_language": {
                                "type": "string",
                                "description": "Preferred language for communications",
                                "enum": ["en", "es", "fr", "de", "zh"],
                            },
                            "paperless_statements": {
                                "type": "boolean",
                                "description": "Enable or disable paperless statements",
                            },
                            "two_factor_auth": {
                                "type": "boolean",
                                "description": "Enable or disable two-factor authentication",
                            },
                        },
                        "required": [],
                    },
                },
                {
                    "name": "profile_update_customer_email",
                    "description": (
                        "Update the customer's email address. Use this when the customer "
                        "wants to change their email address. Note that this may require "
                        "email verification before the change takes effect."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "email": {
                                "type": "string",
                                "description": "New email address",
                            },
                            "confirm_email": {
                                "type": "string",
                                "description": "Email confirmation (must match email)",
                            },
                        },
                        "required": ["email", "confirm_email"],
                    },
                },
            ]
        )

    logger.info(f"Generated {len(tools)} profile tools for user with permissions: {permissions}")

    return tools


# Tool execution handlers (used by the router)


async def execute_get_customer_profile(
    router: Any,
    user_context: Dict[str, Any],
    include_preferences: bool = True,
    include_contact: bool = True,
) -> str:
    """
    Execute get_customer_profile tool.

    Args:
        router: SubAgentRouter instance
        user_context: User authentication context
        include_preferences: Include preferences in response
        include_contact: Include contact info in response

    Returns:
        JSON response from profile agent
    """
    tool_input = {
        "include_preferences": include_preferences,
        "include_contact": include_contact,
        "user_context": user_context,
    }

    return await router.route_to_profile("profile_get_customer_profile", tool_input)


async def execute_update_customer_address(
    router: Any,
    user_context: Dict[str, Any],
    street_address: str,
    city: str,
    state: str,
    postal_code: str,
    street_address_2: str = "",
    country: str = "US",
) -> str:
    """
    Execute update_customer_address tool.

    Args:
        router: SubAgentRouter instance
        user_context: User authentication context
        street_address: Street address
        city: City name
        state: State code
        postal_code: ZIP/postal code
        street_address_2: Additional address line
        country: Country code

    Returns:
        JSON response from profile agent
    """
    tool_input = {
        "street_address": street_address,
        "street_address_2": street_address_2,
        "city": city,
        "state": state,
        "postal_code": postal_code,
        "country": country,
        "user_context": user_context,
    }

    return await router.route_to_profile("profile_update_customer_address", tool_input)


async def execute_update_customer_phone(
    router: Any,
    user_context: Dict[str, Any],
    phone_number: str,
    phone_type: str = "mobile",
    is_primary: bool = True,
) -> str:
    """
    Execute update_customer_phone tool.

    Args:
        router: SubAgentRouter instance
        user_context: User authentication context
        phone_number: New phone number
        phone_type: Type of phone (mobile/home/work)
        is_primary: Whether this is the primary contact number

    Returns:
        JSON response from profile agent
    """
    tool_input = {
        "phone_number": phone_number,
        "phone_type": phone_type,
        "is_primary": is_primary,
        "user_context": user_context,
    }

    return await router.route_to_profile("profile_update_customer_phone", tool_input)


async def execute_update_customer_preferences(router: Any, user_context: Dict[str, Any], **preferences) -> str:
    """
    Execute update_customer_preferences tool.

    Args:
        router: SubAgentRouter instance
        user_context: User authentication context
        **preferences: Preference key-value pairs

    Returns:
        JSON response from profile agent
    """
    tool_input = {**preferences, "user_context": user_context}

    return await router.route_to_profile("profile_update_customer_preferences", tool_input)


async def execute_update_customer_email(
    router: Any, user_context: Dict[str, Any], email: str, confirm_email: str
) -> str:
    """
    Execute update_customer_email tool.

    Args:
        router: SubAgentRouter instance
        user_context: User authentication context
        email: New email address
        confirm_email: Email confirmation

    Returns:
        JSON response from profile agent
    """
    if email != confirm_email:
        return '{"error": "EMAIL_MISMATCH", "message": "Email and confirmation do not match"}'

    tool_input = {"email": email, "user_context": user_context}

    return await router.route_to_profile("profile_update_customer_email", tool_input)
