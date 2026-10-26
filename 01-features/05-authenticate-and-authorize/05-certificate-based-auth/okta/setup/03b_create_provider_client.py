"""
Create the AgentCore Identity credential provider used for user 3LO
sign-in (USER_FEDERATION), then wire its managed callback URL onto
the Okta Web app's redirect_uri list.

This is the "client provider" side of the obo-training pattern -
distinct from the M2M and OBO providers (setup/02 and setup/03) which
authenticate the service app to Okta's token endpoint. The client
provider authenticates the Web app to Okta's token endpoint during
the authorization_code exchange that happens after a user logs in.

Both leg types use PRIVATE_KEY_JWT and share the same KMS key + JWK -
different Okta apps, same signing key.

What this script does:
  1. Creates a CustomOauth2 credential provider CLIENT_PROVIDER_NAME
     with:
       - clientAuthenticationMethod = PRIVATE_KEY_JWT
       - clientId                   = OKTA_LOGIN_APP_CLIENT_ID
       - signingAlgorithm           = RS256
       - additionalHeaderClaims.kid = SIGNING_KID
       - (no onBehalfOfTokenExchangeConfig - this provider is only for
         3LO, not OBO)
     Captures the response's `callbackUrl` (AgentCore Identity's
     managed callback URL for this provider).
  2. Updates the Okta Web app's redirect_uris to include that callback
     URL (replacing the placeholder that setup/01b registered).
  3. Updates the AgentCore workload identity to allow the local
     callback URL as an allowedResourceOauth2ReturnUrls entry - the
     3LO flow bounces from AgentCore's managed callback to this local
     URL with a session_id.
  4. Writes CLIENT_PROVIDER_NAME, AGENTCORE_MANAGED_CALLBACK_URL, and
     LOCAL_CALLBACK_URL to .env.

Idempotent: safe to re-run. If the provider already exists it is
updated in place.

Usage:
    python setup/03b_create_provider_client.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_LOCAL_CALLBACK_URL = "http://localhost:8081/callback"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def okta_headers(api_token: str) -> dict:
    return {
        "Authorization": f"SSWS {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def discovery_url(domain: str, auth_server_id: str) -> str:
    return f"https://{domain}/oauth2/{auth_server_id}/.well-known/openid-configuration"


def build_provider_config(
    *,
    discovery_url_value: str,
    client_id: str,
    kms_key_arn: str,
    kid: str,
) -> dict:
    """CustomOauth2 provider config for USER_FEDERATION (3LO) with PRIVATE_KEY_JWT.

    No onBehalfOfTokenExchangeConfig - this provider is used only for
    the authorization_code exchange after browser login.
    """
    return {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": discovery_url_value},
            "clientId": client_id,
            "clientAuthenticationMethod": "PRIVATE_KEY_JWT",
            "privateKeyJwtConfig": {
                "privateKeySource": {"kmsKeySource": {"kmsKeyArn": kms_key_arn}},
                "signingAlgorithm": "RS256",
                "additionalHeaderClaims": {"kid": kid},
            },
        }
    }


def upsert_provider(control, *, name: str, config: dict) -> tuple[str, str]:
    """Create-or-update the credential provider. Return (arn, callbackUrl)."""
    try:
        resp = control.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )
        print(f"✓ Created client credential provider: {name}")
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        msg = e.response["Error"].get("Message", "")
        already_exists = code in {
            "ConflictException",
            "ResourceAlreadyExistsException",
        } or ("already exists" in msg.lower())
        if not already_exists:
            raise
        resp = control.update_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )
        print(f"✓ Updated client credential provider: {name}")
    return resp["credentialProviderArn"], resp.get("callbackUrl", "")


def update_okta_app_redirect_uris(
    base_url: str,
    api_token: str,
    app_id: str,
    callback_url: str,
) -> None:
    """Overwrite the Okta Web app's redirect_uris with the AgentCore callback URL."""
    resp = requests.get(
        f"{base_url}/api/v1/apps/{app_id}",
        headers=okta_headers(api_token),
        timeout=15,
    )
    if resp.status_code >= 400:
        print(f"⚠ Could not fetch Okta app {app_id}: HTTP {resp.status_code}")
        return
    app = resp.json()
    existing_uris = app.get("settings", {}).get("oauthClient", {}).get("redirect_uris", [])
    # Keep any real URIs the admin added, and add ours if not already present.
    real_uris = [u for u in existing_uris if "placeholder" not in u and "example.com" not in u]
    if callback_url in real_uris:
        merged = real_uris
        print("• Callback URL already registered on Okta Web app")
    else:
        merged = [*real_uris, callback_url]
        print(f"→ Setting Okta Web app redirect_uris to {merged}")

    # Preserve everything, just swap redirect_uris
    app.setdefault("settings", {}).setdefault("oauthClient", {})["redirect_uris"] = merged
    put = requests.put(
        f"{base_url}/api/v1/apps/{app_id}",
        headers=okta_headers(api_token),
        data=json.dumps(app),
        timeout=15,
    )
    if put.status_code >= 400:
        print(
            f"✗ PUT app returned HTTP {put.status_code}: {put.text[:400]}",
            file=sys.stderr,
        )
        put.raise_for_status()
    print("✓ Updated Okta Web app redirect_uris")


