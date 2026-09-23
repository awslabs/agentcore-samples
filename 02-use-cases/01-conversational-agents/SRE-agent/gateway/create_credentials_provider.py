#!/usr/bin/env python3
"""
AWS Agent Credential Provider Service Management Tool

This module manages API key credential providers for Amazon Bedrock AgentCore.
It handles listing, deleting, and creating credential providers with proper
error handling and retry logic for SecretsManager conflicts.
"""

import argparse
import logging
import time
from pathlib import Path
from pprint import pprint
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
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 5
PROPAGATION_DELAY = 15


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


def _check_provider_exists(client: Any, provider_name: str) -> bool:
    """
    Check if a credential provider with the given name already exists.

    Args:
        client: ACPS client instance
        provider_name: Name of the credential provider to check

    Returns:
        True if provider exists, False otherwise
    """
    try:
        existing_providers = client.list_api_key_credential_providers()
        logger.info("Listed existing API key credential providers")

        if "credentialProviders" in existing_providers:
            for provider in existing_providers["credentialProviders"]:
                if provider.get("name") == provider_name:
                    logger.info(f"Found existing credential provider: {provider_name}")
                    return True

        logger.info(f"No existing credential provider found with name: {provider_name}")
        return False

    except ClientError as e:
        logger.error(f"Failed to list credential providers: {e}")
        raise


def _delete_existing_provider(client: Any, provider_name: str) -> None:
    """
    Delete an existing credential provider and wait for propagation.

    Args:
        client: ACPS client instance
        provider_name: Name of the credential provider to delete
    """
    try:
        logger.info(f"Deleting existing credential provider: {provider_name}")
        client.delete_api_key_credential_provider(name=provider_name)
        logger.info("Successfully deleted existing credential provider")

        # Wait for deletion to propagate
        logger.info(f"Waiting {PROPAGATION_DELAY} seconds for deletion to propagate...")
        time.sleep(PROPAGATION_DELAY)

    except ClientError as e:
        logger.error(f"Failed to delete credential provider: {e}")
        raise


def _create_provider_with_retry(
    client: Any, provider_name: str, api_key: str
) -> Dict[str, Any]:
    """
    Create a new credential provider with retry logic for SecretsManager conflicts.

    Args:
        client: ACPS client instance
        provider_name: Name for the new credential provider
        api_key: API key for the credential provider

    Returns:
        Response from the create API call
    """
    retry_delay = INITIAL_RETRY_DELAY

    for attempt in range(MAX_RETRIES):
        try:
            response = client.create_api_key_credential_provider(
                name=provider_name, apiKey=api_key
            )
            logger.info("Successfully created credential provider")
            return response

        except ClientError as e:
            if e.response["Error"][
                "Code"
            ] == "ConflictException" and "SecretsManager" in str(e):
                if attempt < MAX_RETRIES - 1:
                    logger.warning(
                        f"SecretsManager conflict (attempt {attempt + 1}/{MAX_RETRIES}). "
                        f"Retrying in {retry_delay} seconds..."
                    )
                    time.sleep(retry_delay)
                    retry_delay += 2  # Exponential backoff
                else:
                    logger.error(
                        f"Failed to create credential provider after {MAX_RETRIES} attempts: {e}"
                    )
                    raise
            else:
                logger.error(f"Failed to create credential provider: {e}")
                raise

    # This should never be reached
    raise RuntimeError("Unexpected end of retry loop")


def _list_workload_identities(client: Any) -> Optional[Dict[str, Any]]:
    """
    List all workload identities.

    Args:
        client: ACPS client instance

    Returns:
        Workload identities response or None on error
    """
    try:
        workload_identities = client.list_workload_identities()
        logger.info("Listed all workload identities")
        return workload_identities
    except ClientError as e:
        logger.error(f"Failed to list workload identities: {e}")
        return None


def _list_oauth2_providers(client: Any) -> Optional[Dict[str, Any]]:
    """
    List all OAuth2 credential providers.

    Args:
        client: ACPS client instance

    Returns:
        OAuth2 providers response or None on error
    """
    try:
        oauth2_providers = client.list_oauth2_credential_providers()
        logger.info("Listed all OAuth2 credential providers")
        return oauth2_providers
    except ClientError as e:
        logger.error(f"Failed to list OAuth2 credential providers: {e}")
        return None


def _save_credential_provider_arn(
    credential_provider_arn: str, file_path: str = ".credentials_provider"
) -> None:
    """
    Save the credential provider ARN to a local file.

    Args:
        credential_provider_arn: The ARN to save
        file_path: Path to the file where ARN will be saved
    """
    try:
        Path(file_path).write_text(credential_provider_arn)
        logger.info(f"Saved credential provider ARN to {file_path}")
    except Exception as e:
        logger.error(f"Failed to save credential provider ARN: {e}")
        raise


def setup_credential_provider(
    credential_provider_name: str, api_key: str, region: str, endpoint_url: str
) -> None:
    """
    Main function to set up the API key credential provider.

    Args:
        credential_provider_name: Name for the credential provider
        api_key: API key for the credential provider
        region: AWS region name
        endpoint_url: Endpoint URL for the service

    This function orchestrates the entire process of checking for existing
    providers, deleting them if found, and creating a new one.
    """
    logger.info("Starting credential provider setup")

    # Create ACPS client
    client = _create_acps_client(region, endpoint_url)

    # Check if provider already exists
    provider_exists = _check_provider_exists(client, credential_provider_name)

    # Delete existing provider if found
    if provider_exists:
        _delete_existing_provider(client, credential_provider_name)

    # Create new credential provider
    logger.info(f"Creating new API key credential provider: {credential_provider_name}")
    response = _create_provider_with_retry(client, credential_provider_name, api_key)

    print("✅ Successfully created credential provider")
    pprint(response)

    # Extract and save credential provider ARN
    credential_provider_arn = response.get("credentialProviderArn")
    if credential_provider_arn:
        _save_credential_provider_arn(credential_provider_arn)
        print(f"\n📄 Credential Provider ARN: {credential_provider_arn}")
        print("📁 ARN saved to .credentials_provider file")
    else:
        logger.warning("No credentialProviderArn found in response")

    # List additional information
    print("\n📋 Listing all workload identities:")
    workload_identities = _list_workload_identities(client)
    if workload_identities:
        pprint(workload_identities)

    print("\n📋 Listing all OAuth2 credential providers:")
    oauth2_providers = _list_oauth2_providers(client)
    if oauth2_providers:
        pprint(oauth2_providers)

    logger.info("Credential provider setup completed successfully")


def _parse_arguments() -> argparse.Namespace:
    """
    Parse command line arguments.

    Returns:
        Parsed command line arguments
    """
    parser = argparse.ArgumentParser(
        description="Create and manage AWS Agent Credential Provider Service API key providers"
    )

    parser.add_argument(
        "--credential-provider-name",
        default=DEFAULT_CREDENTIAL_PROVIDER_NAME,
        help=f"Name for the credential provider (default: {DEFAULT_CREDENTIAL_PROVIDER_NAME})",
    )

    parser.add_argument(
        "--api-key",
        required=True,
        help="API key for the credential provider (required)",
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

    setup_credential_provider(
        credential_provider_name=args.credential_provider_name,
        api_key=args.api_key,
        region=args.region,
        endpoint_url=args.endpoint_url,
    )


if __name__ == "__main__":
    main()
