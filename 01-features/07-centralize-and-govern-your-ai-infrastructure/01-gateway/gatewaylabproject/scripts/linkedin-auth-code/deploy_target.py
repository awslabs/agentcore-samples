"""Create LinkedIn OpenAPI target with authorization code flow.

Uses schema upfront (no admin auth needed during creation).
Users will be prompted to authorize on first tool invocation.

Requires GATEWAY_ID, CRED_PROVIDER_ARN in environment or .env.

Usage:
    uv run python scripts/linkedin-auth-code/deploy_target.py
"""

import json
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


LINKEDIN_OPENAPI_SPEC = json.dumps(
    {
        "openapi": "3.0.0",
        "info": {"title": "LinkedIn UserInfo API", "version": "2.0.0"},
        "servers": [{"url": "https://api.linkedin.com/v2"}],
        "paths": {
            "/userinfo": {
                "get": {
                    "operationId": "getUserInfo",
                    "summary": "Get the authenticated user's LinkedIn profile information",
                    "responses": {
                        "200": {
                            "description": "User profile information",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "sub": {"type": "string"},
                                            "name": {"type": "string"},
                                            "given_name": {"type": "string"},
                                            "family_name": {"type": "string"},
                                            "picture": {"type": "string"},
                                            "locale": {"type": "string"},
                                            "email": {"type": "string"},
                                            "email_verified": {"type": "boolean"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
)


def main():
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")
    cred_provider_arn = get_required_env("CRED_PROVIDER_ARN")

    region = boto3.Session().region_name
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    print("--- Creating LinkedIn target (schema upfront) ---")
    target_response = client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name="linkedin-auth-code-target",
        description="LinkedIn API with authorization code flow",
        targetConfiguration={
            "mcp": {"openApiSchema": {"inlinePayload": LINKEDIN_OPENAPI_SPEC}}
        },
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": cred_provider_arn,
                        "grantType": "AUTHORIZATION_CODE",
                        "defaultReturnUrl": "http://localhost:8080/callback",
                        "scopes": ["openid", "profile", "email"],
                    }
                },
            }
        ],
    )

    target_id = target_response["targetId"]
    print(f"  Target ID: {target_id}")
    print(f"  Status: {target_response['status']}")

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars["TARGET_ID"] = target_id
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    print("  Saved to .env")


if __name__ == "__main__":
    main()
