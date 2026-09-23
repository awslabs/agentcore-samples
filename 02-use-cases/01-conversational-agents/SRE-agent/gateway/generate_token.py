#!/usr/bin/env python3
"""
Cognito Token Generator

This script generates OAuth2 access tokens from Amazon Cognito using client credentials
and saves them to a .access_token file for use with AgentCore Gateway.
"""

import argparse
import logging
import os
from pathlib import Path
from typing import Any, Dict

import dotenv
import requests

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)


def _get_cognito_token(
    cognito_domain_url: str,
    client_id: str,
    client_secret: str,
    audience: str = "MCPGateway",
) -> Dict[str, Any]:
    """
    Get OAuth2 token from Amazon Cognito or Auth0 using client credentials grant type.

    Args:
        cognito_domain_url: The full Cognito/Auth0 domain URL
        client_id: The App Client ID
        client_secret: The App Client Secret
        audience: The audience for the token (default: MCPGateway)

    Returns:
        Token response containing access_token, expires_in, token_type
    """
    # Construct the token endpoint URL
    if "auth0.com" in cognito_domain_url:
        url = f"{cognito_domain_url.rstrip('/')}/oauth/token"
        # Use JSON format for Auth0
        headers = {"Content-Type": "application/json"}
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "audience": audience,
            "grant_type": "client_credentials",
            "scope": "invoke:gateway",
        }

        # Send as JSON for Auth0
        def response_method():
            return requests.post(url, headers=headers, json=data)
    else:
        # Cognito format
        url = f"{cognito_domain_url.rstrip('/')}/oauth2/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }

        # Send as form data for Cognito
        def response_method():
            return requests.post(url, headers=headers, data=data)

    try:
        # Make the request
        response = response_method()
        response.raise_for_status()  # Raise exception for bad status codes

        provider_type = "Auth0" if "auth0.com" in cognito_domain_url else "Cognito"
        logging.info(f"Successfully obtained {provider_type} access token")
        return response.json()

    except requests.exceptions.RequestException as e:
        logging.error(f"Error getting token: {e}")
        if hasattr(response, "text") and response.text:
            logging.error(f"Response: {response.text}")
        raise


def _save_access_token(
    token_response: Dict[str, Any], output_file: str = ".access_token"
) -> None:
    """
    Save the access token to a file.

    Args:
        token_response: Token response from Cognito
        output_file: Output file path
    """
    access_token = token_response["access_token"]
    Path(output_file).write_text(access_token)
    logging.info(f"Access token saved to {output_file}")
    logging.info(
        f"Token expires in {token_response.get('expires_in', 'unknown')} seconds"
    )


def generate_and_save_token(audience: str = "MCPGateway") -> None:
    """
    Generate Cognito token using environment variables and save to file.

    Args:
        audience: The audience for the token (default: MCPGateway)
    """
    # Load environment variables from .env file
    dotenv.load_dotenv()

    # Get required environment variables
    cognito_domain_url = os.environ.get("COGNITO_DOMAIN")
    client_id = os.environ.get("COGNITO_CLIENT_ID")
    client_secret = os.environ.get("COGNITO_CLIENT_SECRET")

    # Validate that all required variables are present
    if not all([cognito_domain_url, client_id, client_secret]):
        missing_vars = []
        if not cognito_domain_url:
            missing_vars.append("COGNITO_DOMAIN")
        if not client_id:
            missing_vars.append("COGNITO_CLIENT_ID")
        if not client_secret:
            missing_vars.append("COGNITO_CLIENT_SECRET")

        logging.error(
            f"Missing required environment variables: {', '.join(missing_vars)}"
        )
        logging.error("Please set these variables in your .env file")
        raise ValueError(f"Missing environment variables: {', '.join(missing_vars)}")

    logging.info("Generating Cognito access token from environment variables...")

    # Generate token
    token_response = _get_cognito_token(
        cognito_domain_url=cognito_domain_url,
        client_id=client_id,
        client_secret=client_secret,
        audience=audience,
    )

    # Save token to file
    _save_access_token(token_response)

    logging.info("Token generation completed successfully!")


def main():
    """Main function to generate and save Cognito token."""
    parser = argparse.ArgumentParser(
        description="Generate OAuth2 access tokens from Cognito/Auth0"
    )
    parser.add_argument(
        "--audience", default="MCPGateway", help="Token audience (default: MCPGateway)"
    )

    args = parser.parse_args()

    try:
        generate_and_save_token(audience=args.audience)
    except Exception as e:
        logging.error(f"Token generation failed: {e}")
        exit(1)


if __name__ == "__main__":
    main()
