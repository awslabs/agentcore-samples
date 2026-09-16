"""
Tool Validation Module for MCP Tool Authorization

This module handles the validation of MCP tool access based on JWT claims
and allowed tools stored in DynamoDB.

The validation process:
1. Extracts claim_name and claim_value from JWT token
2. Extracts tool_name from MCP request body
3. Looks up the mapping in DynamoDB table (lakehouse_tenant_role_map)
4. Checks if tool_name is in the allowed_tools list
5. Returns authorization result
"""

import logging
import os
from typing import Any, Optional

import boto3

logger = logging.getLogger()


def _resolve_idp_provider() -> str:
    """IdP selector for the Lambda (DR-8): env (set at deploy time) → SSM → cognito."""
    v = os.environ.get("IDP_PROVIDER")
    if v:
        return v.strip().lower()
    try:
        region = os.environ.get("AWS_REGION", "us-east-1")
        ssm = boto3.client("ssm", region_name=region)
        return ssm.get_parameter(Name="/app/lakehouse-agent/idp-provider")["Parameter"]["Value"].strip().lower()
    except Exception:
        return "cognito"


IDP_PROVIDER = _resolve_idp_provider()


def check_tool_authorization(claim_name: str, claim_value: str, tool_name: str) -> bool:
    """
    Check if user has permission for specific tool based on DynamoDB lookup.

    Queries the lakehouse_tenant_role_map table to check if the tool_name
    is in the allowed_tools list for the given claim.

    Args:
        claim_name: The JWT claim name (e.g., "cognito:groups")
        claim_value: The JWT claim value (e.g., '["administrators"]')
        tool_name: The MCP tool name to check authorization for

    Returns:
        True if the tool is allowed for this claim, False otherwise
    """
    try:
        # Get table name from environment or use default
        table_name = os.environ.get("TENANT_ROLE_MAPPING_TABLE")

        if not table_name:
            # Try to get from SSM Parameter Store
            try:
                ssm = boto3.client("ssm")
                response = ssm.get_parameter(Name="/app/lakehouse-agent/tenant-role-mapping-table")
                table_name = response["Parameter"]["Value"]
                logger.info(f"Loaded table name from SSM: {table_name}")
            except Exception as e:
                logger.error(f"Failed to get table name from SSM: {e}")
                # Fallback to default table name
                table_name = "lakehouse_tenant_role_map"
                logger.info(f"Using default table name: {table_name}")

        # Initialize DynamoDB resource
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)

        logger.info(f"🔍 Checking tool authorization: {claim_name}={claim_value}, tool={tool_name}")

        # Query DynamoDB for the claim mapping
        response = table.get_item(Key={"claim_name": claim_name, "claim_value": claim_value})

        # Check if item exists and has allowed_tools
        if "Item" not in response:
            logger.warning(f"⚠️  No mapping found for claim: {claim_name}={claim_value}")
            return False

        item = response["Item"]
        allowed_tools = item.get("allowed_tools", [])

        # Check if tool_name is in the allowed_tools list
        is_allowed = tool_name in allowed_tools

        if is_allowed:
            logger.info(f"✅ Tool '{tool_name}' is allowed for {claim_name}={claim_value}")
        else:
            logger.warning(f"❌ Tool '{tool_name}' is NOT allowed for {claim_name}={claim_value}")
            logger.info(f"   Allowed tools: {allowed_tools}")

        return is_allowed

    except Exception as e:
        logger.error(f"❌ Error checking tool authorization: {e!s}")
        import traceback

        logger.error(f"Stack trace: {traceback.format_exc()}")
        return False


def get_tool_name_from_request(body: dict[str, Any]) -> str | None:
    """
    Extract the tool name from MCP request body.

    MCP request structure:
    {
        "jsonrpc": "2.0",
        "id": "...",
        "method": "tools/call",
        "params": {
            "name": "tool_name",
            "arguments": {...}
        }
    }

    Note: The Gateway may prefix tool names with the target name.
    For example: "lakehouse-mcp-target___query_claims"
    We need to strip the prefix to get the actual tool name: "query_claims"

    Args:
        body: MCP request body

    Returns:
        Tool name (without prefix) or None if not found
    """
    try:
        # Check if this is a tools/call request
        method = body.get("method")
        if method != "tools/call":
            logger.info(f"Not a tools/call request, method: {method}")
            return None

        # Extract tool name from params
        params = body.get("params", {})
        tool_name_with_prefix = params.get("name")

        if not tool_name_with_prefix:
            logger.warning("⚠️  Tool name not found in request params")
            return None

        # Strip the gateway target prefix if present
        # Format: "target-name___tool_name" -> "tool_name"
        if "___" in tool_name_with_prefix:
            tool_name = tool_name_with_prefix.split("___", 1)[1]
            logger.info(f"📋 Extracted tool name: {tool_name} (from {tool_name_with_prefix})")
        else:
            tool_name = tool_name_with_prefix
            logger.info(f"📋 Extracted tool name: {tool_name}")

        return tool_name

    except Exception as e:
        logger.error(f"❌ Error extracting tool name from request: {e!s}")
        return None


