"""
Self-Hosted Agent with AgentCore Identity OAuth Token Management.

Demonstrates how to build a self-hosted Python agent (no AgentCore Runtime)
that uses Amazon Bedrock AgentCore Identity to manage OAuth 2.0 token flows.
Instead of building OAuth authorization flows, token storage, refresh logic,
and secret management yourself, AgentCore Identity handles all of that.

Key concepts:
- CustomOauth2 credential provider: connects to any OAuth2-compatible server
- Workload identity: represents your agent to AgentCore Identity
- Session binding: local callback server (agent.py) handles the OAuth redirect
- CompleteResourceTokenAuth: binds the OAuth session to the user
- No AgentCore Runtime required — pure boto3

Usage:
    python self_hosted_agent_oauth.py

Prerequisites:
    - AWS CLI configured with credentials
    - pip install -r requirements.txt
    - jq installed (for create_cognito.sh)
    - Set environment variables after running create_cognito.sh:
      USER_POOL_ID, CLIENT_ID, CLIENT_SECRET, ISSUER_URL, COGNITO_USERNAME, COGNITO_PASSWORD
"""

import os
import random
import string
import subprocess

import boto3
from boto3.session import Session

# ── Configuration ─────────────────────────────────────────────────────────────

CREDENTIAL_PROVIDER_NAME = "AgentCoreIdentityStandaloneProvider"
WORKLOAD_NAME = "standalone-agent-identity"
CALLBACK_URL = "http://127.0.0.1:8080/callback"

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name or "us-east-1"
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

print(f"Region:  {REGION}")
print(f"Account: {ACCOUNT_ID}")


# ── Step 1: Create Cognito User Pool (or bring your own OAuth server) ──────────


def create_cognito_user_pool() -> dict:
    """Create an Amazon Cognito user pool as the OAuth authorization server.

    Alternatively, run:  bash create_cognito.sh
    and copy the output values into the environment variables below.

    If you already have an OAuth server, skip this step and set:
    - USER_POOL_ID, CLIENT_ID, CLIENT_SECRET, ISSUER_URL
    - COGNITO_USERNAME, COGNITO_PASSWORD (test user credentials)
    """
    print("  Creating Cognito user pool via create_cognito.sh...")
    result = subprocess.run(
        ["bash", "create_cognito.sh"],
        capture_output=True,
        text=True,
        env={**os.environ, "AWS_REGION": REGION},
    )
    if result.returncode != 0:
        print(f"  Error: {result.stderr}")
        raise RuntimeError("create_cognito.sh failed")
    print(result.stdout)
    print("  Cognito user pool created. Copy the values above and set as env vars.")
    return {}


# ── Step 2: Create Credential Provider ────────────────────────────────────────


def create_credential_provider() -> dict:
    """Create a CustomOauth2 credential provider for the Cognito user pool.

    A credential provider tells AgentCore Identity how to interact with your
    OAuth authorization server. Once created, the provider handles:
    - Generating authorization URLs
    - Exchanging authorization codes for tokens
    - Refreshing expired tokens
    """
    issuer_url = os.environ.get("ISSUER_URL")
    client_id = os.environ.get("CLIENT_ID")
    client_secret = os.environ.get("CLIENT_SECRET")

    if not all([issuer_url, client_id, client_secret]):
        raise ValueError(
            "Set USER_POOL_ID, CLIENT_ID, CLIENT_SECRET, ISSUER_URL environment variables.\n"
            "Run 'bash create_cognito.sh' first and copy the output values."
        )

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    global CREDENTIAL_PROVIDER_NAME

    try:
        resp = control.create_oauth2_credential_provider(
            name=CREDENTIAL_PROVIDER_NAME,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "oauthDiscovery": {
                        "discoveryUrl": issuer_url,
                    },
                    "clientId": client_id,
                    "clientSecret": client_secret,
                }
            },
        )
        provider_arn = resp["credentialProviderArn"]
        callback_url = resp["callbackUrl"]
        print(f"  Created credential provider: {CREDENTIAL_PROVIDER_NAME}")
    except control.exceptions.ConflictException:
        # Name already taken — use a unique suffix
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
        CREDENTIAL_PROVIDER_NAME = f"{CREDENTIAL_PROVIDER_NAME}-{suffix}"
        print(f"  Name taken, using: {CREDENTIAL_PROVIDER_NAME}")
        resp = control.create_oauth2_credential_provider(
            name=CREDENTIAL_PROVIDER_NAME,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "oauthDiscovery": {
                        "discoveryUrl": issuer_url,
                    },
                    "clientId": client_id,
                    "clientSecret": client_secret,
                }
            },
        )
        provider_arn = resp["credentialProviderArn"]
        callback_url = resp["callbackUrl"]

    print(f"  Provider ARN: {provider_arn}")
    print(f"  OAuth2 callback URL: {callback_url}")
    return {"provider_arn": provider_arn, "callback_url": callback_url}


