"""
Setup script: Creates AgentCore Identity credential providers for:

  1. M2M  — OAuth2 client credentials (Cognito machine client from cognito_config.json)
  2. GitHub 3LO — Authorization code grant for GitHub repository access
  3. Google 3LO — Authorization code grant for Google Calendar access

Run setup_cognito.py first — M2M credentials are read from cognito_config.json.

Usage:
    python setup_oauth_providers.py

Prerequisites:
    - cognito_config.json (from setup_cognito.py)
    - .env file (or environment variables) with:
        GITHUB_CLIENT_ID        GitHub OAuth App client ID
        GITHUB_CLIENT_SECRET    GitHub OAuth App client secret
        GOOGLE_CLIENT_ID        Google OAuth2 client ID
        GOOGLE_CLIENT_SECRET    Google OAuth2 client secret

Outputs:
    oauth_config.json   Provider names and AgentCore callback URLs
"""

import os
import json
from boto3.session import Session

try:
    from dotenv import load_dotenv

    load_dotenv(override=True)
except ImportError:
    pass

try:
    from bedrock_agentcore.services.identity import IdentityClient
except ImportError:
    raise SystemExit("bedrock-agentcore package not found.\nInstall it with: pip install -r requirements.txt")


def create_m2m_provider(identity_client: IdentityClient) -> dict:
    """Create M2M provider using Cognito machine client from cognito_config.json."""
    try:
        with open("cognito_config.json") as f:
            cognito_config = json.load(f)
    except FileNotFoundError:
        print("  Skipping M2M: cognito_config.json not found. Run setup_cognito.py first.")
        return {"name": "M2MProvider", "skipped": True}

    client_id = cognito_config.get("m2m_client_id", "")
    client_secret = cognito_config.get("m2m_client_secret", "")
    token_endpoint = cognito_config.get("m2m_token_endpoint", "")
    region = cognito_config.get("region", "")
    pool_id = cognito_config.get("pool_id", "")

    if not all([client_id, client_secret, token_endpoint]):
        print("  Skipping M2M: m2m fields missing from cognito_config.json.")
        print("  Re-run setup_cognito.py to regenerate.")
        return {"name": "M2MProvider", "skipped": True}

    print("Creating M2M (client credentials) credential provider...")
    provider = identity_client.create_oauth2_credential_provider(
        {
            "name": "M2MProvider",
            "credentialProviderVendor": "CustomOauth2",
            "oauth2ProviderConfigInput": {
                "customOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "oauthDiscovery": {
                        "discoveryUrl": f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration",
                    },
                }
            },
        }
    )
    print(f"  Created: {provider.get('name')}")
    return {"name": "M2MProvider", "provider": provider}


def create_github_3lo_provider(identity_client: IdentityClient) -> dict:
    """Create GitHub OAuth2 3LO credential provider."""
    client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "")

    if not all([client_id, client_secret]):
        print("  Skipping GitHub 3LO (GITHUB_CLIENT_ID/SECRET not set).")
        return {"name": "GitHub3LOProvider", "skipped": True}

    print("Creating GitHub OAuth2 (authorization code / 3LO) credential provider...")
    try:
        provider = identity_client.create_oauth2_credential_provider(
            {
                "name": "GitHub3LOProvider",
                "credentialProviderVendor": "GithubOauth2",
                "oauth2ProviderConfigInput": {
                    "githubOauth2ProviderConfig": {
                        "clientId": client_id,
                        "clientSecret": client_secret,
                    }
                },
            }
        )
        callback_url = provider.get("callbackUrl", "")
        print(f"  Created: {provider.get('name')}")
    except Exception as e:
        if "already exists" in str(e).lower():
            print("  GitHub3LOProvider already exists.")
            existing = identity_client.cp_client.get_oauth2_credential_provider(name="GitHub3LOProvider")
            callback_url = existing.get("callbackUrl", "")
        else:
            raise
    print("\n  IMPORTANT: Add this callback URL to your GitHub OAuth App:")
    print(f"  {callback_url}")
    print("  (GitHub -> Settings -> Developer settings -> OAuth Apps -> your app -> Authorization callback URL)")
    return {"name": "GitHub3LOProvider", "callback_url": callback_url}


def create_google_3lo_provider(identity_client: IdentityClient) -> dict:
    """Create Google OAuth2 3LO credential provider."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    if not all([client_id, client_secret]):
        print("  Skipping Google 3LO (GOOGLE_CLIENT_ID/SECRET not set).")
        return {"name": "Google3LOProvider", "skipped": True}

    print("Creating Google OAuth2 (authorization code / 3LO) credential provider...")
    try:
        provider = identity_client.create_oauth2_credential_provider(
            {
                "name": "Google3LOProvider",
                "credentialProviderVendor": "GoogleOauth2",
                "oauth2ProviderConfigInput": {
                    "googleOauth2ProviderConfig": {
                        "clientId": client_id,
                        "clientSecret": client_secret,
                    }
                },
            }
        )
        callback_url = provider.get("callbackUrl", "")
        print(f"  Created: {provider.get('name')}")
    except Exception as e:
        if "already exists" in str(e).lower():
            print("  Google3LOProvider already exists.")
            existing = identity_client.cp_client.get_oauth2_credential_provider(name="Google3LOProvider")
            callback_url = existing.get("callbackUrl", "")
        else:
            raise
    print("\n  IMPORTANT: Add this callback URL to your Google OAuth App:")
    print(f"  {callback_url}")
    print(
        "  (Google Cloud Console -> APIs & Services -> Credentials -> OAuth 2.0 Client IDs -> Authorised redirect URIs)"
    )
    return {"name": "Google3LOProvider", "callback_url": callback_url}


def main():
    session = Session()
    identity_client = IdentityClient(region=session.region_name)

    results = {}

    print("=== M2M Credential Provider (Cognito client credentials) ===")
    results["m2m"] = create_m2m_provider(identity_client)

    print("\n=== GitHub 3LO Credential Provider ===")
    results["github_3lo"] = create_github_3lo_provider(identity_client)

    print("\n=== Google 3LO Credential Provider ===")
    results["google_3lo"] = create_google_3lo_provider(identity_client)

    with open("oauth_config.json", "w") as f:
        json.dump(
            {
                "m2m_provider_name": results["m2m"]["name"],
                "github_3lo_provider_name": results["github_3lo"]["name"],
                "github_callback_url": results["github_3lo"].get("callback_url", ""),
                "google_3lo_provider_name": results["google_3lo"]["name"],
                "google_callback_url": results["google_3lo"].get("callback_url", ""),
            },
            f,
            indent=2,
        )

    print("\nOAuth provider configuration saved to oauth_config.json")

    github_url = results["github_3lo"].get("callback_url")
    google_url = results["google_3lo"].get("callback_url")
    if github_url or google_url:
        print("\n=== Action Required: Register Callback URLs ===")
        if github_url:
            print("GitHub OAuth App -> Authorization callback URL:")
            print(f"  {github_url}")
        if google_url:
            print("Google Cloud Console -> Authorised redirect URIs:")
            print(f"  {google_url}")


if __name__ == "__main__":
    main()