def validate_tool_access(claims: dict[str, Any], body: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    """
    Validate if the user has access to the requested tool.

    This function:
    1. Extracts the tool name from the request body
    2. Gets the claim for authorization check
    3. Checks if the tool is allowed for the user's claim

    Args:
        claims: Decoded JWT claims
        body: MCP request body

    Returns:
        Tuple of (is_authorized, error_message, tool_name)
        - is_authorized: True if access is granted, False otherwise
        - error_message: Error message if access is denied, None if granted
        - tool_name: The tool name being requested, None if not a tool call
    """
    # Extract tool name from request
    tool_name = get_tool_name_from_request(body)

    # If not a tool call, allow it (could be tools/list or other MCP methods)
    if not tool_name:
        logger.info("Not a tool call request, allowing")
        return (True, None, None)

    # Get claim for authorization check (similar to token exchange)
    claim_for_auth = get_claim_for_authorization(claims)

    if not claim_for_auth:
        error_msg = "No suitable claim found for authorization"
        logger.error(f"❌ {error_msg}")
        return (False, error_msg, tool_name)

    claim_name, claim_value = claim_for_auth

    # Check tool authorization
    is_authorized = check_tool_authorization(claim_name, claim_value, tool_name)

    if not is_authorized:
        # Extract group name for error message
        group_name = claim_value.replace("[", "").replace("]", "").replace('"', "")
        error_msg = f"Tool '{tool_name}' is not allowed for group {group_name}"
        logger.error(f"❌ {error_msg}")
        return (False, error_msg, tool_name)

    logger.info(f"✅ Tool access validated: {tool_name} for {claim_name}={claim_value}")
    return (True, None, tool_name)


def get_claim_for_authorization(claims: dict[str, Any]) -> tuple[str, str] | None:
    """
    Extract the appropriate claim for authorization check from JWT claims.

    Priority order:
    1. cognito:groups - for group-based authorization
    2. email - for user-specific authorization
    3. username - for username-based authorization

    Args:
        claims: Decoded JWT claims

    Returns:
        Tuple of (claim_name, claim_value) or None if no suitable claim found
    """
    import json

    if IDP_PROVIDER == "cognito":
        # [COGNITO] upstream verbatim: cognito:groups (whole-array) → email → username
        groups = claims.get("cognito:groups")
        if groups:
            claim_value = json.dumps(groups)
            logger.info(f"Found cognito:groups claim for authorization: {claim_value}")
            return ("cognito:groups", claim_value)

        email = claims.get("email")
        if email:
            logger.info(f"Found email claim for authorization: {email}")
            return ("email", email)

        username = claims.get("username") or claims.get("cognito:username")
        if username:
            logger.info(f"Found username claim for authorization: {username}")
            return ("username", username)
    else:  # okta
        # [OKTA] fork verbatim: filter built-in `Everyone` + iterate; then email → sub.
        OKTA_BUILTIN_GROUPS = {"Everyone"}  # Okta-specific; sibling IdPs revisit
        groups = claims.get("groups")
        if groups:
            for group in groups:
                if group in OKTA_BUILTIN_GROUPS:
                    continue
                claim_value = json.dumps([group])
                logger.info(f"Found groups claim for authorization (per-group iter): {claim_value}")
                return ("groups", claim_value)
            logger.warning(f"⚠️  All groups filtered as built-ins: {groups}")

        email = claims.get("email")
        if email:
            logger.info(f"Found email claim for authorization: {email}")
            return ("email", email)

        sub = claims.get("sub")
        if sub:
            logger.info(f"Found sub claim for authorization: {sub}")
            return ("sub", sub)

    logger.warning("⚠️  No suitable claim found for authorization")
    return None
