"""
Outbound Auth with GitHub OAuth2 3-Legged OAuth (3LO).

Demonstrates how to configure a Strands agent on AgentCore Runtime to access
a user's private GitHub repositories using the GithubOauth2 credential provider
with the USER_FEDERATION auth flow.

Key concepts:
- GithubOauth2 credential provider: pre-configured for GitHub OAuth2 endpoints
- USER_FEDERATION auth flow: requires user consent via authorization URL
- OAuth2 session binding: local oauth2_callback_server.py handles the callback
- Inbound Auth (Cognito): agent requires a valid Cognito JWT token
- Outbound Auth (GitHub): agent acquires GitHub access token on user's behalf

Usage:
    python outbound_auth_github.py

Prerequisites:
    - AWS CLI configured with credentials
    - GitHub OAuth app client ID and secret (see README for setup)
    - pip install -r requirements.txt
    - Set environment variables: GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
"""

import os
import time

import boto3
import dotenv
from boto3.session import Session

# ── Configuration ─────────────────────────────────────────────────────────────

PROVIDER_NAME = "github-provider"

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name or "us-east-1"
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

print(f"Region:  {REGION}")
print(f"Account: {ACCOUNT_ID}")


# ── Step 1: Configure Inbound Auth (Cognito) ────────────────────────────────────


def setup_cognito() -> dict:
    """Create a Cognito User Pool for inbound authentication."""
    print("  Setting up Amazon Cognito user pool...")
    cognito = boto3.client("cognito-idp", region_name=REGION)

    pool_name = f"Cognito_3LO_Github_{int(time.time()) % 10000}"
    pool_resp = cognito.create_user_pool(
        PoolName=pool_name,
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 8,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": False,
            }
        },
    )
    user_pool_id = pool_resp["UserPool"]["Id"]

    client_resp = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=f"{pool_name}-client",
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        GenerateSecret=False,
    )
    client_id = client_resp["UserPoolClient"]["ClientId"]

    cognito.admin_create_user(
        UserPoolId=user_pool_id,
        Username="testuser",
        TemporaryPassword="MyPassword123!",  # pragma: allowlist secret
        UserAttributes=[{"Name": "email", "Value": "testuser@example.com"}],
        MessageAction="SUPPRESS",
    )
    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username="testuser",
        Password="MyPassword123!",  # pragma: allowlist secret
        Permanent=True,
    )

    discovery_url = f"https://cognito-idp.{REGION}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"
    config = {
        "user_pool_id": user_pool_id,
        "client_id": client_id,
        "discovery_url": discovery_url,
        "username": "testuser",
        "password": "MyPassword123!",
    }
    print(f"  Cognito pool: {user_pool_id}, client: {client_id}")
    return config


# ── Step 2: Create GitHub OAuth2 Credential Provider ──────────────────────────


def create_github_credential_provider() -> dict:
    """Create a GithubOauth2 credential provider for GitHub API access.

    The provider is pre-configured for GitHub's OAuth2 endpoints. You only
    need to provide your GitHub OAuth App's client ID and secret.
    """
    dotenv.load_dotenv(override=True)

    client_id = os.environ.get("GITHUB_CLIENT_ID")
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError(
            "Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET environment variables.\n"
            "See README.md for GitHub OAuth App setup instructions."
        )

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    try:
        resp = control.create_oauth2_credential_provider(
            name=PROVIDER_NAME,
            credentialProviderVendor="GithubOauth2",
            oauth2ProviderConfigInput={
                "githubOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                }
            },
        )
        provider_arn = resp["credentialProviderArn"]
        callback_url = resp["callbackUrl"]
        print(f"  Created credential provider: {provider_arn}")
    except control.exceptions.ConflictException:
        resp = control.get_oauth2_credential_provider(name=PROVIDER_NAME)
        provider_arn = resp["credentialProviderArn"]
        callback_url = resp["callbackUrl"]
        print(f"  Reusing existing provider: {provider_arn}")

    print(f"  GitHub OAuth2 callback URL: {callback_url}")
    print(
        "\n  IMPORTANT: Update your GitHub OAuth App's Authorization callback URL:\n"
        "  GitHub → Settings → Developer Settings → OAuth Apps → your app\n"
        "  Authorization callback URL → Update to:\n"
        f"  {callback_url}\n"
    )
    return {"provider_arn": provider_arn, "callback_url": callback_url}


# ── Step 3: Show session binding explanation ───────────────────────────────────


def explain_session_binding():
    """Explain the OAuth2 session binding flow for GitHub."""
    print("  OAuth2 Session Binding for GitHub:")
    print("  1. Agent calls GetResourceOauth2Token (USER_FEDERATION)")
    print("  2. AgentCore Identity returns { authorizationUrl, sessionUri }")
    print("  3. User opens authorizationUrl → GitHub OAuth consent page")
    print("  4. GitHub redirects to oauth2_callback_server on port 9090")
    print("  5. Callback server calls CompleteResourceTokenAuth")
    print("  6. On next invocation, agent gets the GitHub access token")
    print("  7. Agent calls GitHub API: GET /user/repos?type=private")
    print("")
    print("  Session binding code in github_agent.py:")
    print("  @requires_access_token(")
    print("      provider_name='github-provider',")
    print("      auth_flow='USER_FEDERATION',")
    print("      callback_url=CALLBACK_URL")
    print("  )")


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    print("=== Outbound Auth: GitHub OAuth2 3LO ===\n")

    # ── 1. Cognito setup ───────────────────────────────────────────────────────
    print("=== Step 1: Setting up Cognito for Inbound Auth ===")
    cognito_config = setup_cognito()

    # ── 2. GitHub credential provider ─────────────────────────────────────────
    print("\n=== Step 2: Creating GitHub OAuth2 Credential Provider ===")
    provider_info = create_github_credential_provider()

    # ── 3. Session binding explanation ────────────────────────────────────────
    print("\n=== Step 3: OAuth2 Session Binding Flow ===")
    explain_session_binding()

    # ── 4. Running the app ────────────────────────────────────────────────────
    print("\n=== Step 4: Running the Interactive App ===")
    print("  The github_agent.py file is the agent code deployed to AgentCore Runtime.")
    print("  It lists private GitHub repositories using the GithubOauth2 credential provider.")
    print("")
    print("  For interactive testing, run the Streamlit app:")
    print(f"  python oauth2_callback_server.py --region {REGION} &")
    print("  streamlit run chatbot_app_cognito.py")
    print("  Login: testuser / MyPassword123!")
    print("  Try: 'What are my private repositories?'")

    print("\n=== Summary ===")
    print(f"  GitHub credential provider: {PROVIDER_NAME}")
    print(f"  Provider ARN: {provider_info['provider_arn']}")
    print(f"  Callback URL: {provider_info['callback_url']}")
    print(f"  Cognito User Pool: {cognito_config['user_pool_id']}")
    print("\n  Register the callback URL in GitHub OAuth App settings.")


if __name__ == "__main__":
    main()
