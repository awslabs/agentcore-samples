#!/usr/bin/python
from urllib.parse import urlencode
import argparse
import json
import logging
import requests
import sys
import uuid

from utils import (
    generate_pkce_pair,
    get_auth_code_automatically,
    get_ssm_parameter,
    invoke_endpoint,
    load_access_token,
    save_access_token,
)

# Set up detailed logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main():
    """CLI tool for interactive chat with Bedrock AgentCore."""

    parser = argparse.ArgumentParser(
        description="Interactive Agent Runtime CLI Tool - Start a conversation with the customer support agent"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Set logging level based on arguments
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.WARNING)

    print("🚀 Agent Runtime CLI Tool")
    print("=" * 30)

    # Get Agent ARN from SSM Parameter Store
    agent_arn = get_ssm_parameter("/app/customersupportvpc/agentcore/agent_runtime_arn")
    print(f"🤖 Agent ARN: {agent_arn}")

    # Extract runtime_id, region, and account_id from ARN
    # ARN format: arn:aws:bedrock-agentcore:region:account-id:runtime/runtime-id
    runtime_id = agent_arn.split("/")[-1]
    arn_parts = agent_arn.split(":")
    region = arn_parts[3]
    account_id = arn_parts[4]

    print(f"📋 AWS Account ID: {account_id}")
    print(f"🌍 AWS Region: {region}")
    print(f"🤖 Agent Runtime ID: {runtime_id}")

    # Try to load existing access token
    access_token = load_access_token(runtime_id)

    if access_token:
        print("✅ Using cached access token.")
    else:
        print("🔐 No cached token found. Starting authentication flow...")

        code_verifier, code_challenge = generate_pkce_pair()
        state = str(uuid.uuid4())

        client_id = get_ssm_parameter("/app/customersupportvpc/agentcore/web_client_id")
        cognito_domain = get_ssm_parameter("/app/customersupportvpc/agentcore/cognito_domain")
        redirect_uri = "http://localhost:8080/callback"

        login_params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "email openid profile",
            "code_challenge_method": "S256",
            "code_challenge": code_challenge,
            "state": state,
        }

        login_url = f"{cognito_domain}/oauth2/authorize?{urlencode(login_params)}"

        # Try automated OAuth flow first
        auth_code = get_auth_code_automatically(login_url)

        # Fallback to manual flow if automation fails
        if not auth_code:
            print("\n🔧 Automated flow failed. Falling back to manual authentication:")
            print("🔐 Open the following URL in a browser to authenticate:")
            print(login_url)
            auth_code = input("📥 Paste the `code` from the redirected URL: ").strip()

        token_url = get_ssm_parameter("/app/customersupportvpc/agentcore/cognito_token_url")
        response = requests.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": auth_code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            print(f"❌ Failed to exchange code: {response.text}")
            sys.exit(1)

        access_token = response.json()["access_token"]

        # Save the token for future use
        save_access_token(access_token, runtime_id)
        print("✅ Access token acquired and saved.")

    session_id = str(uuid.uuid4())
    print("\n🤖 Starting interactive session with agent. Type 'q' or 'quit' to exit.\n")

    while True:
        user_input = input("👤 You: ").strip()

        if user_input.lower() in ["q", "quit"]:
            print("👋 Goodbye!")
            break

        if not user_input:
            continue

        print("🤖 Assistant: ", end="", flush=True)
        # asyncio.run(
        invoke_endpoint(
            agent_arn=agent_arn,
            payload=json.dumps({"prompt": user_input, "actor_id": "DEFAULT"}),
            bearer_token=access_token,
            session_id=session_id,
            stream=True,
        )
        # )
        print("\n")


if __name__ == "__main__":
    main()
