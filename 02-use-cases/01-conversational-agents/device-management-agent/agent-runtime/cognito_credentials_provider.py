#!/usr/bin/python
"""
Amazon Cognito OAuth2 Credential Provider Management CLI

This module provides a command-line interface for managing Amazon Cognito OAuth2
credential providers in Amazon Bedrock AgentCore. It enables creation, deletion,
and listing of OAuth2 credential providers used for agent authentication.

The CLI tool automates the process of configuring OAuth2 authentication by:
- Creating credential providers with Cognito configuration
- Storing provider information in .env files for easy access
- Managing provider lifecycle (create, delete, list)
- Validating required environment variables

Key Features:
    - Create OAuth2 credential providers with custom names
    - Automatic .env file management for provider persistence
    - List all existing credential providers
    - Delete credential providers with confirmation prompts
    - Environment variable validation and error handling

Commands:
    create: Create a new Cognito OAuth2 credential provider
    delete: Delete an existing credential provider
    list: List all OAuth2 credential providers

Environment Variables Required:
    COGNITO_CLIENT_ID: Amazon Cognito app client ID
    COGNITO_CLIENT_SECRET: Amazon Cognito app client secret
    COGNITO_DISCOVERY_URL: OIDC discovery URL/issuer
    COGNITO_AUTH_URL: Authorization endpoint URL
    COGNITO_TOKEN_URL: Token endpoint URL
    AWS_REGION: AWS region for AgentCore operations

Environment Variables Managed:
    COGNITO_PROVIDER_NAME: Name of the created credential provider

Example Usage:
    Create a new provider:
    >>> python cognito_credentials_provider.py create --name my-provider

    List all providers:
    >>> python cognito_credentials_provider.py list

    Delete a provider:
    >>> python cognito_credentials_provider.py delete --name my-provider

    Delete with auto-confirmation:
    >>> python cognito_credentials_provider.py delete --name my-provider --confirm

Notes:
    - Provider names are stored in .env file for easy reference
    - Deletion requires confirmation unless --confirm flag is used
    - All operations require valid AWS credentials
"""

import boto3
import click
import sys
import os
from dotenv import load_dotenv
from utils import get_aws_region

# Load environment variables from .env file
load_dotenv()

REGION = get_aws_region()

identity_client = boto3.client(
    "bedrock-agentcore-control",
    region_name=REGION,
)


def get_env_variable(var_name: str, description: str = None) -> str:
    """Get environment variable with validation."""
    value = os.getenv(var_name)
    if not value:
        desc = description or var_name
        click.echo(f"❌ Missing required environment variable: {var_name}", err=True)
        click.echo(f"   Please set {desc} in your .env file", err=True)
        sys.exit(1)
    return value


def store_provider_name_in_env(provider_name: str):
    """Store credential provider name in .env file."""
    env_file_path = ".env"
    try:
        # Read existing .env file content
        env_lines = []
        if os.path.exists(env_file_path):
            with open(env_file_path, "r", encoding="utf-8") as f:
                env_lines = f.readlines()

        # Remove existing COGNITO_PROVIDER_NAME if it exists
        env_lines = [line for line in env_lines if not line.startswith("COGNITO_PROVIDER_NAME=")]

        # Add the new provider name
        env_lines.append(f"COGNITO_PROVIDER_NAME={provider_name}\n")

        # Write back to .env file
        with open(env_file_path, "w", encoding="utf-8") as f:
            f.writelines(env_lines)

        click.echo(f"📦 Stored provider name in .env file: {provider_name}")
    except Exception as e:
        click.echo(f"⚠️ Failed to store provider name in .env file: {e}")


def get_provider_name_from_env() -> str:
    """Get credential provider name from .env file."""
    return os.getenv("COGNITO_PROVIDER_NAME")


def delete_provider_name_from_env():
    """Remove provider name from .env file."""
    env_file_path = ".env"
    try:
        if not os.path.exists(env_file_path):
            return

        # Read existing .env file content
        with open(env_file_path, "r", encoding="utf-8") as f:
            env_lines = f.readlines()

        # Remove COGNITO_PROVIDER_NAME line
        env_lines = [line for line in env_lines if not line.startswith("COGNITO_PROVIDER_NAME=")]

        # Write back to .env file
        with open(env_file_path, "w", encoding="utf-8") as f:
            f.writelines(env_lines)

        click.echo("🧹 Removed provider name from .env file")
    except Exception as e:
        click.echo(f"⚠️ Failed to remove provider name from .env file: {e}")


