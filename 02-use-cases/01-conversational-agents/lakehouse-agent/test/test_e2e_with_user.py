#!/usr/bin/env python3
"""
End-to-End Test with User Authentication for RLS

Invokes the agent runtime with a real user JWT (not client_credentials) so
row-level security is exercised under a user identity. Works on both IdPs
(IDP_PROVIDER = "cognito" | "okta").

Token sources, in precedence order:
1. --token <jwt> — a token you already hold. Works on BOTH IdPs.
2. Cognito only — the Cognito ROPC grant against the SSM-stored test user
   (`test-user-3` / `test-password`), or --username/--password to override.

Okta Identity Engine tenants block the Resource Owner Password Credentials
(ROPC) grant, so the credential path cannot mint a token on the Okta path.
There, sign in through the Streamlit UI (notebook 08, Authorization Code +
PKCE), copy the access token, and paste it with --token.

Usage:
    # Both IdPs — paste a token you already hold
    python test_e2e_with_user.py --token <jwt>

    # Cognito only — use the SSM-stored test user
    python test_e2e_with_user.py

    # Cognito only — override the test-user credentials
    python test_e2e_with_user.py --username <username> --password <password>
"""

import argparse
import base64
import json
import sys
import urllib.parse
from pathlib import Path

import requests

# Make the repo root importable (utils/ lives there) when this test runs from
# its own test/ subdir.
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.aws_session_utils import get_aws_session
from utils.idp_config import get_idp_provider

# Fail-fast guidance shown when a reader tries the Cognito credential path on Okta.
OKTA_ROPC_HELP = (
    "❌ The username/password path is Cognito-only, but IDP_PROVIDER='okta'.\n"
    "   Okta Identity Engine tenants block the Resource Owner Password Credentials (ROPC)\n"
    "   grant, so a username + password cannot mint a token here.\n"
    "   ➜ Sign in through the Streamlit UI (notebook 08, Authorization Code + PKCE), copy\n"
    "     the access token, then re-run:\n"
    "         python test_e2e_with_user.py --token <jwt>"
)


def decode_jwt(token):
    """Decode a JWT payload without signature verification (inspection only)."""
    parts = token.split(".")
    if len(parts) == 3:
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    return {}


