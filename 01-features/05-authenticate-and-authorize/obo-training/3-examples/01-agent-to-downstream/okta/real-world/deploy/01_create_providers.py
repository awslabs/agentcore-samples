"""
Create AgentCore Identity resources for the Okta real-world example.

Creates:
  - Workload identity for the deployed agent.
  - One CustomOauth2 credential provider that the agent uses to do OBO
    (RFC 8693 token exchange) against Okta, using the Service App's
    client credentials.

This example does NOT create a separate "client" credential provider — the
frontend talks to Okta directly via authlib, not via AgentCore. AgentCore
Identity is only involved in the OBO hop.

Names default to -realworld suffixes so this doesn't collide with any
resources already created by the `local/` variant in the same AWS account.

Run:
    python deploy/01_create_providers.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def _discovery_url(domain: str, auth_server_id: str) -> str:
    return f"https://{domain}/oauth2/{auth_server_id}/.well-known/openid-configuration"


def preflight_discovery_url(domain: str, auth_server_id: str) -> None:
    """Fetch the Okta OIDC discovery document before handing it to AgentCore.

    Catches the most common misconfigurations — using the admin host, wrong
    auth server ID, typo in the domain — with clear hints before AWS returns
    its opaque 'Invalid Discovery URL'.
    """
    if "-admin." in domain:
        print(
            f"✗ OKTA_DOMAIN={domain!r} looks like the Okta admin host.\n"
            f"  Remove the '-admin' substring and use the app-facing host instead\n"
            f"  (e.g. {domain.replace('-admin.', '.')}).",
            file=sys.stderr,
        )
        sys.exit(1)

    url = _discovery_url(domain, auth_server_id)
    print(f"Preflighting discovery URL:\n  {url}")
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=10) as resp:
            status = resp.status
            body = resp.read(2048).decode("utf-8", errors="replace")
    except HTTPError as e:
        print(f"✗ Discovery URL returned HTTP {e.code}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"✗ Could not reach discovery URL: {e.reason}", file=sys.stderr)
        sys.exit(1)

    if status != 200 or '"issuer"' not in body:
        print(
            f"✗ Discovery URL returned HTTP {status} but content doesn't look like an OIDC doc.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("✓ Discovery URL reachable and returns a valid-looking OIDC document.")


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


def ensure_okta_actor_provider(
    client,
    *,
    name: str,
    domain: str,
    auth_server_id: str,
    client_id: str,
    client_secret: str,
) -> None:
    """Create the OBO-enabled credential provider using Okta's Token Exchange grant."""
    config = {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {
                "discoveryUrl": _discovery_url(domain, auth_server_id),
            },
            "clientId": client_id,
            "clientSecret": client_secret,
            "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
            "onBehalfOfTokenExchangeConfig": {
                "grantType": "TOKEN_EXCHANGE",
                "tokenExchangeGrantTypeConfig": {
                    "actorTokenContent": "NONE",
                },
            },
        }
    }
    try:
        client.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
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

    domain = must_env("OKTA_DOMAIN")
    auth_server_id = must_env("OKTA_AUTH_SERVER_ID")
    agent_client_id = must_env("AGENT_CLIENT_ID")
    agent_client_secret = must_env("AGENT_CLIENT_SECRET")
    workload_name = must_env("WORKLOAD_NAME")
    actor_provider_name = must_env("ACTOR_PROVIDER_NAME")

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Region:   {region}")
    print(f"Okta:     {domain} / auth server '{auth_server_id}'")
    print(f"Service:  {agent_client_id}")
    print(f"Workload: {workload_name}")
    print(f"Provider: {actor_provider_name}")
    print()

    preflight_discovery_url(domain, auth_server_id)
    print()

    ensure_workload_identity(ac_control, workload_name)
    ensure_okta_actor_provider(
        ac_control,
        name=actor_provider_name,
        domain=domain,
        auth_server_id=auth_server_id,
        client_id=agent_client_id,
        client_secret=agent_client_secret,
    )

    print("\n✓ AgentCore Identity resources ready.")
    print("Next step: follow README.md sections 5–10 to scaffold and deploy the agent with the AgentCore CLI.")


if __name__ == "__main__":
    main()
