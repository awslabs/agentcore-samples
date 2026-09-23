"""
One-time setup: create AgentCore Identity resources for Use Case 1 (Okta flavor).

Creates:
- A workload identity for the agent.
- A "client" OAuth2 credential provider (CustomOauth2) for the 3LO sign-in via the
  Okta native app.
- An "actor" OAuth2 credential provider (CustomOauth2) for the OBO token exchange,
  using the Okta service app's client credentials.

For Okta, the built-in OktaOauth2 provider does NOT support OBO. We use CustomOauth2
with onBehalfOfTokenExchangeConfig.grantType = TOKEN_EXCHANGE for the actor provider.

Run once, idempotently.

Usage:
    python 01_create_providers.py
"""

from __future__ import annotations

import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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


def preflight_discovery_url(domain: str, auth_server_id: str) -> None:
    """Fetch the OIDC discovery document ourselves before handing it to AgentCore.

    AgentCore's 'Invalid Discovery URL' error is opaque. This preflight check
    surfaces the actual HTTP problem (404, DNS failure, redirect) with a
    clearer message and concrete remediation steps.
    """
    # Guard against the very common mistake of using the admin console URL.
    # Okta's app-facing host and admin host both serve the OIDC discovery doc,
    # so a simple HTTP check won't catch it — we block it here explicitly.
    if "-admin." in domain:
        print(
            f"✗ OKTA_DOMAIN={domain!r} looks like the Okta admin host.\n"
            f"  Remove the '-admin' substring and use the app-facing host instead\n"
            f"  (e.g. {domain.replace('-admin.', '.')}). Tokens issued under the\n"
            f"  admin host will have an issuer other downstream consumers won't\n"
            f"  expect, and AgentCore may reject the provider at exchange time.",
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
        _print_discovery_hints(domain, auth_server_id)
        sys.exit(1)
    except URLError as e:
        print(f"✗ Could not reach discovery URL: {e.reason}", file=sys.stderr)
        print(
            "  Check OKTA_DOMAIN is correct and reachable from your network.",
            file=sys.stderr,
        )
        sys.exit(1)

    if status != 200 or '"issuer"' not in body:
        print(
            f"✗ Discovery URL returned HTTP {status} but content doesn't look like an OIDC discovery doc.",
            file=sys.stderr,
        )
        print(
            "  First 500 chars:\n    " + body[:500].replace("\n", "\n    "),
            file=sys.stderr,
        )
        _print_discovery_hints(domain, auth_server_id)
        sys.exit(1)

    print("✓ Discovery URL reachable and returns a valid-looking OIDC document.")


def _print_discovery_hints(domain: str, auth_server_id: str) -> None:
    print(
        "\n  Usual causes of 'Invalid Discovery URL':\n"
        f"  1. OKTA_AUTH_SERVER_ID ({auth_server_id!r}) doesn't match an authorization\n"
        f"     server in your tenant. Check Okta admin → Security → API → Authorization\n"
        f"     Servers. Fresh Integrator orgs sometimes have no 'default' server — you\n"
        f"     need to create one (or use a different auth server's ID).\n"
        f"  2. OKTA_DOMAIN ({domain!r}) has a typo or is the admin host. Use the\n"
        f"     app-facing host (e.g. integrator-1234567.okta.com), not the admin one\n"
        f"     (integrator-1234567-admin.okta.com). Drop '-admin' if it's there.\n"
        f"  3. Your tenant has been renamed and the old domain no longer resolves.\n"
        f"\n"
        f"  Open the URL in a browser with the values you have in .env:\n"
        f"     https://{domain}/oauth2/{auth_server_id}/.well-known/openid-configuration\n"
        f"  A working tenant returns a JSON document with 'issuer', 'jwks_uri', etc.\n",
        file=sys.stderr,
    )


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


def _discovery_url(domain: str, auth_server_id: str) -> str:
    return f"https://{domain}/oauth2/{auth_server_id}/.well-known/openid-configuration"


def ensure_okta_client_provider(
    client,
    *,
    name: str,
    domain: str,
    auth_server_id: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Native app provider — for the initial 3LO user sign-in. No OBO config."""
    config = {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {
                "discoveryUrl": _discovery_url(domain, auth_server_id),
            },
            "clientId": client_id,
            "clientSecret": client_secret,
            "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
        }
    }
    try:
        resp = client.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
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


def ensure_okta_actor_provider(
    client,
    *,
    name: str,
    domain: str,
    auth_server_id: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Service app provider — performs the OBO exchange via RFC 8693 token exchange."""
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
        resp = client.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
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

    domain = must_env("OKTA_DOMAIN")
    auth_server_id = must_env("OKTA_AUTH_SERVER_ID")

    native_client_id = must_env("NATIVE_APP_CLIENT_ID")
    native_client_secret = must_env("NATIVE_APP_CLIENT_SECRET")
    service_client_id = must_env("SERVICE_APP_CLIENT_ID")
    service_client_secret = must_env("SERVICE_APP_CLIENT_SECRET")

    workload_name = must_env("WORKLOAD_NAME")
    client_provider_name = must_env("CLIENT_PROVIDER_NAME")
    actor_provider_name = must_env("ACTOR_PROVIDER_NAME")

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Region:     {region}")
    print(f"Okta:       {domain} / auth server '{auth_server_id}'")
    print(f"Native app: {native_client_id}")
    print(f"Service app:{service_client_id}")
    print()

    preflight_discovery_url(domain, auth_server_id)
    print()

    ensure_workload_identity(ac_control, workload_name)
    ensure_okta_client_provider(
        ac_control,
        name=client_provider_name,
        domain=domain,
        auth_server_id=auth_server_id,
        client_id=native_client_id,
        client_secret=native_client_secret,
    )
    ensure_okta_actor_provider(
        ac_control,
        name=actor_provider_name,
        domain=domain,
        auth_server_id=auth_server_id,
        client_id=service_client_id,
        client_secret=service_client_secret,
    )

    print()
    print("Next steps:")
    print(" 1. Register AgentCore's managed callback URL on the Okta Native App.")
    print("    Find the URL in the AWS console under AgentCore Identity →")
    print(f"    Credential providers → {client_provider_name} (look for the")
    print("    return URL under `oauthDiscovery`).")
    print("    In Okta: Applications → <native app> → General tab → General")
    print("    Settings → Edit → Sign-in redirect URIs → Add URI → Save.")
    print("    (Full walkthrough in IDP_SETUP.md Step 5.)")
    print(" 2. Run: python 02_run_example.py")


if __name__ == "__main__":
    main()
