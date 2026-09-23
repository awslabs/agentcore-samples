"""
Outbound Auth with Google OAuth2 3-Legged OAuth (3LO) / USER_FEDERATION.

Demonstrates how to configure a Strands agent on AgentCore Runtime to access
Google Calendar on behalf of a user using the 3LO OAuth flow with AgentCore
Identity's GoogleOauth2 credential provider.

Key concepts:
- GoogleOauth2 credential provider: pre-configured for Google Calendar API
- USER_FEDERATION auth flow: requires user consent via authorization URL
- OAuth2 session binding: local oauth2_callback_server.py handles the callback
- Inbound Auth (Cognito): agents require a valid Cognito JWT token
- Outbound Auth (Google): agent acquires Google access token on user's behalf

Usage:
    python outbound_auth_3lo.py

Prerequisites:
    - AWS CLI configured with credentials
    - Google OAuth2 client ID and secret (see README for setup)
    - pip install -r requirements.txt
    - Set environment variables: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
"""

import os
import subprocess
import sys
import time

import boto3
import dotenv
from boto3.session import Session

# ── Configuration ─────────────────────────────────────────────────────────────

PROVIDER_NAME = "google-cal-provider"

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name or "us-east-1"
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

print(f"Region:  {REGION}")
print(f"Account: {ACCOUNT_ID}")


# ── Step 1: Configure Inbound Auth (Cognito) ────────────────────────────────────


def setup_cognito():
    """Create a Cognito User Pool for inbound authentication.

    In this tutorial, both inbound (user JWT) and outbound (Google Calendar)
    auth are configured. The Cognito token authenticates the user to the agent,
    and the Google credential provider gets the access token for Calendar API.
    """
    print("  Setting up Amazon Cognito user pool for inbound auth...")

    cognito = boto3.client("cognito-idp", region_name=REGION)

    pool_name = f"Cognito_3LO_Google_{int(time.time()) % 10000}"
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

    # Create test user
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


def reauthenticate_user(client_id: str) -> str:
    """Get a fresh Cognito access token for the test user."""
    cognito = boto3.client("cognito-idp", region_name=REGION)
    auth_resp = cognito.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": "testuser", "PASSWORD": "MyPassword123!"},  # pragma: allowlist secret
        ClientId=client_id,
    )
    return auth_resp["AuthenticationResult"]["AccessToken"]


# ── Step 2: Create Google OAuth2 Credential Provider ──────────────────────────


