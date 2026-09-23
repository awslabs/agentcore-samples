"""
Device Management System - Utility Functions

This module provides utility functions and classes for the Device Management System
agent runtime, including AWS region configuration, Amazon Cognito token management,
and OAuth authentication helpers.

Key Components:
    - AWS region configuration utilities
    - CognitoTokenManager: Automatic OAuth token management with refresh
    - Authentication helpers for MCP server communication
    - Environment variable validation and configuration

Classes:
    CognitoTokenManager: Manages OAuth tokens with automatic refresh capability

Functions:
    get_aws_region(): Retrieve AWS region from environment variables
    get_oauth_token(): Get valid OAuth token for MCP server authentication

Environment Variables:
    AWS_REGION: AWS region (defaults to us-west-2)
    COGNITO_DOMAIN: Amazon Cognito domain URL
    COGNITO_CLIENT_ID: OAuth client ID
    COGNITO_CLIENT_SECRET: OAuth client secret

Example:
    >>> token_manager = CognitoTokenManager()
    >>> token = token_manager.get_valid_token()
    >>> print(f"Token: {token}")
"""

import os
import requests
import logging
from datetime import datetime, timedelta
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


def get_aws_region() -> str:
    """
    Retrieve AWS region from environment variables.

    Returns:
        str: AWS region name, defaults to 'us-west-2' if not specified

    Environment Variables:
        AWS_REGION: The AWS region to use for service calls
    """
    return os.getenv("AWS_REGION", "us-west-2")


class CognitoTokenManager:
    """
    Manages Amazon Cognito OAuth tokens with automatic refresh capability.

    This class handles OAuth 2.0 client credentials flow with Amazon Cognito,
    automatically refreshing tokens when they expire and caching valid tokens
    to minimize authentication requests.

    Attributes:
        token (str): Current OAuth access token
        token_expires_at (datetime): Token expiration timestamp
        cognito_domain (str): Amazon Cognito domain URL
        client_id (str): OAuth client ID
        client_secret (str): OAuth client secret

    Raises:
        ValueError: If required environment variables are missing

    Example:
        >>> manager = CognitoTokenManager()
        >>> token = manager.get_valid_token()
        >>> # Token is automatically refreshed if expired
    """

    def __init__(self):
        self.token = None
        self.token_expires_at = None
        self.cognito_domain = os.getenv("COGNITO_DOMAIN")
        self.client_id = os.getenv("COGNITO_CLIENT_ID")
        self.client_secret = os.getenv("COGNITO_CLIENT_SECRET")

        if not all([self.cognito_domain, self.client_id, self.client_secret]):
            raise ValueError(
                "Missing required Cognito environment variables: COGNITO_DOMAIN, COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET"
            )

    def _fetch_new_token(self) -> Optional[str]:
        """Fetch a new OAuth token from Cognito"""
        try:
            url = f"https://{self.cognito_domain}/oauth2/token"
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            data = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }

            logger.info(f"Requesting new token from {url}")
            response = requests.post(url, headers=headers, data=data, timeout=10)

            if response.status_code == 200:
                token_data = response.json()
                access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 3600)  # Default to 1 hour

                # Set expiration time with a 5-minute buffer
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in - 300)

                logger.info("Successfully obtained new OAuth token")
                return access_token
            else:
                logger.error(f"Failed to get token: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Error fetching OAuth token: {str(e)}")
            return None

    def _is_token_expired(self) -> bool:
        """Check if the current token is expired or about to expire"""
        if not self.token or not self.token_expires_at:
            return True
        return datetime.now() >= self.token_expires_at

    def get_token(self) -> Optional[str]:
        """Get a valid OAuth token, refreshing if necessary"""
        if self._is_token_expired():
            logger.info("Token expired or missing, fetching new token")
            self.token = self._fetch_new_token()

        return self.token


# Global token manager instance
_token_manager = None


def get_oauth_token() -> Optional[str]:
    """Get a valid OAuth token for Cognito authentication"""
    global _token_manager

    try:
        if _token_manager is None:
            _token_manager = CognitoTokenManager()

        return _token_manager.get_token()
    except Exception as e:
        logger.error(f"Error getting OAuth token: {str(e)}")
        return None


def get_auth_headers() -> dict:
    """Get authorization headers with Bearer token"""
    token = get_oauth_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    else:
        logger.warning("No valid token available for authorization")
        return {}


def create_agentcore_client():
    """Create AgentCore client and boto session"""
    import boto3

    # Create boto session
    boto_session = boto3.Session(region_name=os.getenv("AWS_REGION", "us-west-2"))

    # Create bedrock-agentcore client directly using boto3
    agentcore_client = boto_session.client(
        "bedrock-agentcore",
        region_name=os.getenv("AWS_REGION", "us-west-2"),
        # endpoint_url=os.getenv("ENDPOINT_URL")
    )

    return boto_session, agentcore_client


def get_gateway_endpoint(agentcore_client, gateway_id: str) -> str:
    """Get gateway endpoint URL from gateway ID"""
    try:
        # Use the correct boto3 method for bedrock-agentcore
        response = agentcore_client.describe_gateway(gatewayId=gateway_id)
        endpoint = response.get("gateway", {}).get("gatewayEndpoint", "")
        return endpoint
    except Exception as e:
        logger.error(f"Error getting gateway endpoint: {str(e)}")
        # If we can't get the endpoint, return the one from environment
        return os.getenv("gateway_endpoint", "")
