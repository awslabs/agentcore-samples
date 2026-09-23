"""
Create the AgentCore Identity credential provider used for user 3LO
sign-in (USER_FEDERATION) against Entra ID, then wire its managed
callback URL onto the Entra login web app's redirectUris list.

This is the "client provider" side of the OBO flow - distinct from the
M2M and OBO providers (setup/03 and setup/04) which authenticate the
service app to Entra's token endpoint. The client provider
authenticates the web app to Entra's token endpoint during the
authorization_code exchange that happens after a user signs in.

All three provider types use PRIVATE_KEY_JWT with the same KMS-signed
certificate - different Entra apps, one KMS key, one cert.

What this script does:
  1. Creates a CustomOauth2 credential provider CLIENT_PROVIDER_NAME
     with:
       - clientAuthenticationMethod = PRIVATE_KEY_JWT
       - clientId                   = ENTRA_LOGIN_CLIENT_ID
       - signingAlgorithm           = RS256
       - additionalHeaderClaims.x5t#S256 = the cert thumbprint
       - No onBehalfOfTokenExchangeConfig - this provider is only for
         the authorization_code exchange.
     Captures the response's `callbackUrl` (AgentCore Identity's managed
     callback URL for this provider).
  2. Updates the Entra web app's redirectUris via Graph PATCH to include
     that callback URL (replacing the placeholder from setup/02b).
     Falls back gracefully to printing manual instructions when `az` is
     not available (see Manual Entra configuration in README.md).
  3. Adds the local callback URL to the workload identity's
     allowedResourceOauth2ReturnUrls - the 3LO flow bounces from
     AgentCore's managed callback to this local URL with a session_id.
  4. Writes CLIENT_PROVIDER_NAME, AGENTCORE_MANAGED_CALLBACK_URL, and
     LOCAL_CALLBACK_URL to .env.

Idempotent: safe to re-run. Existing provider is updated in place;
existing web app redirect URI is not duplicated; existing workload
allowed URL is not appended twice.

Usage:
    python setup/04b_create_provider_client.py
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
DEFAULT_LOCAL_CALLBACK_URL = "http://localhost:8081/callback"
DEFAULT_CLIENT_PROVIDER_NAME = "pkjwt-entra-sample-client"


def _load_helper():
    path = Path(__file__).resolve().parent / "_entra_graph.py"
    spec = importlib.util.spec_from_file_location("_entra_graph", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


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


def discovery_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"


def build_provider_config(
    *,
    tenant_id: str,
    client_id: str,
    kms_key_arn: str,
    x5t_s256: str,
) -> dict:
    """CustomOauth2 provider config for USER_FEDERATION (3LO) with PRIVATE_KEY_JWT.

    No onBehalfOfTokenExchangeConfig - this provider is used only for
    the authorization_code exchange after browser login.
    """
    return {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": discovery_url(tenant_id)},
            "clientId": client_id,
            "clientAuthenticationMethod": "PRIVATE_KEY_JWT",
            "privateKeyJwtConfig": {
                "privateKeySource": {"kmsKeySource": {"kmsKeyArn": kms_key_arn}},
                "signingAlgorithm": "RS256",
                "additionalHeaderClaims": {"x5t#S256": x5t_s256},
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


def update_web_app_redirect_uri(graph, token: str, object_id: str, callback_url: str) -> None:
    """PATCH the login web app's redirectUris to include AgentCore's callback."""
    app = graph.graph_get(
        f"applications/{object_id}",
        token,
        params={"$select": "id,displayName,web"},
    )
    web = app.get("web") or {}
    existing = list(web.get("redirectUris") or [])
    # Drop the placeholder and any other example.com URIs - keep everything
    # real the admin added.
    real = [u for u in existing if "placeholder" not in u and "example.com" not in u]
    if callback_url in real:
        print("• Callback URL already registered on the Entra web app")
        return
    merged = [*real, callback_url]
    print(f"→ PATCH /applications (set redirectUris to {merged})")
    graph.graph_patch(
        f"applications/{object_id}",
        token,
        {"web": {"redirectUris": merged}},
    )
    print("✓ Web app redirect URIs updated")


def _manual_redirect_uri_instructions(login_app_id: str, callback_url: str) -> str:
    return (
        f"  In the Entra admin center:\n"
        f"    App registrations → your login web app → Authentication\n"
        f"    Platform 'Web' → Redirect URIs → Add URI → paste:\n"
        f"        {callback_url}\n"
        f"    → Save.\n"
        f"  Direct URL:\n"
        f"    https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/"
        f"ApplicationMenuBlade/~/Authentication/appId/{login_app_id}"
    )


def update_workload_return_url(control, workload_name: str, local_url: str) -> None:
    """Add the local callback URL to the workload identity's allowed list."""
    try:
        existing = control.get_workload_identity(name=workload_name)
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code in {"ResourceNotFoundException", "NotFoundException"}:
            print(f"⚠ Workload identity {workload_name!r} not found. Run setup/03 or 04 first.")
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