def _manual_redirect_uri_instructions(base_url: str, login_app_id: str, callback_url: str) -> str:
    """Text block telling the user which field to paste the URL into."""
    return (
        f"  In the Okta admin console:\n"
        f"    Applications → Applications → your login web app → General tab\n"
        f"    LOGIN → General Settings → Edit → Sign-in redirect URIs\n"
        f"    → Add URI → paste:\n"
        f"        {callback_url}\n"
        f"    → Save.\n"
        f"  Direct URL:\n"
        f"    {base_url}/admin/app/oidc_client/instance/{login_app_id}\n"
        f"  (OKTA_API_TOKEN was not set in .env, so setup/03b skipped the\n"
        f"   automated PUT. The rest of this script still ran.)"
    )


def update_workload_return_url(control, workload_name: str, local_url: str) -> None:
    """Add the local callback URL to the workload identity's allowed list."""
    try:
        existing = control.get_workload_identity(name=workload_name)
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code in {"ResourceNotFoundException", "NotFoundException"}:
            print(f"⚠ Workload identity {workload_name!r} not found. Run setup/02 or 03 first.")
            return
        raise

    existing_urls = existing.get("allowedResourceOauth2ReturnUrls", []) or []
    if local_url in existing_urls:
        print(f"• Local callback already in workload allowed URLs: {local_url}")
        return
    merged = [*existing_urls, local_url]
    control.update_workload_identity(
        name=workload_name,
        allowedResourceOauth2ReturnUrls=merged,
    )
    print(f"✓ Added local callback to workload allowed URLs: {local_url}")


def write_env_var(name: str, value: str) -> None:
    text = ENV_FILE.read_text()
    pattern = rf"^{re.escape(name)}=.*$"
    replacement = f"{name}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        new_text = text.rstrip() + f"\n{replacement}\n"
    ENV_FILE.write_text(new_text)
    print(f"✓ Wrote {name} to .env")


