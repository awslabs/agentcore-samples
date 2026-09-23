"""
Create AgentCore Identity resources for Use Case 2 real-world (Okta).

Creates:
  - One workload identity for the deployed agent (used by OBO #1).
  - Two CustomOauth2 credential providers, both configured for OBO via
    RFC 8693 Token Exchange (Okta's OBO flavor):

      * AGENT_OBO_PROVIDER_NAME    — auths as AgentApp; used by agent code
                                     for OBO #1 (T_user -> T_gateway).
      * GATEWAY_OBO_PROVIDER_NAME  — auths as GatewayApp; used by AgentCore
                                     Gateway for OBO #2 (T_gateway ->
                                     T_downstream).

Why two providers? Each OBO hop authenticates as a different Okta app, so
each needs its own client_id + client_secret stored on AgentCore Identity.

Why CustomOauth2 (and not the built-in OktaOauth2 vendor)? The built-in
vendor auto-configures a few things for standard flows but does not surface
the `actorTokenContent` knob that Okta's Token Exchange grant needs. With
`CustomOauth2` we get the full `onBehalfOfTokenExchangeConfig` schema.

Key Okta-vs-Entra differences from UC2 Entra's version of this script:
  - grantType is TOKEN_EXCHANGE (Okta uses RFC 8693) — not JWT_AUTHORIZATION_GRANT.
  - tokenExchangeGrantTypeConfig.actorTokenContent = NONE tells AgentCore
    to omit the actor_token from the exchange request. Only the client
    credentials identify the acting party.
  - clientAuthenticationMethod is CLIENT_SECRET_BASIC (Okta's default).
  - Discovery URL is Okta-flavored: /oauth2/<auth-server-id>/.well-known/*
    instead of Entra's /<tenant>/v2.0/.well-known/*.
  - No `requestedAccessTokenVersion` concern — Okta doesn't have a v1/v2
    token version distinction the way Entra does.

Also preflights the discovery URL before calling AWS so a typo in
OKTA_DOMAIN fails fast with a clear message.

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

    Catches the most common misconfigurations — using the -admin host, wrong
    auth server ID, typo in the domain — with clear hints BEFORE AWS returns
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
        print(
            f"✗ Discovery URL returned HTTP {e.code}. Check OKTA_DOMAIN and OKTA_AUTH_SERVER_ID in .env.",
            file=sys.stderr,
        )
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
        code = e.response["Error"].get("Code", "")
        msg = e.response["Error"].get("Message", "")
        # AgentCore returns ValidationException with an "already exists"
        # message when the workload is a duplicate instead of ConflictException.
        already_exists = code in {
            "ConflictException",
            "ResourceAlreadyExistsException",
        } or ("already exists" in msg.lower())
        if already_exists:
            print(f"• Workload identity already exists: {name}")
        else:
            raise


def ensure_okta_obo_provider(
    client,
    *,
    name: str,
    domain: str,
    auth_server_id: str,
    client_id: str,
    client_secret: str,
) -> str:
    """Create-or-update a CustomOauth2 credential provider for Okta OBO.

    On re-runs, always UPDATES the provider with the current client_id and
    client_secret from .env. This is important: a "skip if exists" behavior
    would silently leave stale credentials in place, which causes OBO
    exchanges to fail with an opaque HTTP 400 from AgentCore Identity (Okta
    returns an `invalid_client` error but AgentCore hides the payload).

    Returns the provider ARN.
    """
    config = {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {
                "discoveryUrl": _discovery_url(domain, auth_server_id),
            },
            "clientId": client_id,
            "clientSecret": client_secret,
            # Okta accepts client_secret_basic (Authorization: Basic ...)
            # for token endpoint auth. It's the default for confidential
            # clients on the default auth server.
            "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
            "onBehalfOfTokenExchangeConfig": {
                # RFC 8693 Token Exchange — Okta's OBO grant. Distinct from
                # Entra's JWT_AUTHORIZATION_GRANT (RFC 7523).
                "grantType": "TOKEN_EXCHANGE",
                "tokenExchangeGrantTypeConfig": {
                    # Tell AgentCore NOT to send an actor_token in the
                    # exchange request. Only the client credentials identify
                    # the actor. This is what UC1 Okta uses and matches
                    # Okta's expected shape for OBO on the default auth
                    # server (which doesn't accept an actor_token).
                    "actorTokenContent": "NONE",
                },
            },
        }
    }

    # Try create first; on conflict, update in place so the provider reflects
    # the client_id / client_secret currently in .env.
    try:
        resp = client.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )
        # No success-path print here — this function receives a client_secret
        # argument, so CodeQL's clear-text-logging query flags any print in
        # scope even for static message bodies. Caller prints a summary.
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

    # Already exists — force an update so client_id / client_secret are fresh.
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
            f"runtime with HTTP 400 / invalid_client, delete and recreate:\n"
            f"    aws bedrock-agentcore-control delete-oauth2-credential-provider "
            f"--name {name}\n"
            f"    python deploy/01_create_providers.py",
            file=sys.stderr,
        )
        return arn


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    region = os.environ.get("AWS_REGION", "us-west-2")

    domain = must_env("OKTA_DOMAIN")
    auth_server_id = must_env("OKTA_AUTH_SERVER_ID")
    agent_client_id = must_env("AGENT_CLIENT_ID")
    agent_client_secret = must_env("AGENT_CLIENT_SECRET")
    gateway_client_id = must_env("GATEWAY_CLIENT_ID")
    gateway_client_secret = must_env("GATEWAY_CLIENT_SECRET")
    workload_name = must_env("AGENT_WORKLOAD_NAME")
    agent_obo_provider_name = must_env("AGENT_OBO_PROVIDER_NAME")
    gateway_obo_provider_name = must_env("GATEWAY_OBO_PROVIDER_NAME")

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Region:        {region}")
    print(f"Okta:          {domain} / auth server '{auth_server_id}'")
    print(f"AgentApp:      {agent_client_id}")
    print(f"GatewayApp:    {gateway_client_id}")
    print(f"Workload:      {workload_name}")
    print(f"Agent OBO:     {agent_obo_provider_name}")
    print(f"Gateway OBO:   {gateway_obo_provider_name}")
    print()

    preflight_discovery_url(domain, auth_server_id)
    print()

    ensure_workload_identity(ac_control, workload_name)

    print()
    agent_provider_arn = ensure_okta_obo_provider(
        ac_control,
        name=agent_obo_provider_name,
        domain=domain,
        auth_server_id=auth_server_id,
        client_id=agent_client_id,
        client_secret=agent_client_secret,
    )

    print()
    gateway_provider_arn = ensure_okta_obo_provider(
        ac_control,
        name=gateway_obo_provider_name,
        domain=domain,
        auth_server_id=auth_server_id,
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
