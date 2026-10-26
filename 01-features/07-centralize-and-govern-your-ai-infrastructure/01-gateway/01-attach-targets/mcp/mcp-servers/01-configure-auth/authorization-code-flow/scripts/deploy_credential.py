"""Create the OAuth2 credential provider for the configured MCP server.

Creates the credential provider and outputs the callback URL that must be
registered with the OAuth app or the provider's authorization server.

How the OAuth client is obtained is set by the profile's authModel:

  oauth_app  You created an OAuth app by hand and its id/secret live in env
             vars (github: GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET). This
             script creates the provider from them in one shot.
  dcr        The authorization server only supports Dynamic Client
             Registration (atlassian). This script creates the provider with
             *dummy* credentials so it vends a callbackUrl, then prints the
             remaining manual steps (allowlist the callback domain, curl the
             DCR endpoint, then deploy_credential_update.py to store the real
             id/secret). No real credentials are needed at this point.

The vendor, provider config key, and per-model keys all come from the profile
named by the required flag -- see mcp_config.py.

Usage:
    uv run python scripts/deploy_credential.py --github
    uv run python scripts/deploy_credential.py --atlassian
"""

import boto3
from mcp_config import (
    credential_provider_name,
    get_required_env,
    load_env,
    save_env,
    select_profile,
)

# Placeholders CreateOauth2CredentialProvider accepts so a dcr provider can be
# created before a client exists. deploy_credential_update.py overwrites them
# with the DCR-registered values.
DCR_DUMMY_CLIENT_ID = "dcr-placeholder-client-id"
DCR_DUMMY_CLIENT_SECRET = "dcr-placeholder-client-secret"


def find_provider(client, name):
    """Return an existing OAuth2 credential provider's name, or None.

    CreateOauth2CredentialProvider is not idempotent -- a second run raises
    ConflictException. A paged name scan lets a re-run reuse the provider.
    """
    paginator = client.get_paginator("list_oauth2_credential_providers")
    for page in paginator.paginate():
        for prov in page.get("credentialProviders", []):
            if prov.get("name") == name:
                return name
    return None


def provider_config_oauth_app(profile):
    """{clientId, clientSecret} read from the profile's env vars.

    A named vendor (GithubOauth2) has its authorize/token endpoints baked in and
    carries no discoveryUrl. A CustomOauth2 vendor whose authorization server is
    not one of the named ones (Slack's user-token endpoints, advertised at its
    own .well-known/oauth-authorization-server) needs oauthDiscovery, so include
    it whenever the profile supplies a discoveryUrl.
    """
    config = {
        "clientId": get_required_env(profile["clientIdEnv"]),
        "clientSecret": get_required_env(profile["clientSecretEnv"]),
    }
    if profile.get("discoveryUrl"):
        config["oauthDiscovery"] = {"discoveryUrl": profile["discoveryUrl"]}
    return config


def provider_config_dcr(profile):
    """Dummy {clientId, clientSecret} + oauthDiscovery for the initial create.

    The real values arrive later via DCR; all we need now is a provider that
    vends its callbackUrl. discoveryUrl is the generic (non-tenant) one --
    deploy_credential_update.py switches it to the DCR-tenant URL.
    """
    return {
        "clientId": DCR_DUMMY_CLIENT_ID,
        "clientSecret": DCR_DUMMY_CLIENT_SECRET,
        "oauthDiscovery": {"discoveryUrl": profile["discoveryUrl"]},
    }


def print_oauth_app_next_steps(profile, identity_callback):
    print()
    print("  *** ACTION REQUIRED ***")
    print(f"  Go to {profile['appSettingsUrl']} and update your")
    print(f"  {profile['displayName']} app's authorization callback URL to:")
    print(f"\n    {identity_callback}\n")


def print_dcr_next_steps(profile, identity_callback):
    """The manual DCR hops: allowlist -> register -> update."""
    print()
    print("  *** ACTION REQUIRED (Dynamic Client Registration) ***")
    print()
    print("  1. Allowlist the callback domain in Atlassian:")
    print("       Atlassian org admin -> Security -> Rovo MCP server settings")
    print(f"       add the domain of: {identity_callback}")
    print()
    print("  2. Register a client via the DCR endpoint (RFC 7591):")
    print()
    print(  # lgtm[py/clear-text-logging-sensitive-data]
        "       curl -s -X POST "  # codeql[py/clear-text-logging-sensitive-data]
        f'"{profile["dcrRegisterUrl"]}" \\'
    )
    print('         -H "Content-Type: application/json" \\')
    print("         -d '{")
    print('           "client_name": "AgentCore Gateway",')
    print(f'           "redirect_uris": ["{identity_callback}"],')
    print('           "grant_types": ["authorization_code", "refresh_token"],')
    print('           "response_types": ["code"],')
    print('           "token_endpoint_auth_method": "none"')
    print("         }'")
    print()
    print("  3. Export the returned values and store them on the provider:")
    print(f"       export {profile['clientIdEnv']}=<client_id from the response>")
    print(
        f"       export {profile['clientSecretEnv']}=<client_secret from the response>"
    )
    print(
        f"       uv run python scripts/deploy_credential_update.py --{profile['name']}"
    )
    print()


def main():
    profile = select_profile(__doc__)
    load_env()

    region = boto3.Session().region_name
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    provider_name = credential_provider_name(profile)
    auth_model = profile["authModel"]

    if find_provider(client, provider_name):
        # Reuse: get_oauth2_credential_provider returns the same callbackUrl the
        # create call would have. Stored credentials are left unchanged.
        print(f"--- {profile['displayName']} credential provider exists -- reusing ---")
        response = client.get_oauth2_credential_provider(name=provider_name)
    else:
        print(f"--- Creating {profile['displayName']} OAuth2 credential provider ---")
        if auth_model == "dcr":
            config = provider_config_dcr(profile)
        else:
            config = provider_config_oauth_app(profile)
        response = client.create_oauth2_credential_provider(
            name=provider_name,
            credentialProviderVendor=profile["vendor"],
            oauth2ProviderConfigInput={profile["providerConfigKey"]: config},
        )
    cred_provider_arn = response["credentialProviderArn"]
    identity_callback = response["callbackUrl"]

    print(f"  Provider name:  {provider_name}")
    print(f"  Credential ARN: {cred_provider_arn}")
    print(f"  Callback URL:   {identity_callback}")

    if auth_model == "dcr":
        print_dcr_next_steps(profile, identity_callback)
    else:
        print_oauth_app_next_steps(profile, identity_callback)

    save_env(CRED_PROVIDER_ARN=cred_provider_arn, CALLBACK_URL=identity_callback)
    print("  Saved to .env")


if __name__ == "__main__":
    main()