def _try_graph_token(graph) -> str | None:
    """Attempt Graph token acquisition. Return None on any failure.

    We don't want setup/04b to hard-fail on missing az login - the
    AgentCore provider creation is fully automatable, only the Entra
    redirectUris PATCH depends on Graph. If Graph isn't reachable, we
    fall back to printing manual instructions.
    """
    try:
        return graph.get_graph_token()
    except SystemExit:
        return None


def main() -> None:
    load_dotenv(ENV_FILE)
    graph = _load_helper()
    region = must_env("AWS_REGION")

    tenant_id = must_env("ENTRA_TENANT_ID")
    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    x5t_s256 = must_env("X5T_S256_THUMBPRINT")
    workload_name = must_env("WORKLOAD_NAME")
    login_app_object_id = must_env("ENTRA_LOGIN_APP_OBJECT_ID")
    login_client_id = must_env("ENTRA_LOGIN_CLIENT_ID")
    provider_name = os.environ.get("CLIENT_PROVIDER_NAME") or DEFAULT_CLIENT_PROVIDER_NAME
    local_url = os.environ.get("LOCAL_CALLBACK_URL") or DEFAULT_LOCAL_CALLBACK_URL

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Region:         {region}")
    print(f"Tenant:         {tenant_id}")
    print(f"Login app:      {login_client_id}")
    print(f"Workload:       {workload_name}")
    print(f"Provider:       {provider_name}")
    print(f"Local callback: {local_url}")
    print()

    # 1. Create-or-update the client credential provider
    config = build_provider_config(
        tenant_id=tenant_id,
        client_id=login_client_id,
        kms_key_arn=key_arn,
        x5t_s256=x5t_s256,
    )
    print("→ Creating client credential provider (USER_FEDERATION)...")
    arn, callback_url = upsert_provider(ac_control, name=provider_name, config=config)
    if not callback_url:
        print(
            "✗ AgentCore did not return a callbackUrl for the client provider - cannot wire the Entra redirectUri.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"  AgentCore-managed callback URL: {callback_url}")

    # 2. Update the Entra web app's redirectUris via Graph, or fall back
    #    to printing manual instructions if Graph auth is unavailable.
    print()
    print("→ Wiring AgentCore callback URL onto Entra web app...")
    graph_token = _try_graph_token(graph)
    if graph_token:
        update_web_app_redirect_uri(graph, graph_token, login_app_object_id, callback_url)
    else:
        print("→ Manual Entra step required (`az login` not available)")
        print(_manual_redirect_uri_instructions(login_client_id, callback_url))

    # 3. Add the local callback URL to the workload identity's allowed list.
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
    print("  Summary - step 04b: client (USER_FEDERATION) provider")
    print("=" * 70)
    print("  What was created / updated:")
    print(f"    Provider name        : {provider_name}")
    print(f"    Provider ARN         : {arn}")
    print("    Vendor               : CustomOauth2")
    print("    Client auth          : PRIVATE_KEY_JWT (same KMS key as M2M/OBO)")
    print(f"    Client ID (Web app)  : {login_client_id}")
    print(f"    Assertion header     : x5t#S256 = {x5t_s256}")
    print(f"    AgentCore callback   : {callback_url}")
    if graph_token:
        print("    Entra web app redirectUris updated to include the callback")
    else:
        print("    Entra web app redirectUris update: PENDING MANUAL STEP")
        print("      (paste the callback URL above into the web app -")
        print("       see the instructions block earlier in this run)")
    print(f"    Workload allowed URL : {local_url}")
    print()
    print("  Where to inspect it:")
    print("    AgentCore Identity provider (control-plane API):")
    print("      aws bedrock-agentcore-control get-oauth2-credential-provider \\")
    print(f"        --name {provider_name} --region {region}")
    print("    Entra web app redirectUris:")
    print("      App registrations → your login web app → Authentication")
    print("      https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/")
    print(f"      ApplicationMenuBlade/~/Authentication/appId/{login_client_id}")
    print()
    print("  Written to .env:")
    print(f"    CLIENT_PROVIDER_NAME={provider_name}")
    print(f"    AGENTCORE_MANAGED_CALLBACK_URL={callback_url}")
    print(f"    LOCAL_CALLBACK_URL={local_url}")
    print()
    print("  Why this step matters:")
    print("    This provider handles the code-exchange call to Entra's /token")
    print("    endpoint after you sign in through the browser. Same KMS key")
    print("    signs the JWT client assertion - same private_key_jwt flow -")
    print("    but the client_id is your web app (not the service app), and")
    print("    the grant type is authorization_code (not client_credentials).")
    print()
    print("  Next step:")
    print("    python get_entra_user_jwt.py")
    print("    (opens your browser, runs 3LO, writes ENTRA_USER_JWT to .env)")


if __name__ == "__main__":
    main()
