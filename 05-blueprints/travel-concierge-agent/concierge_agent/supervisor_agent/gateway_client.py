"""
Gateway client utilities for AgentCore Gateway integration.
Handles OAuth2 authentication and MCP client creation.
"""

import os
import boto3
import requests
import logging
from datetime import datetime, timedelta
from typing import Optional
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

# Token cache
_token_cache: Optional[str] = None
_token_expiry: Optional[datetime] = None


def get_ssm_parameter(parameter_name: str, region: str) -> str:
    """
    Fetch parameter from SSM Parameter Store.

    Args:
        parameter_name: SSM parameter name
        region: AWS region

    Returns:
        Parameter value
    """
    ssm = boto3.client("ssm", region_name=region)
    try:
        response = ssm.get_parameter(Name=parameter_name)
        return response["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        raise ValueError(f"SSM parameter not found: {parameter_name}")
    except Exception as e:
        raise ValueError(f"Failed to retrieve SSM parameter {parameter_name}: {e}")


def get_gateway_access_token() -> str:
    """
    Get OAuth2 access token for gateway authentication.
    Tokens are cached and refreshed automatically.

    Returns:
        Bearer token string
    """
    global _token_cache, _token_expiry

    # Return cached token if still valid
    if _token_cache and _token_expiry and datetime.now() < _token_expiry:
        logger.debug("Using cached gateway token")
        return _token_cache

    logger.info("Acquiring new gateway OAuth2 token...")

    # Get configuration from environment
    client_id = os.environ.get("GATEWAY_CLIENT_ID")
    user_pool_id = os.environ.get("GATEWAY_USER_POOL_ID")
    scope = os.environ.get("GATEWAY_SCOPE", "concierge-gateway/invoke")
    region = os.environ.get("AWS_REGION", "us-east-1")

    if not client_id or not user_pool_id:
        raise ValueError("GATEWAY_CLIENT_ID and GATEWAY_USER_POOL_ID must be set")

    try:
        # Get client secret from Cognito
        cognito = boto3.client("cognito-idp", region_name=region)
        response = cognito.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
        client_secret = response["UserPoolClient"]["ClientSecret"]

        # Get Cognito domain
        pool_response = cognito.describe_user_pool(UserPoolId=user_pool_id)
        domain = pool_response["UserPool"].get("Domain")
        if not domain:
            raise ValueError("No Cognito domain configured")

        # Request token
        token_url = f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"

        token_response = requests.post(
            token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": scope,
            },
            timeout=10,
        )
        token_response.raise_for_status()

        data = token_response.json()
        _token_cache = data["access_token"]

        # Cache for (expires_in - 600) seconds (10 min buffer)
        expires_in = data.get("expires_in", 3600)
        _token_expiry = datetime.now() + timedelta(seconds=expires_in - 600)

        logger.info(f"✅ Gateway token acquired, expires in {expires_in}s")
        return _token_cache

    except Exception as e:
        logger.error(f"❌ Failed to get gateway token: {e}")
        raise


def get_gateway_client(tool_filter_pattern: str, prefix: str = "gateway") -> MCPClient:
    """
    Get Gateway MCP client with specified tool filtering.

    Args:
        tool_filter_pattern: Regex pattern to filter tools (e.g., "^carttools___")
        prefix: Prefix for tool names (default: "gateway")

    Returns:
        MCPClient filtered to specified tools

    Example:
        cart_client = get_gateway_client("^carttools___")
        travel_client = get_gateway_client("^traveltools___")
        itinerary_client = get_gateway_client("^itinerarytools___")
    """
    import re

    region = os.environ.get("AWS_REGION", "us-east-1")
    deployment_id = os.getenv("DEPLOYMENT_ID", "default")

    gateway_url = get_ssm_parameter(f"/concierge-agent/{deployment_id}/gateway-url", region)
    access_token = get_gateway_access_token()

    logger.info(f"Creating Gateway MCP client with filter: {tool_filter_pattern}, prefix: {prefix}")

    tool_filters = {"allowed": [re.compile(tool_filter_pattern)]}

    client = MCPClient(
        lambda: streamablehttp_client(url=gateway_url, headers={"Authorization": f"Bearer {access_token}"}),
        prefix=prefix,
        tool_filters=tool_filters,
    )

    logger.info(f"✅ Gateway MCP client created with filter: {tool_filter_pattern}")
    return client
