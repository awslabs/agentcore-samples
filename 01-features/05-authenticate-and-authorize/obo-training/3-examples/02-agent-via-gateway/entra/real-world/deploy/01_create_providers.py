"""
Create AgentCore Identity resources for Use Case 2 real-world (Entra).

Creates:
  - One workload identity for the deployed agent (used by OBO #1).
  - Two CustomOauth2 credential providers, both configured for OBO via the
    JWT_AUTHORIZATION_GRANT grant type (Entra's RFC 7523 flavor):

      * AGENT_OBO_PROVIDER_NAME    — auths as AgentApp; used by agent code
                                     for OBO #1 (T_user → T_gateway).
      * GATEWAY_OBO_PROVIDER_NAME  — auths as GatewayApp; used by AgentCore
                                     Gateway for OBO #2 (T_gateway → T_graph).

Why two providers? Each OBO hop authenticates as a different Entra app, so
each needs its own client_id + client_secret stored on AgentCore Identity.

Why not the built-in MicrosoftOauth2 vendor? `onBehalfOfTokenExchangeConfig`
is only available inside `customOauth2ProviderConfig`. The built-in vendor
auto-configures OBO for direct AgentCore Identity calls but does not surface
the config knobs Gateway needs.

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
        code = e.response["Error"].get("Code", "")
        msg = e.response["Error"].get("Message", "")
        # AgentCore returns ValidationException with an "already exists"
        # message when the workload is a duplicate, instead of ConflictException.
        already_exists = code in {
            "ConflictException",
            "ResourceAlreadyExistsException",
        } or ("already exists" in msg.lower())
        if already_exists:
            print(f"• Workload identity already exists: {name}")
        else:
            raise


def ensure_obo_provider(
    client,
    *,
    name: str,
    tenant_id: str,
    client_id: str,
    client_secret: str,
) -> str:
    """Create-or-update a CustomOauth2 credential provider for Entra OBO.

    On re-runs, always UPDATES the provider with the current client_id and
    client_secret from .env. This is important: the previous behavior of
    "skip if exists" silently leaves stale credentials in place, which
    then causes OBO exchanges to fail with an opaque `HTTP 400` from
    AgentCore Identity (Entra returns AADSTS7000215 but AgentCore hides it).

    Returns the provider ARN.
    """
    discovery_url = f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
    config = {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": discovery_url},
            "clientId": client_id,
            "clientSecret": client_secret,
            "clientAuthenticationMethod": "CLIENT_SECRET_POST",
            "onBehalfOfTokenExchangeConfig": {
                "grantType": "JWT_AUTHORIZATION_GRANT",
            },
        }
    }

    # Try create first; on conflict, update in place so the provider reflects
    # the client_id/client_secret currently in .env. All success-path prints
    # are omitted from this function — it receives a client_secret argument,
    # so CodeQL's clear-text-logging query flags any print in scope even
    # when the message body is static. The caller prints a summary.
    try:
        resp = client.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )
        return resp["credentialProviderArn"]
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        msg = e.response["Error"].get("Message", "")
        already_exists = code in {
            "ConflictException",
            "ResourceAlreadyExistsException",
        } or ("already exists" in msg.lower())
        if not already_exists:
            raise

    # Already exists — force an update so client_id/client_secret are fresh.
    try:
        resp = client.update_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )
        return resp["credentialProviderArn"]
    except ClientError as e:
        # Fall back to fetching the ARN for callers that don't need fresh
        # credentials, but surface the error clearly.
        existing = client.get_oauth2_credential_provider(name=name)
        arn = existing["credentialProviderArn"]
        print(
            f"⚠ Could not update credential provider '{name}': "
            f"{e.response['Error'].get('Code')}: {e.response['Error'].get('Message')}\n"
            f"  Using existing (possibly stale) provider. If OBO fails at "
            f"runtime with HTTP 400, delete and recreate:\n"
            f"    aws bedrock-agentcore-control delete-oauth2-credential-provider "
            f"--name {name}\n"
            f"    python deploy/01_create_providers.py",
            file=sys.stderr,
        )
        return arn


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    region = os.environ.get("AWS_REGION", "us-west-2")

    tenant_id = must_env("TENANT_ID")
    agent_client_id = must_env("AGENT_CLIENT_ID")
    agent_client_secret = must_env("AGENT_CLIENT_SECRET")
    gateway_client_id = must_env("GATEWAY_CLIENT_ID")
    gateway_client_secret = must_env("GATEWAY_CLIENT_SECRET")
    workload_name = must_env("AGENT_WORKLOAD_NAME")
    agent_obo_provider_name = must_env("AGENT_OBO_PROVIDER_NAME")
    gateway_obo_provider_name = must_env("GATEWAY_OBO_PROVIDER_NAME")

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Region:        {region}")
    print(f"Tenant:        {tenant_id}")
    print(f"AgentApp:      {agent_client_id}")
    print(f"GatewayApp:    {gateway_client_id}")
    print(f"Workload:      {workload_name}")
    print(f"Agent OBO:     {agent_obo_provider_name}")
    print(f"Gateway OBO:   {gateway_obo_provider_name}")
    print()

    ensure_workload_identity(ac_control, workload_name)

    print()
    agent_provider_arn = ensure_obo_provider(
        ac_control,
        name=agent_obo_provider_name,
        tenant_id=tenant_id,
        client_id=agent_client_id,
        client_secret=agent_client_secret,
    )

    print()
    gateway_provider_arn = ensure_obo_provider(
        ac_control,
        name=gateway_obo_provider_name,
        tenant_id=tenant_id,
        client_id=gateway_client_id,
        client_secret=gateway_client_secret,
    )

    print()
    print("✓ AgentCore Identity resources ready.")
    print()
    print("Provider ARNs (you'll need the Gateway one in step 02):")
    print(f"  Agent provider ARN:   {agent_provider_arn}")
    print(f"  Gateway provider ARN: {gateway_provider_arn}")
    print()
    print("Next step: python deploy/02_create_gateway.py")


if __name__ == "__main__":
    main()
