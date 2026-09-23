"""Create LinkedIn OAuth2 credential provider.

Requires LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET in environment.

Usage:
    uv run python scripts/linkedin-auth-code/deploy_credential.py
"""

import os
import sys

import boto3


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


def main():
    load_env()

    client_id = get_required_env("LINKEDIN_CLIENT_ID")
    client_secret = get_required_env("LINKEDIN_CLIENT_SECRET")

    region = boto3.Session().region_name
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    print("--- Creating LinkedIn OAuth2 credential provider ---")
    response = client.create_oauth2_credential_provider(
        name="linkedin-oauth-credential",
        credentialProviderVendor="LinkedinOauth2",
        oauth2ProviderConfigInput={
            "linkedinOauth2ProviderConfig": {
                "clientId": client_id,
                "clientSecret": client_secret,
            }
        },
    )
    cred_provider_arn = response["credentialProviderArn"]
    callback_url = response["callbackUrl"]

    print(f"  Credential ARN: {cred_provider_arn}")
    print(f"  Callback URL:   {callback_url}")
    print()
    print("  *** ACTION REQUIRED ***")
    print(
        "  Go to https://developer.linkedin.com/ → your app → Auth → OAuth 2.0 settings"
    )
    print(f"  Add this as an 'Authorized redirect URL':\n\n    {callback_url}\n")

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars["CRED_PROVIDER_ARN"] = cred_provider_arn
    env_vars["CALLBACK_URL"] = callback_url
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    print("  Saved to .env")


if __name__ == "__main__":
    main()
