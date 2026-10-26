"""
Create AgentCore Identity resources for the real-world example.

Creates:
  - Workload identity for the deployed agent.
  - One MicrosoftOauth2 credential provider that the agent uses to do OBO.

This script creates resources with names that are distinct from the `local/`
variant (WORKLOAD_NAME defaults to obo-usecase1-entra-realworld,
ACTOR_PROVIDER_NAME defaults to obo-uc1-entra-realworld-actor), so running
this won't interfere with any local-variant resources you already have. The
local and real-world variants are independent unless you deliberately
point both at the same names.

This example does NOT create a separate "client" credential provider — the
frontend talks to Entra directly via MSAL, not via AgentCore.

Run:
    python deploy/01_create_providers.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def ensure_workload_identity(client, name: str) -> None:
    try:
        client.create_workload_identity(name=name)
        print(f"✓ Created workload identity: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] in {
            "ConflictException",
            "ResourceAlreadyExistsException",
        }:
            print(f"• Workload identity already exists: {name}")
        else:
            raise


def ensure_actor_provider(
    client,
    *,
    name: str,
    client_id: str,
    client_secret: str,
    tenant_id: str,
) -> None:
    """Create the OBO-enabled credential provider.

    Built-in MicrosoftOauth2 auto-configures OBO (JWT_AUTHORIZATION_GRANT +
    requested_token_use=on_behalf_of) for ON_BEHALF_OF_TOKEN_EXCHANGE calls.
    """
    config = {
        "microsoftOauth2ProviderConfig": {
            "clientId": client_id,
            "clientSecret": client_secret,
            "tenantId": tenant_id,
        }
    }
    try:
        client.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="MicrosoftOauth2",
            oauth2ProviderConfigInput=config,
        )
        # No print here — this function receives a client_secret argument,
        # so CodeQL's clear-text-logging query flags any print in scope.
        # main() prints a summary after the function returns.
    except ClientError as e:
        if e.response["Error"]["Code"] in {
            "ConflictException",
            "ResourceAlreadyExistsException",
        }:
            pass  # already exists — main() logs the summary
        else:
            raise


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    region = os.environ.get("AWS_REGION", "us-west-2")

    tenant_id = must_env("TENANT_ID")
    agent_client_id = must_env("AGENT_CLIENT_ID")
    agent_client_secret = must_env("AGENT_CLIENT_SECRET")
    workload_name = must_env("WORKLOAD_NAME")
    actor_provider_name = must_env("ACTOR_PROVIDER_NAME")

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Region:   {region}")
    print(f"Tenant:   {tenant_id}")
    print(f"Agent:    {agent_client_id}")
    print(f"Workload: {workload_name}")
    print(f"Provider: {actor_provider_name}")
    print()

    ensure_workload_identity(ac_control, workload_name)
    ensure_actor_provider(
        ac_control,
        name=actor_provider_name,
        client_id=agent_client_id,
        client_secret=agent_client_secret,
        tenant_id=tenant_id,
    )

    print("\n✓ AgentCore Identity resources ready.")
    print("Next step: follow README.md sections 5–10 to scaffold and deploy the agent with the AgentCore CLI.")


if __name__ == "__main__":
    main()