def create_cognito_provider(provider_name: str) -> dict:
    """Create a Cognito OAuth2 credential provider."""
    try:
        click.echo("📥 Loading Cognito configuration from environment variables...")

        client_id = get_env_variable("COGNITO_CLIENT_ID", "Cognito app client ID")
        click.echo(f"✅ Retrieved client ID: {client_id}")

        client_secret = get_env_variable("COGNITO_CLIENT_SECRET", "Cognito app client secret")
        click.echo(f"✅ Retrieved client secret: {client_secret[:4]}***")

        issuer = get_env_variable("COGNITO_DISCOVERY_URL", "OIDC discovery URL/issuer")
        auth_url = get_env_variable("COGNITO_AUTH_URL", "Authorization endpoint URL")
        token_url = get_env_variable("COGNITO_TOKEN_URL", "Token endpoint URL")

        click.echo(f"✅ Issuer: {issuer}")
        click.echo(f"✅ Authorization Endpoint: {auth_url}")
        click.echo(f"✅ Token Endpoint: {token_url}")

        click.echo("⚙️  Creating OAuth2 credential provider...")
        cognito_provider = identity_client.create_oauth2_credential_provider(
            name=provider_name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "oauthDiscovery": {
                        "authorizationServerMetadata": {
                            "issuer": issuer,
                            "authorizationEndpoint": auth_url,
                            "tokenEndpoint": token_url,
                            "responseTypes": ["code", "token"],
                        }
                    },
                }
            },
        )

        click.echo("✅ OAuth2 credential provider created successfully")
        provider_arn = cognito_provider["credentialProviderArn"]
        click.echo(f"   Provider ARN: {provider_arn}")
        click.echo(f"   Provider Name: {cognito_provider['name']}")

        # Store provider name in .env file
        store_provider_name_in_env(provider_name)

        return cognito_provider

    except Exception as e:
        click.echo(f"❌ Error creating Cognito credential provider: {str(e)}", err=True)
        sys.exit(1)


def delete_cognito_provider(provider_name: str) -> bool:
    """Delete a Cognito OAuth2 credential provider."""
    try:
        click.echo(f"🗑️  Deleting OAuth2 credential provider: {provider_name}")

        identity_client.delete_oauth2_credential_provider(name=provider_name)

        click.echo("✅ OAuth2 credential provider deleted successfully")
        return True

    except Exception as e:
        click.echo(f"❌ Error deleting credential provider: {str(e)}", err=True)
        return False


def list_credential_providers() -> list:
    """List all OAuth2 credential providers."""
    try:
        response = identity_client.list_oauth2_credential_providers(maxResults=20)
        providers = response.get("credentialProviders", [])
        return providers

    except Exception as e:
        click.echo(f"❌ Error listing credential providers: {str(e)}", err=True)
        return []


def find_provider_by_name(provider_name: str) -> bool:
    """Check if provider exists by name."""
    providers = list_credential_providers()
    for provider in providers:
        if provider.get("name") == provider_name:
            return True
    return False


@click.group()
@click.pass_context
def cli(ctx):
    """AgentCore Cognito Credential Provider Management CLI.

    Create and delete OAuth2 credential providers for Cognito authentication.
    """
    ctx.ensure_object(dict)


@cli.command()
@click.option("--name", required=True, help="Name for the credential provider (required)")
def create(name):
    """Create a new Cognito OAuth2 credential provider."""
    click.echo(f"🚀 Creating Cognito credential provider: {name}")
    click.echo(f"📍 Region: {REGION}")

    # Check if provider already exists in .env
    existing_name = get_provider_name_from_env()
    if existing_name:
        click.echo(f"⚠️  A provider already exists in .env file: {existing_name}")
        if not click.confirm("Do you want to replace it?"):
            click.echo("❌ Operation cancelled")
            sys.exit(0)

    try:
        provider = create_cognito_provider(provider_name=name)
        click.echo("🎉 Cognito credential provider created successfully!")
        click.echo(f"   Provider ARN: {provider['credentialProviderArn']}")
        click.echo(f"   Provider Name: {provider['name']}")

    except Exception as e:
        click.echo(f"❌ Failed to create credential provider: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--name",
    help="Name of the credential provider to delete (if not provided, will read from SSM parameter)",
)
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
def delete(name, confirm):
    """Delete a Cognito OAuth2 credential provider."""

    # If no name provided, try to get from .env file
    if not name:
        name = get_provider_name_from_env()
        if not name:
            click.echo(
                "❌ No provider name provided and couldn't read from .env file",
                err=True,
            )
            click.echo("   Hint: Use 'list' command to see available providers")
            sys.exit(1)
        click.echo(f"📖 Using provider name from .env file: {name}")

    click.echo(f"🔍 Looking for credential provider: {name}")

    # Check if provider exists
    if not find_provider_by_name(name):
        click.echo(f"❌ No credential provider found with name: {name}", err=True)
        click.echo("   Hint: Use 'list' command to see available providers")
        sys.exit(1)

    click.echo(f"📖 Found provider: {name}")

    # Confirmation prompt
    if not confirm:
        if not click.confirm(
            f"⚠️  Are you sure you want to delete credential provider '{name}'? This action cannot be undone."
        ):
            click.echo("❌ Operation cancelled")
            sys.exit(0)

    if delete_cognito_provider(name):
        click.echo(f"✅ Credential provider '{name}' deleted successfully")

        # Remove provider name from .env file
        delete_provider_name_from_env()
        click.echo("🎉 Credential provider deleted and removed from .env file successfully")
    else:
        click.echo("❌ Failed to delete credential provider", err=True)
        sys.exit(1)


@cli.command("list")
def list_providers():
    """List all OAuth2 credential providers."""
    providers = list_credential_providers()

    if not providers:
        click.echo("ℹ️  No credential providers found")
        return

    click.echo(f"📋 Found {len(providers)} credential provider(s):")
    for provider in providers:
        click.echo(f"  • Name: {provider.get('name', 'N/A')}")
        click.echo(f"    ARN: {provider['credentialProviderArn']}")
        click.echo(f"    Vendor: {provider.get('credentialProviderVendor', 'N/A')}")
        if "createdTime" in provider:
            click.echo(f"    Created: {provider['createdTime']}")
        click.echo()


if __name__ == "__main__":
    cli()
