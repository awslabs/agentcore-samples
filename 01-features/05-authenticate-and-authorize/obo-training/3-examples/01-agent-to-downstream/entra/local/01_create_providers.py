"""
One-time setup: create AgentCore Identity resources for Use Case 1 (Entra flavor).

Creates:
- A workload identity for the agent.
- A "client" OAuth2 credential provider used for the 3LO sign-in (simulating a frontend).
- An "actor" OAuth2 credential provider used for the OBO exchange.

Both credential providers use the built-in MicrosoftOauth2 vendor. For Entra, the
built-in provider auto-configures OBO (JWT_AUTHORIZATION_GRANT + requested_token_use=on_behalf_of).

Run once, idempotently. Re-running will skip resources that already exist.

Usage:
    python 01_create_providers.py
"""

from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"ERROR: required env var {name} is not set. See config.example.env.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def ensure_workload_identity(client, name: str) -> dict:
    try:
        resp = client.create_workload_identity(name=name)
        print(f"✓ Created workload identity: {name}")
        return resp
    except ClientError as e:
        if e.response["Error"]["Code"] in {
            "ConflictException",
            "ResourceAlreadyExistsException",
        }:
            print(f"• Workload identity already exists: {name}")
            return client.get_workload_identity(name=name)
        raise


def ensure_microsoft_provider(
    client,
    *,
    name: str,
    client_id: str,
    client_secret: str,
    tenant_id: str,
) -> dict:
    """Create (or fetch) a MicrosoftOauth2 credential provider.

    The built-in MicrosoftOauth2 vendor automatically configures OBO support
    (JWT_AUTHORIZATION_GRANT) when ON_BEHALF_OF_TOKEN_EXCHANGE is requested at
    runtime. No explicit onBehalfOfTokenExchangeConfig is required.
    """
    config = {
        "microsoftOauth2ProviderConfig": {
            "clientId": client_id,
            "clientSecret": client_secret,
            "tenantId": tenant_id,
        }
    }
    try:
        resp = client.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="MicrosoftOauth2",
            oauth2ProviderConfigInput=config,
        )
        # No print here — this function receives a client_secret argument,
        # so CodeQL's clear-text-logging query flags any print in scope.
        # main() prints a summary after the function returns.
        return resp
    except ClientError as e:
        if e.response["Error"]["Code"] in {
            "ConflictException",
            "ResourceAlreadyExistsException",
        }:
            return client.get_oauth2_credential_provider(name=name)
        raise


def main() -> None:
    load_dotenv()
    region = os.environ.get("AWS_REGION", "us-west-2")

    tenant_id = must_env("TENANT_ID")
    agent_client_id = must_env("AGENT_CLIENT_ID")
    agent_client_secret = must_env("AGENT_CLIENT_SECRET")
    workload_name = must_env("WORKLOAD_NAME")
    client_provider_name = must_env("CLIENT_PROVIDER_NAME")
    actor_provider_name = must_env("ACTOR_PROVIDER_NAME")

    # AgentCore control plane client
    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Region: {region}")
    print(f"Tenant:  {tenant_id}")
    print(f"Agent client ID: {agent_client_id}")
    print()

    ensure_workload_identity(ac_control, workload_name)

    # Client provider — used for the initial 3LO sign-in to get a user JWT.
    # In production this role is played by the real frontend, not AgentCore.
    ensure_microsoft_provider(
        ac_control,
        name=client_provider_name,
        client_id=agent_client_id,
        client_secret=agent_client_secret,
        tenant_id=tenant_id,
    )

    # Actor provider — used for the OBO exchange.
    # For this simple example we reuse the same Entra app for both roles. In a
    # realistic multi-hop setup you would have separate apps for the frontend client
    # and the middle-tier actor.
    ensure_microsoft_provider(
        ac_control,
        name=actor_provider_name,
        client_id=agent_client_id,
        client_secret=agent_client_secret,
        tenant_id=tenant_id,
    )

    # The client provider has a managed callback URL that you need to add
    # to your Entra app registration's redirect URIs. Surface guidance for
    # the user — the actual URL is visible in the AWS console.
    print()
    print("Next steps:")
    print(" 1. If your Entra app doesn't yet have the AgentCore-managed redirect URI as")
    print("    a platform redirect, add it in Entra → App registrations → Authentication.")
    print("    You can find the redirect URI in the AWS console under AgentCore Identity →")
    print(f"    Credential providers → {client_provider_name}.")
    print(" 2. Run: python 02_run_example.py")


if __name__ == "__main__":
    main()
