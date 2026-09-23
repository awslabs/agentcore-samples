"""
Test script: Invokes the M2M + Auth Code agent with a Cognito bearer token.

Tests:
  1. M2M flow  — agent calls internal API using client credentials (no user interaction)
  2. Auth Code — agent accesses Google Calendar on behalf of the user (3LO consent flow)

For the 3LO test, this script:
  - Starts the OAuth2 callback server (localhost:9090)
  - Stores the user's bearer token so session binding can verify identity
  - Invokes the agent (which returns a Google consent URL on first call)
  - Waits for user to complete consent, then re-invokes to retrieve calendar events

Usage:
    # Test M2M flow
    python invoke.py --flow m2m

    # Test Auth Code (3LO) flow
    python invoke.py --flow authcode
"""

import warnings

import argparse
import json
import os
import subprocess
import sys
import webbrowser

import boto3

from oauth2_callback_server import (
    store_token_in_oauth2_callback_server,
    wait_for_oauth2_server_to_be_ready,
    get_oauth2_callback_url,
)

warnings.filterwarnings("ignore", category=Warning, module="requests")
warnings.filterwarnings("ignore", message="urllib3")


def get_bearer_token(config: dict) -> str:
    """Get a fresh Cognito access token."""
    cognito = boto3.client("cognito-idp", region_name=config["region"])
    auth = cognito.initiate_auth(
        ClientId=config["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": config["username"],
            "PASSWORD": config["password"],
        },
    )
    return auth["AuthenticationResult"]["AccessToken"]


def _find_project_dir() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    for entry in os.listdir(base):
        candidate = os.path.join(base, entry)
        if os.path.isdir(candidate) and os.path.isdir(os.path.join(candidate, "agentcore")):
            return candidate
    raise FileNotFoundError("No agentcore project directory found. Run 'agentcore create' first.")