def update_cognito_callback_url(callback_url: str):
    """Add the credential provider's callback URL to the Cognito user pool client."""
    user_pool_id = os.environ.get("USER_POOL_ID")
    client_id = os.environ.get("CLIENT_ID")

    if not user_pool_id or not client_id:
        print("  Skipping Cognito update (USER_POOL_ID not set)")
        return

    cognito = boto3.client("cognito-idp", region_name=REGION)
    cognito.update_user_pool_client(
        UserPoolId=user_pool_id,
        ClientId=client_id,
        CallbackURLs=[
            f"https://bedrock-agentcore.{REGION}.amazonaws.com/identities/oauth2/callback",
            callback_url,
        ],
        AllowedOAuthFlows=["code"],
        AllowedOAuthScopes=["openid", "profile", "email"],
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
    )
    print("  Cognito user pool client updated with callback URL ✓")


# ── Step 3: Create Workload Identity ──────────────────────────────────────────


def create_workload_identity():
    """Create a workload identity that represents this agent.

    The workload identity:
    - Tells AgentCore Identity who is requesting tokens
    - Restricts which callback URLs OAuth sessions can redirect to (security)
    - Issues workload tokens for GetResourceOauth2Token calls
    """
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    global WORKLOAD_NAME

    try:
        control.create_workload_identity(name=WORKLOAD_NAME)
        print(f"  Created workload identity: {WORKLOAD_NAME}")
    except control.exceptions.ConflictException:
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
        WORKLOAD_NAME = f"{WORKLOAD_NAME}-{suffix}"
        print(f"  Name taken, using: {WORKLOAD_NAME}")
        control.create_workload_identity(name=WORKLOAD_NAME)
        print(f"  Created workload identity: {WORKLOAD_NAME}")

    control.update_workload_identity(
        name=WORKLOAD_NAME,
        allowedResourceOauth2ReturnUrls=[CALLBACK_URL],
    )
    print(f"  Workload identity updated with callback URL: {CALLBACK_URL} ✓")


# ── Step 4: Run the Agent ──────────────────────────────────────────────────────


def run_agent():
    """Run the self-hosted agent (agent.py).

    The agent:
    1. Requests a workload access token from AgentCore Identity
    2. Calls GetResourceOauth2Token → receives an authorization URL
    3. Opens the URL in the browser (or prints it if browser unavailable)
    4. Waits for the user to grant consent
    5. The local HTTP server (port 8080) receives the OAuth callback
    6. The server calls CompleteResourceTokenAuth to bind the session
    7. Agent retrieves the access token and decodes it

    Run this from the terminal for interactive browser-based auth.
    """
    agent_user_id = os.environ.get("AGENT_USER_ID", "quickstart-user")

    env = {  # noqa: F841
        **os.environ,
        "CREDENTIAL_PROVIDER_NAME": CREDENTIAL_PROVIDER_NAME,
        "AWS_REGION": REGION,
        "AGENT_USER_ID": agent_user_id,
    }

    print(f"\n  Running agent with workload: {WORKLOAD_NAME}")
    print(f"  Credential provider: {CREDENTIAL_PROVIDER_NAME}")
    print(f"  User ID: {agent_user_id}")
    print("")
    print("  Command:")
    print(f"  CREDENTIAL_PROVIDER_NAME={CREDENTIAL_PROVIDER_NAME} \\")
    print(f"  WORKLOAD_NAME={WORKLOAD_NAME} \\")
    print(f"  AWS_REGION={REGION} \\")
    print("  python3 agent.py")


