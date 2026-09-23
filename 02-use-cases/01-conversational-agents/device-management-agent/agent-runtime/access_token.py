"""
Amazon Cognito Access Token Management Module

This module provides authentication functionality for the Device Management System,
handling OAuth token retrieval from Amazon Cognito with fallback mechanisms for
different deployment environments (local development vs containerized runtime).

The module supports two authentication methods:
1. Amazon Bedrock AgentCore workload identity (preferred)
2. Direct Amazon Cognito OAuth client credentials flow (fallback)

Environment Variables Required:
    COGNITO_DOMAIN: Amazon Cognito domain URL
    COGNITO_CLIENT_ID: OAuth client ID
    COGNITO_CLIENT_SECRET: OAuth client secret

Example:
    >>> token = get_gateway_access_token()
    >>> print(f"Access token: {token}")
"""

import os
import requests
from dotenv import load_dotenv
from bedrock_agentcore.identity.auth import requires_access_token

load_dotenv()


def get_cognito_token_direct():
    """
    Retrieve OAuth access token directly from Amazon Cognito.

    This function implements the OAuth 2.0 client credentials flow to obtain
    an access token from Amazon Cognito. Used as a fallback when Amazon Bedrock
    AgentCore workload identity is not available (e.g., in containerized environments).

    Returns:
        str: OAuth access token if successful, None if failed

    Raises:
        ValueError: If required environment variables are missing
        requests.RequestException: If HTTP request to Cognito fails

    Environment Variables:
        COGNITO_DOMAIN: Amazon Cognito domain URL (e.g., https://domain.auth.region.amazoncognito.com)
        COGNITO_CLIENT_ID: OAuth client ID from Cognito App Client
        COGNITO_CLIENT_SECRET: OAuth client secret from Cognito App Client
    """
    try:
        # Get Cognito configuration from environment
        cognito_domain = os.getenv("COGNITO_DOMAIN")
        client_id = os.getenv("COGNITO_CLIENT_ID")
        client_secret = os.getenv("COGNITO_CLIENT_SECRET")

        print(f"Debug - Cognito Domain: {cognito_domain}")
        print(f"Debug - Client ID: {client_id}")
        print(f"Debug - Client Secret: {'***' if client_secret else 'None'}")

        if not all([cognito_domain, client_id, client_secret]):
            missing = []
            if not cognito_domain:
                missing.append("COGNITO_DOMAIN")
            if not client_id:
                missing.append("COGNITO_CLIENT_ID")
            if not client_secret:
                missing.append("COGNITO_CLIENT_SECRET")
            raise ValueError(f"Missing Cognito configuration: {', '.join(missing)}")

        # Prepare token request
        token_url = f"{cognito_domain}/oauth2/token"
        print(f"Debug - Token URL: {token_url}")

        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "device-management-oauth/invoke",
        }

        print("Debug - Making token request...")
        # Make token request
        response = requests.post(token_url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        print(f"Debug - Response status: {response.status_code}")
        print(f"Debug - Response headers: {dict(response.headers)}")

        token_data = response.json()
        print(f"Debug - Token data keys: {list(token_data.keys())}")
        access_token = token_data.get("access_token")
        print(f"Debug - Access token received: {'Yes' if access_token else 'No'}")
        return access_token

    except Exception as e:
        print(f"Error getting Cognito token directly: {e}")
        import traceback

        traceback.print_exc()
        return None


@requires_access_token(
    provider_name="vgs-identity-provider",
    scopes=[],
    auth_flow="M2M",
)
def get_gateway_access_token_bedrock(access_token: str):
    """
    Retrieve access token using Amazon Bedrock AgentCore workload identity.

    This function uses the Amazon Bedrock AgentCore identity provider to obtain
    an access token when running in environments with workload identity configured.
    This is the preferred method for production deployments.

    Args:
        access_token (str): Access token provided by the AgentCore identity system

    Returns:
        str: The provided access token (passed through from AgentCore)

    Note:
        This function is decorated with @requires_access_token which handles
        the actual token retrieval from the AgentCore identity provider.
    """
    # Note: Not logging actual token for security reasons
    print("Access Token received from Bedrock AgentCore")
    return access_token


def get_gateway_access_token():
    """
    Retrieve access token with automatic fallback between authentication methods.

    This is the main entry point for token retrieval. It attempts to use Amazon Bedrock
    AgentCore workload identity first (preferred for production), then falls back to
    direct Amazon Cognito OAuth if workload identity is not available.

    Authentication Flow:
        1. Try Amazon Bedrock AgentCore workload identity
        2. If workload identity fails, fall back to direct Cognito OAuth
        3. Return token if either method succeeds
        4. Raise exception if both methods fail

    Returns:
        str: Valid OAuth access token for gateway authentication

    Raises:
        Exception: If both authentication methods fail
        ValueError: If required environment variables are missing

    Example:
        >>> try:
        ...     token = get_gateway_access_token()
        ...     print("Authentication successful")
        ... except Exception as e:
        ...     print(f"Authentication failed: {e}")
    """
    try:
        # Try bedrock_agentcore method first
        print("Trying bedrock_agentcore authentication...")
        return get_gateway_access_token_bedrock()
    except ValueError as e:
        if "Workload access token has not been set" in str(e):
            print("Workload access token not available, falling back to direct Cognito authentication...")
            # Fall back to direct Cognito token retrieval
            token = get_cognito_token_direct()
            if token:
                print("Successfully obtained token via direct Cognito authentication")
                return token
            else:
                raise Exception("Failed to obtain token via both bedrock_agentcore and direct Cognito methods")
        else:
            raise e
    except Exception as e:
        print(f"Error with bedrock_agentcore authentication: {e}")
        print("Falling back to direct Cognito authentication...")
        # Fall back to direct Cognito token retrieval
        token = get_cognito_token_direct()
        if token:
            print("Successfully obtained token via direct Cognito authentication")
            return token
        else:
            raise Exception("Failed to obtain token via both bedrock_agentcore and direct Cognito methods")


if __name__ == "__main__":
    token = get_gateway_access_token()
    # Note: Not printing actual token for security reasons
    print(f"Token retrieved: {'Yes' if token else 'No'}")