def _find_in_json(obj, key):
    """Recursively search for a key in nested JSON."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = _find_in_json(v, key)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_in_json(item, key)
            if result:
                return result
    return None


def get_agent_arn() -> str:
    """Read the deployed agent ARN from deployed-state.json.

    Searches for runtimeArn recursively to work across CLI versions.
    """
    project_dir = _find_project_dir()
    state_file = os.path.join(project_dir, "agentcore", ".cli", "deployed-state.json")
    if not os.path.exists(state_file):
        raise FileNotFoundError("No deployed-state.json found. Run 'agentcore deploy -y' first.")
    with open(state_file) as f:
        state = json.load(f)
    arn = _find_in_json(state, "runtimeArn")
    if arn:
        return arn
    raise ValueError("No deployed agent found. Run 'agentcore deploy -y' first.")


def parse_event_stream(response: dict) -> str:
    parts = []
    for event in response.get("response", []):
        raw = event if isinstance(event, bytes) else event.get("chunk", {}).get("bytes", b"")
        if raw:
            try:
                decoded = json.loads(raw.decode("utf-8"))
                if isinstance(decoded, str):
                    parts.append(decoded)
                elif isinstance(decoded, dict):
                    content = decoded.get("content", [])
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(c["text"])
                        elif isinstance(c, str):
                            parts.append(c)
                    if not content and "message" in decoded:
                        msg = decoded["message"]
                        if isinstance(msg, dict):
                            for c in msg.get("content", []):
                                if isinstance(c, dict) and c.get("type") == "text":
                                    parts.append(c["text"])
            except Exception:
                parts.append(raw.decode("utf-8"))
    return "\n".join(parts) if parts else "(no response)"


def invoke(client, agent_arn: str, prompt: str, bearer_token: str, user_id: str, region: str) -> str:
    def _inject_bearer(request, **kwargs):
        request.headers["Authorization"] = f"Bearer {bearer_token}"

    client.meta.events.register("before-send.bedrock-agentcore.InvokeAgentRuntime", _inject_bearer)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeUserId=user_id,
        qualifier="DEFAULT",
        payload=json.dumps({"prompt": prompt}),
    )
    client.meta.events.unregister("before-send.bedrock-agentcore.InvokeAgentRuntime", _inject_bearer)
    return parse_event_stream(resp)


def test_m2m(client, agent_arn: str, bearer_token: str, config: dict):
    print("\n=== M2M Flow Test ===")
    print("The agent will get weather data using M2M client credentials (no user consent needed).")
    prompt = "What is the weather in Seattle?"
    print(f"Prompt: '{prompt}'")

    result = invoke(client, agent_arn, prompt, bearer_token, config["username"], config["region"])
    print(f"\nAgent response:\n{result}")


def test_authcode(client, agent_arn: str, bearer_token: str, config: dict, provider: str = "google"):
    provider_config = {
        "github": {
            "prompt": "List my GitHub repositories.",
            "consent_keywords": ["github", "oauth", "http"],
            "wait_message": "Waiting for you to complete the GitHub consent flow...",
            "reinvoke_message": "Re-invoking agent to retrieve GitHub repositories...",
        },
        "google": {
            "prompt": "What is on my Google Calendar today?",
            "consent_keywords": ["google", "oauth", "http"],
            "wait_message": "Waiting for you to complete the Google consent flow...",
            "reinvoke_message": "Re-invoking agent to retrieve calendar events...",
        },
    }
    cfg = provider_config[provider]

    print(f"\n=== Auth Code (3LO) Flow Test — {provider.capitalize()} ===")
    print("Starting OAuth2 callback server...")

    server_proc = subprocess.Popen(
        [sys.executable, "oauth2_callback_server.py", "--region", config["region"]],
    )

    try:
        if not wait_for_oauth2_server_to_be_ready():
            print("ERROR: OAuth2 callback server did not start in time.")
            return

        # Store the user's bearer token for session binding
        store_token_in_oauth2_callback_server(bearer_token)
        print(f"  Callback URL: {get_oauth2_callback_url()}")  # codeql[py/clear-text-logging-sensitive-data]

        prompt = cfg["prompt"]
        print(f"\nPrompt: '{prompt}'")
        print("Invoking agent (first call — expect consent URL)...")

        result = invoke(
            client,
            agent_arn,
            prompt,
            bearer_token,
            config["username"],
            config["region"],
        )
        print(f"\nAgent response:\n{result}")

        # If response contains an auth URL, wait for user to complete consent
        result_lower = result.lower()
        if "http" in result_lower and any(kw in result_lower for kw in cfg["consent_keywords"]):
            # Extract and auto-open the consent URL
            import re

            urls = re.findall(r'https?://[^\s\'")*\]]+', str(result))
            if urls:
                consent_url = urls[0]
                print(f"\nConsent URL: {consent_url}")
                print("Opening in your browser automatically...")
                webbrowser.open(consent_url)
            print(f"\n{cfg['wait_message']}")
            print("After authorizing in your browser, press Enter to re-invoke the agent.")
            input()

            print(cfg["reinvoke_message"])
            result2 = invoke(
                client,
                agent_arn,
                prompt,
                bearer_token,
                config["username"],
                config["region"],
            )
            print(f"\nAgent response:\n{result2}")

    finally:
        server_proc.terminate()
        server_proc.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--flow",
        choices=["m2m", "authcode", "both"],
        default="both",
        help="Which flow to test (default: both)",
    )
    parser.add_argument(
        "--provider",
        choices=["github", "google"],
        default="google",
        help="3LO provider for authcode flow: github or google (default: google)",
    )
    args = parser.parse_args()

    try:
        with open("cognito_config.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("ERROR: cognito_config.json not found. Run 'python setup_cognito.py' first.")
        sys.exit(1)

    print("Getting Cognito bearer token...")
    bearer_token = get_bearer_token(config)
    print(f"  Token obtained (first 20 chars): {bearer_token[:20]}...")

    print("Resolving deployed agent ARN...")
    agent_arn = get_agent_arn()
    print(f"  Agent ARN: {agent_arn}")

    boto_client = boto3.client("bedrock-agentcore", region_name=config["region"])

    if args.flow in ("m2m", "both"):
        test_m2m(boto_client, agent_arn, bearer_token, config)

    if args.flow in ("authcode", "both"):
        test_authcode(boto_client, agent_arn, bearer_token, config, provider=args.provider)


if __name__ == "__main__":
    main()