# ── Step 5: Cleanup ────────────────────────────────────────────────────────────


def cleanup():
    """Delete workload identity and credential provider."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    cognito = boto3.client("cognito-idp", region_name=REGION)

    try:
        control.delete_workload_identity(name=WORKLOAD_NAME)
        print(f"  Deleted workload identity: {WORKLOAD_NAME} ✓")
    except Exception as e:
        print(f"  Workload delete error: {e}")

    try:
        control.delete_oauth2_credential_provider(name=CREDENTIAL_PROVIDER_NAME)
        print(f"  Deleted credential provider: {CREDENTIAL_PROVIDER_NAME} ✓")
    except Exception as e:
        print(f"  Credential provider delete error: {e}")

    user_pool_id = os.environ.get("USER_POOL_ID")
    if user_pool_id:
        try:
            pool_info = cognito.describe_user_pool(UserPoolId=user_pool_id)
            domain = pool_info["UserPool"].get("Domain", "")
            if domain:
                cognito.delete_user_pool_domain(UserPoolId=user_pool_id, Domain=domain)
            cognito.delete_user_pool(UserPoolId=user_pool_id)
            print(f"  Deleted Cognito pool: {user_pool_id} ✓")
        except Exception as e:
            print(f"  Cognito delete error: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Self-hosted agent with AgentCore Identity OAuth")
    parser.add_argument(
        "--create-cognito",
        action="store_true",
        help="Create a new Cognito user pool (runs create_cognito.sh)",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete workload identity and credential provider",
    )
    args = parser.parse_args()

    if args.cleanup:
        print("\n=== Cleaning up ===")
        cleanup()
        return

    print("=== Self-Hosted Agent with AgentCore Identity OAuth ===\n")

    # ── 1. (Optional) Create Cognito ──────────────────────────────────────────
    if args.create_cognito:
        print("=== Step 1: Creating Cognito User Pool ===")
        create_cognito_user_pool()
        print("\nSet these environment variables before proceeding:")
        print("  export USER_POOL_ID=...")
        print("  export CLIENT_ID=...")
        print("  export CLIENT_SECRET=...")
        print("  export ISSUER_URL=...")
        print("  export COGNITO_USERNAME=...")
        print("  export COGNITO_PASSWORD=...")
        return

    # ── 2. Create credential provider ────────────────────────────────────────
    print("=== Step 2: Creating CustomOauth2 Credential Provider ===")
    provider_info = create_credential_provider()

    # ── 3. Update Cognito callback URL ────────────────────────────────────────
    print("\n=== Step 3: Updating Cognito Callback URL ===")
    update_cognito_callback_url(provider_info["callback_url"])

    # ── 4. Create workload identity ───────────────────────────────────────────
    print("\n=== Step 4: Creating Workload Identity ===")
    create_workload_identity()

    # ── 5. Run instructions ───────────────────────────────────────────────────
    print("\n=== Step 5: Running the Agent ===")
    run_agent()

    print("\n=== Summary ===")
    print(f"  Credential provider: {CREDENTIAL_PROVIDER_NAME}")
    print(f"  Workload identity: {WORKLOAD_NAME}")
    print(f"  Callback URL: {CALLBACK_URL}")
    print("")
    print("  To run the agent:")
    print(f"  CREDENTIAL_PROVIDER_NAME={CREDENTIAL_PROVIDER_NAME} WORKLOAD_NAME={WORKLOAD_NAME} python3 agent.py")
    print("")
    print("  To clean up:")
    print("  python self_hosted_agent_oauth.py --cleanup")


if __name__ == "__main__":
    main()
