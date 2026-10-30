"""Create the MCP server target with implicit sync (Method 1).

Admin must complete the authorization code flow during target creation. The
gateway then calls list/tools on the MCP server and caches the result.

The endpoint, scopes, and target name come from the profile named by the
required flag -- see mcp_config.py.

Requires GATEWAY_ID, CRED_PROVIDER_ARN in environment or .env.

Usage:
    uv run python scripts/deploy_target_implicit.py --github
"""

import time

import boto3
from mcp_config import (
    get_required_env,
    implicit_target_name,
    load_env,
    save_env,
    select_profile,
)


def find_target(client, gateway_id, name):
    """Return the targetId of an existing target with this name, or None.

    CreateGatewayTarget is not idempotent -- a second run raises
    ConflictException. A paged name scan lets a re-run reuse the target.
    """
    paginator = client.get_paginator("list_gateway_targets")
    for page in paginator.paginate(gatewayIdentifier=gateway_id):
        for tgt in page.get("items", []):
            if tgt.get("name") == name:
                return tgt["targetId"]
    return None


def main():
    profile = select_profile(__doc__)
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")
    cred_provider_arn = get_required_env("CRED_PROVIDER_ARN")

    region = boto3.Session().region_name
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    target_name = implicit_target_name(profile)

    existing_id = find_target(client, gateway_id, target_name)
    if existing_id:
        # A fresh authorization URL is only returned by create. If this target is
        # still NEEDS_AUTHORIZATION, re-run cleanup and create it anew to get one.
        tgt = client.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=existing_id
        )
        print(
            f"--- {profile['displayName']} implicit target exists: "
            f"{existing_id} ({tgt['status']}) -- reusing ---"
        )
        save_env(IMPLICIT_TARGET_ID=existing_id)
        print("  Saved to .env")
        return

    print(f"--- Creating {profile['displayName']} target (Method 1: implicit sync) ---")
    print("  This requires you to authorize in your browser.\n")

    target_response = client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name,
        description=(
            f"{profile['displayName']} MCP Server with authorization code flow "
            "- implicit sync"
        ),
        targetConfiguration={"mcp": {"mcpServer": {"endpoint": profile["endpoint"]}}},
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": cred_provider_arn,
                        "grantType": "AUTHORIZATION_CODE",
                        # A property of callback_server.py's listener, not of the
                        # MCP server -- so it stays out of the profile.
                        "defaultReturnUrl": "http://localhost:8080/callback",
                        "scopes": profile["scopes"],
                    }
                },
            }
        ],
    )

    target_id = target_response["targetId"]
    auth_url = target_response["authorizationData"]["oauth2"]["authorizationUrl"]
    user_id = target_response["authorizationData"]["oauth2"]["userId"]

    print(f"  Target ID: {target_id}")
    print(f"  Status: {target_response['status']} (Needs Authorization)")
    print("\n  Start the callback server in another terminal:")
    print(  # lgtm[py/clear-text-logging-sensitive-data]
        f"  uv run python scripts/callback_server.py"  # codeql[py/clear-text-logging-sensitive-data]
        f' --user-id "{user_id}"'
        f' --auth-url "{auth_url}"'
    )
    print("\n  After authorizing, the target will become READY.")

    # Wait for target to become READY
    print("\n  Waiting for target to become READY...")
    for _ in range(12):
        time.sleep(10)
        tgt = client.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )
        status = tgt["status"]
        print(f"    Status: {status}")
        if status in ["READY", "FAILED", "UPDATE_UNSUCCESSFUL"]:
            break

    save_env(IMPLICIT_TARGET_ID=target_id)
    print("  Saved to .env")


if __name__ == "__main__":
    main()