def main() -> None:
    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")

    domain = must_env("OKTA_DOMAIN")
    auth_server_id = must_env("OKTA_AUTH_SERVER_ID")
    # OKTA_API_TOKEN is optional here - if unset we skip the Okta
    # redirect_uri PUT and print instructions for adding the URL
    # manually. Everything else (AgentCore provider creation + workload
    # allowed-URL update) works either way.
    api_token = os.environ.get("OKTA_API_TOKEN") or None
    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    kid = must_env("SIGNING_KID")
    workload_name = must_env("WORKLOAD_NAME")
    login_app_id = must_env("OKTA_LOGIN_APP_ID")
    login_app_client_id = must_env("OKTA_LOGIN_APP_CLIENT_ID")
    provider_name = os.environ.get("CLIENT_PROVIDER_NAME") or "pkjwt-okta-sample-client"
    local_url = os.environ.get("LOCAL_CALLBACK_URL") or DEFAULT_LOCAL_CALLBACK_URL

    base_url = f"https://{domain}"
    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Region:         {region}")
    print(f"Okta:           {base_url} / AS {auth_server_id!r}")
    print(f"Login app:      {login_app_client_id}")
    print(f"Workload:       {workload_name}")
    print(f"Provider:       {provider_name}")
    print(f"Local callback: {local_url}")
    print(f"Okta mode:      {'API (auto-update redirect_uri)' if api_token else 'MANUAL (redirect_uri to be pasted)'}")
    print()

    # 1. Create-or-update the client credential provider
    config = build_provider_config(
        discovery_url_value=discovery_url(domain, auth_server_id),
        client_id=login_app_client_id,
        kms_key_arn=key_arn,
        kid=kid,
    )
    print("→ Creating client credential provider (USER_FEDERATION)...")
    arn, callback_url = upsert_provider(ac_control, name=provider_name, config=config)
    if not callback_url:
        print(
            "✗ AgentCore did not return a callbackUrl for the client provider - cannot wire the Okta redirect_uri.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"  AgentCore-managed callback URL: {callback_url}")

    # 2. Update the Okta Web app's redirect_uris (API path) or tell the
    #    user what to paste in the admin console (manual path).
    print()
    if api_token:
        print("→ Wiring AgentCore callback URL onto Okta Web app...")
        update_okta_app_redirect_uris(base_url, api_token, login_app_id, callback_url)
    else:
        print("→ Manual Okta step required")
        print(_manual_redirect_uri_instructions(base_url, login_app_id, callback_url))

    # 3. Add the local callback URL to the workload identity's allowed list
    print()
    print("→ Adding local callback URL to workload identity allowed list...")
    update_workload_return_url(ac_control, workload_name, local_url)

    # 4. Persist identifiers
    print()
    write_env_var("CLIENT_PROVIDER_NAME", provider_name)
    write_env_var("AGENTCORE_MANAGED_CALLBACK_URL", callback_url)
    write_env_var("LOCAL_CALLBACK_URL", local_url)

    print()
    print("=" * 70)
    print("  Summary - step 03b: client (USER_FEDERATION) provider")
    print("=" * 70)
    print("  What was created / updated:")
    print(f"    Provider name        : {provider_name}")
    print(f"    Provider ARN         : {arn}")
    print("    Vendor               : CustomOauth2")
    print("    Client auth          : PRIVATE_KEY_JWT (same KMS key as M2M/OBO)")
    print(f"    Client ID (Web app)  : {login_app_client_id}")
    print(f"    JWK kid header       : {kid}")
    print(f"    AgentCore callback   : {callback_url}")
    if api_token:
        print("    Okta Web app redirect_uris updated to include that callback")
    else:
        print("    Okta Web app redirect_uris update: PENDING MANUAL STEP")
        print("      (paste the callback URL above into the web app -")
        print("       see the instructions block earlier in this run)")
    print(f"    Workload allowed URL : {local_url}")
    print()
    print("  Where to inspect it:")
    print("    AgentCore identity provider (control-plane API):")
    print("      aws bedrock-agentcore-control get-oauth2-credential-provider \\")
    print(f"        --name {provider_name} --region {region}")
    print("    Okta Web app redirect_uris:")
    print("      Applications → Applications → login Web app → General tab")
    print(f"        {base_url}/admin/app/oidc_client/instance/{login_app_id}")
    print()
    print("  Written to .env:")
    print(f"    CLIENT_PROVIDER_NAME={provider_name}")
    print(f"    AGENTCORE_MANAGED_CALLBACK_URL={callback_url}")
    print(f"    LOCAL_CALLBACK_URL={local_url}")
    print()
    print("  Why this step matters:")
    print("    This provider handles the code-exchange call to Okta's /token")
    print("    endpoint after you sign in through the browser. Same KMS key")
    print("    signs the JWT client assertion - same private_key_jwt flow -")
    print("    but the client_id is your Web app (not the service app), and")
    print("    the grant type is authorization_code (not client_credentials).")
    print()
    print("  Next step:")
    print("    python get_okta_user_jwt.py")
    print("    (opens your browser, runs 3LO, writes OKTA_USER_JWT to .env)")


if __name__ == "__main__":
    main()