def create_google_credential_provider() -> dict:
    """Create a GoogleOauth2 credential provider for Google Calendar access.

    The provider is pre-configured for Google's OAuth2 endpoints. You only
    need to provide your app's client ID and secret from the Google Developer Console.
    """
    dotenv.load_dotenv(override=True)

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError(
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables.\n"
            "See README.md for Google Developer Console setup instructions."
        )

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    try:
        resp = control.create_oauth2_credential_provider(
            name=PROVIDER_NAME,
            credentialProviderVendor="GoogleOauth2",
            oauth2ProviderConfigInput={
                "googleOauth2ProviderConfig": {
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

    print(f"  Google OAuth2 callback URL: {callback_url}")
    print(
        "\n  IMPORTANT: Register this callback URL in the Google Developer Console:\n"
        "  APIs & Services → Credentials → OAuth 2.0 Client → Authorized redirect URIs\n"
        f"  Add: {callback_url}\n"
    )
    return {"provider_arn": provider_arn, "callback_url": callback_url}


# ── Step 3: Update workload identity with local callback URL ───────────────────


def update_workload_identity(agent_runtime_id: str):
    """Register the local OAuth2 callback server URL with the workload identity.

    This enables OAuth2 session binding — AgentCore Identity only redirects
    OAuth callbacks to pre-registered URLs (security requirement).
    """
    from oauth2_callback_server import get_oauth2_callback_url

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    workload_identity = control.get_workload_identity(name=agent_runtime_id)
    existing_urls = workload_identity.get("allowedResourceOauth2ReturnUrls") or []
    callback_url = get_oauth2_callback_url()

    print(
        f"  Updating workload {agent_runtime_id} with callback URL: {callback_url}"
    )  # codeql[py/clear-text-logging-sensitive-data]
    control.update_workload_identity(
        name=agent_runtime_id,
        allowedResourceOauth2ReturnUrls=[*existing_urls, callback_url],
    )
    print("  Workload identity updated ✓")


# ── Step 4: Invoke agent with 3LO OAuth flow ───────────────────────────────────


def invoke_agent_with_google_calendar(runtime, bearer_token: str):
    """Invoke the deployed agent to access Google Calendar.

    The first invocation triggers the 3LO OAuth flow:
    1. Agent calls GetResourceOauth2Token (USER_FEDERATION)
    2. AgentCore Identity returns an authorization URL
    3. The authorization URL is returned to the user
    4. User opens URL, grants consent
    5. Google redirects to the callback server
    6. Callback server calls CompleteResourceTokenAuth
    7. On next invocation, AgentCore Identity vends the access token
    """
    from oauth2_callback_server import (
        store_token_in_oauth2_callback_server,
        wait_for_oauth2_server_to_be_ready,
    )

    oauth2_callback_server_process = subprocess.Popen([sys.executable, "oauth2_callback_server.py", "--region", REGION])

    try:
        if wait_for_oauth2_server_to_be_ready():
            store_token_in_oauth2_callback_server(bearer_token)
            print("  Invoking agent (this will trigger the Google OAuth2 flow)...")
            invoke_response = runtime.invoke(
                {"prompt": "What is in my agenda for today? Highlight the main events!"},
                bearer_token=bearer_token,
            )
            print(f"  Response: {invoke_response}")
        else:
            print("  Failed to start OAuth2 callback server.")
    finally:
        oauth2_callback_server_process.terminate()


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    print("=== Outbound Auth: Google OAuth2 3LO (Google Calendar) ===\n")

    # ── 1. Cognito setup ───────────────────────────────────────────────────────
    print("=== Step 1: Setting up Cognito for Inbound Auth ===")
    cognito_config = setup_cognito()

    # ── 2. Google credential provider ─────────────────────────────────────────
    print("\n=== Step 2: Creating Google OAuth2 Credential Provider ===")
    provider_info = create_google_credential_provider()

    # ── 3. Key info about the agent code ──────────────────────────────────────
    print("\n=== Step 3: Agent Code (strands_claude_google_3lo.py) ===")
    print("  The agent code is in strands_claude_google_3lo.py.")
    print("  Key pattern: @requires_access_token(provider_name='google-cal-provider',")
    print("                                       scopes=['https://www.googleapis.com/auth/calendar.readonly'],")
    print("                                       auth_flow='USER_FEDERATION',")
    print("                                       callback_url=CALLBACK_URL)")
    print("  This triggers the 3LO OAuth flow when the tool is first called.")

    # ── 4. OAuth2 session binding explanation ─────────────────────────────────
    print("\n=== Step 4: OAuth2 Session Binding ===")
    print("  The oauth2_callback_server.py handles the OAuth2 callback.")
    print("  Run: python oauth2_callback_server.py --region", REGION)
    print("  Then invoke the agent and follow the authorization URL.")
    print("")
    print("  Session binding flow:")
    print("  1. Agent calls GetResourceOauth2Token → gets auth URL")
    print("  2. User opens auth URL, grants consent to Google Calendar")
    print("  3. Google redirects to oauth2_callback_server on port 9090")
    print("  4. Callback server calls CompleteResourceTokenAuth")
    print("  5. Agent retrieves access token and calls Calendar API")

    # ── 5. Streamlit app ───────────────────────────────────────────────────────
    print("\n=== Step 5: Running the Streamlit Chat App ===")
    print("  For an interactive experience, run:")
    print(f"  python oauth2_callback_server.py --region {REGION} & streamlit run chatbot_app_cognito.py")
    print("  Login: testuser / MyPassword123!")
    print("  Try: 'What is in my agenda for today?'")

    print("\n=== Summary ===")
    print(f"  Google credential provider: {PROVIDER_NAME}")
    print(f"  Provider ARN: {provider_info['provider_arn']}")
    print(f"  Callback URL: {provider_info['callback_url']}")
    print(f"  Cognito User Pool: {cognito_config['user_pool_id']}")
    print("\n  Register the callback URL in Google Developer Console to enable the OAuth flow.")


if __name__ == "__main__":
    main()