def get_cognito_user_token(ssm, username=None, password=None):
    """
    Mint a user token from Cognito via the ROPC grant (Cognito path only).

    Args:
        ssm: A boto3 SSM client.
        username: Optional username override; defaults to SSM `test-user-3`.
        password: Optional password override; defaults to SSM `test-password`.

    Returns:
        Tuple of (bearer_token, user_label) — (None, None) on failure.
    """
    cognito_domain = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-domain")["Parameter"]["Value"]
    client_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-id")["Parameter"]["Value"]
    client_secret = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-secret", WithDecryption=True)[
        "Parameter"
    ]["Value"]

    # Get test user credentials (unless the caller overrode them)
    test_user = username or ssm.get_parameter(Name="/app/lakehouse-agent/test-user-3")["Parameter"]["Value"]
    test_password = (
        password
        or ssm.get_parameter(Name="/app/lakehouse-agent/test-password", WithDecryption=True)["Parameter"]["Value"]
    )

    print(f"✅ Test User: {test_user}")
    print()

    # Get user token using Resource Owner Password Credentials flow
    print("🔑 Getting user token (ROPC flow)...")

    token_url = f"{cognito_domain}/oauth2/token"

    try:
        response = requests.post(
            token_url,
            auth=(client_id, client_secret),
            data={
                "grant_type": "password",
                "username": test_user,
                "password": test_password,
                "scope": "lakehouse-api/claims.query openid email profile",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code == 200:
            token_data = response.json()
            access_token = token_data["access_token"]
            id_token = token_data.get("id_token")

            print("✅ Access token obtained")
            if id_token:
                print("✅ ID token obtained")

            access_claims = decode_jwt(access_token)
            print("\n🔍 Access Token Claims:")
            print(f"   Username: {access_claims.get('username', 'N/A')}")
            print(f"   Email: {access_claims.get('email', 'N/A')}")
            print(f"   Scope: {access_claims.get('scope', 'N/A')}")

            if id_token:
                id_claims = decode_jwt(id_token)
                print("\n🔍 ID Token Claims:")
                print(f"   Email: {id_claims.get('email', 'N/A')}")
                print(f"   Email Verified: {id_claims.get('email_verified', 'N/A')}")

            # Use ID token if available (contains more user info), otherwise access token
            return (id_token if id_token else access_token), test_user

        print(f"❌ Failed to get token: HTTP {response.status_code}")
        print(f"   Response: {response.text}")

        # Try client_credentials as fallback
        print("\n⚠️  Falling back to client_credentials flow...")
        response = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "lakehouse-api/claims.query",
            },
        )

        if response.status_code == 200:
            print("✅ Got client_credentials token (no user identity for RLS)")
            return response.json()["access_token"], test_user

        print(f"❌ Failed: {response.text}")
        return None, None

    except Exception as e:
        print(f"❌ Error getting token: {e}")
        import traceback

        traceback.print_exc()
        return None, None


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end agent runtime test under a user identity (Cognito or Okta)",
        epilog=(
            "--token works on BOTH IdPs. The username/password path (explicit flags, or the "
            "SSM-stored test user when no flags are given) is Cognito-only: Okta Identity "
            "Engine tenants block the ROPC grant, so use --token there with a token from the "
            "Streamlit UI login (notebook 08)."
        ),
    )
    parser.add_argument(
        "--token",
        help="User JWT to invoke the agent with (works on both IdPs; paste a token you already hold)",
    )
    parser.add_argument(
        "--username",
        help="Cognito username (Cognito only; overrides the SSM test-user-3 value)",
    )
    parser.add_argument(
        "--password",
        help="Cognito user password (Cognito only; overrides the SSM test-password value)",
    )
    args = parser.parse_args()

    # --token and --username/--password are mutually exclusive token sources.
    if args.token and (args.username or args.password):
        parser.error("--token cannot be combined with --username/--password — supply one token source, not both.")
    if not args.token and bool(args.username) != bool(args.password):
        parser.error("--username and --password must be supplied together (or omit both to use the SSM test user).")

    session, region, account_id = get_aws_session()
    ssm = session.client("ssm", region_name=region)

    print("=" * 70)
    print("E2E TEST WITH USER AUTHENTICATION")
    print("=" * 70)
    print()

    idp_provider = get_idp_provider(ssm)
    print(f"📍 IDP_PROVIDER: {idp_provider}")
    print()

    # Get configuration
    print("Loading configuration from SSM...")

    runtime_arn = ssm.get_parameter(Name="/app/lakehouse-agent/agent-runtime-id")["Parameter"]["Value"]
    print(f"✅ Runtime: {runtime_arn}")

    # Resolve the user token
    if args.token:
        bearer_token = args.token
        claims = decode_jwt(bearer_token)
        if not claims:
            print("❌ --token does not look like a JWT (expected three '.'-separated parts)")
            return False
        test_user = claims.get("sub") or claims.get("username") or claims.get("email") or "token-user"
        print(f"✅ Test User: {test_user} (from --token claims)")
        print()
        print("🔑 Using pasted user token (no IdP call made)")
    else:
        if idp_provider != "cognito":
            print()
            print(OKTA_ROPC_HELP)
            return False

        bearer_token, test_user = get_cognito_user_token(ssm, args.username, args.password)
        if not bearer_token:
            return False

    # Invoke agent
    print("\n🤖 Invoking agent with user token...")

    encoded_arn = urllib.parse.quote(
        f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{runtime_arn}",
        safe="",
    )
    runtime_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations"

    try:
        response = requests.post(
            runtime_url,
            json={
                "input": "Show me all my claims",
                "sessionId": f"test-session-{test_user.replace('@', '-at-')}",
            },
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )

        if response.status_code == 200:
            result = response.json()

            print("✅ Agent response received")
            print("\nResponse:")
            print(f"  Content: {result.get('content', 'N/A')[:200]}...")
            print(f"  Tool Calls: {result.get('tool_calls', 0)}")

            if result.get("tool_calls", 0) > 0:
                print("\n✅✅✅ SUCCESS: Tools were invoked!")
                print(f"\nWith user identity: {test_user}")
                print("RLS should be applied based on this user")
            else:
                print("\n❌ FAIL: No tools invoked")
                print("   Check MCP server logs for errors")

            return result.get("tool_calls", 0) > 0

        else:
            print(f"❌ Agent invocation failed: HTTP {response.status_code}")
            print(f"   Response: {response.text}")
            return False

    except Exception as e:
        print(f"❌ Error invoking agent: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
