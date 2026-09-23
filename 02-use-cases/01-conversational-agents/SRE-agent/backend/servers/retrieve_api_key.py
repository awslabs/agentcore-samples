#!/usr/bin/env python3
"""
AWS Agent Credential Provider API Key Retrieval Tool

This module retrieves API keys from Amazon Bedrock AgentCore credential providers
by fetching the Secrets Manager ARN and then retrieving the secret value.
"""

import argparse
import json
import logging
from typing import Any, Dict, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


# Configuration constants
DEFAULT_CREDENTIAL_PROVIDER_NAME = "sre-agent-api-key-credential-provider"
DEFAULT_REGION = "us-east-1"
DEFAULT_ENDPOINT_URL = "https://bedrock-agentcore-control.us-east-1.amazonaws.com"


def _create_acps_client(region: str, endpoint_url: str) -> Any:
    """
    Create and configure the Agent Credential Provider Service client.

    Args:
        region: AWS region name
        endpoint_url: Endpoint URL for the service

    Returns:
        Configured boto3 client for agentcredentialprovider
    """
    sdk_config = Config(
        region_name=region,
        signature_version="v4",
        retries={"max_attempts": 2, "mode": "standard"},
    )

    return boto3.client(
        service_name="bedrock-agentcore-control",
        config=sdk_config,
        endpoint_url=endpoint_url,
    )


def _get_credential_provider_details(
    client: Any, provider_name: str
) -> Optional[Dict[str, Any]]:
    """
    Get API key credential provider details including Secrets Manager ARN.

    Args:
        client: ACPS client instance
        provider_name: Name of the credential provider

    Returns:
        Provider details including secretsManagerArn or None on error
    """
    try:
        logger.info(f"Attempting to retrieve credential provider: {provider_name}")
        response = client.get_api_key_credential_provider(name=provider_name)
        logger.info(f"Successfully retrieved credential provider: {provider_name}")
        logger.debug(f"Full response: {response}")

        # Log all keys in the response for debugging
        if response:
            logger.info(f"Response keys: {list(response.keys())}")
            for key, value in response.items():
                if key != "apiKey":  # Don't log sensitive data
                    logger.info(f"  {key}: {value}")

        return response
    except ClientError as e:
        logger.error(f"Failed to get credential provider: {e}")
        logger.error(
            f"Error code: {e.response.get('Error', {}).get('Code', 'Unknown')}"
        )
        logger.error(
            f"Error message: {e.response.get('Error', {}).get('Message', 'Unknown')}"
        )
        return None


def _retrieve_secret_value(secrets_manager_arn: str, region: str) -> Optional[str]:
    """
    Retrieve the secret value from AWS Secrets Manager.

    Args:
        secrets_manager_arn: ARN of the secret in Secrets Manager
        region: AWS region name

    Returns:
        The secret value (API key) or None on error
    """
    try:
        # Create Secrets Manager client
        secrets_client = boto3.client(service_name="secretsmanager", region_name=region)

        # Retrieve the secret
        response = secrets_client.get_secret_value(SecretId=secrets_manager_arn)

        # Extract the secret value
        if "SecretString" in response:
            secret_string = response["SecretString"]
            logger.debug(f"Secret string type: {type(secret_string)}")

            # Try to parse as JSON first
            try:
                secret_data = json.loads(secret_string)
                logger.info(f"Secret is JSON with keys: {list(secret_data.keys())}")

                # Extract the API key from the known field
                api_key = secret_data.get("api_key_value")
                if api_key:
                    logger.info(
                        "Successfully retrieved API key from 'api_key_value' field"
                    )
                    return api_key
                else:
                    logger.error("No 'api_key_value' field found in secret")
                    logger.error(f"Available fields: {list(secret_data.keys())}")
                    return None

            except json.JSONDecodeError:
                # If not JSON, the secret might be the API key directly
                logger.info("Secret is not JSON, treating as raw API key")
                return secret_string.strip()
        else:
            logger.error("No SecretString found in response")
            return None

    except ClientError as e:
        logger.error(f"Failed to retrieve secret from Secrets Manager: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse secret JSON: {e}")
        return None


def retrieve_api_key(
    credential_provider_name: str,
    region: str = DEFAULT_REGION,
    endpoint_url: str = DEFAULT_ENDPOINT_URL,
) -> Optional[str]:
    """
    Main function to retrieve API key from credential provider.

    Args:
        credential_provider_name: Name of the credential provider
        region: AWS region name (defaults to us-east-1)
        endpoint_url: Endpoint URL for the service

    Returns:
        The API key or None if retrieval fails
    """
    logger.info("Starting API key retrieval")

    # Create ACPS client
    client = _create_acps_client(region, endpoint_url)

    # Get credential provider details
    provider_details = _get_credential_provider_details(
        client, credential_provider_name
    )

    if not provider_details:
        logger.error("Failed to get credential provider details")
        return None

    # Extract Secrets Manager ARN from nested structure
    # The ARN is in apiKeySecretArn.secretArn
    api_key_secret_arn = provider_details.get("apiKeySecretArn")
    if not api_key_secret_arn:
        logger.error("No apiKeySecretArn found in provider details")
        logger.error(f"Available fields in response: {list(provider_details.keys())}")
        return None

    secrets_manager_arn = api_key_secret_arn.get("secretArn")
    if not secrets_manager_arn:
        logger.error("No secretArn found in apiKeySecretArn")
        return None
    logger.info(f"Using Secrets Manager ARN: {secrets_manager_arn}")
    # Retrieve the API key from Secrets Manager
    api_key = _retrieve_secret_value(secrets_manager_arn, region)

    if api_key:
        logger.info("API key retrieval completed successfully")
        return api_key
    else:
        logger.error("Failed to retrieve API key")
        return None


def _parse_arguments() -> argparse.Namespace:
    """
    Parse command line arguments.

    Returns:
        Parsed command line arguments
    """
    parser = argparse.ArgumentParser(
        description="Retrieve API key from AWS Agent Credential Provider Service"
    )

    parser.add_argument(
        "--credential-provider-name",
        default=DEFAULT_CREDENTIAL_PROVIDER_NAME,
        help=f"Name of the credential provider (default: {DEFAULT_CREDENTIAL_PROVIDER_NAME})",
    )

    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )

    parser.add_argument(
        "--endpoint-url",
        default=DEFAULT_ENDPOINT_URL,
        help=f"Service endpoint URL (default: {DEFAULT_ENDPOINT_URL})",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = _parse_arguments()

    api_key = retrieve_api_key(
        credential_provider_name=args.credential_provider_name,
        region=args.region,
        endpoint_url=args.endpoint_url,
    )

    if api_key:
        print("✅ Successfully retrieved API key")
        # Do not print the full API key.
        print(
            "ℹ️ API key has been securely retrieved and is available for programmatic use."
        )
    else:
        print("❌ Failed to retrieve API key")


if __name__ == "__main__":
    main()
