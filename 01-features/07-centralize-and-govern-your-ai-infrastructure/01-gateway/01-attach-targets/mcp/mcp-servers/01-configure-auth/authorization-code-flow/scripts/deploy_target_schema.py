"""Create the MCP server target with the tool schema supplied upfront (Method 2).

No authorization happens at create time -- the admin supplies the tool schema and
the gateway parses and caches it, so the target is immediately READY. Users are
prompted to authorize on their first tool invocation.

The endpoint, scopes, target name, and schema file all come from the profile
named by the required flag -- see mcp_config.py.

Requires GATEWAY_ID, CRED_PROVIDER_ARN in environment or .env.

Where the user is returned after consent depends on who is driving the flow:

    PORTAL_CONNECT_RETURN_URL set  -> the AgentCore Consent Portal
                                     (github/portal.md)
    unset                         -> http://localhost:8080/callback, i.e.
                                     callback_server.py (github/README.md)

deploy_portal.py writes that variable into .env, so running it first is the only
thing needed to point this target at the portal.

Usage:
    uv run python scripts/deploy_target_schema.py --github
"""

import os

import boto3
from mcp_config import (
    credential_provider_name,
    custom_parameters,
    get_required_env,
    load_env,
    save_env,
    schema_target_name,
    select_profile,
    tool_schema_path,
)

# Where callback_server.py listens. Used when no portal is deployed.
LOCAL_RETURN_URL = "http://localhost:8080/callback"


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


def resolve_provider_arn(client, profile):
    """The profile's own credential provider ARN, by derived name.

    .env is a single flat namespace, so CRED_PROVIDER_ARN holds whichever
    deploy_credential ran last. On a shared gateway with more than one target
    that would bind this target to the wrong provider. Looking the provider up
    by the profile's derived name keeps each target bound to its own provider
    regardless of run order; the .env value is only a fallback.
    """
    name = credential_provider_name(profile)
    try:
        return client.get_oauth2_credential_provider(name=name)["credentialProviderArn"]
    except Exception:
        return get_required_env("CRED_PROVIDER_ARN")


def main():
    profile = select_profile(__doc__)
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")

    # A property of whoever receives the redirect, not of the MCP server -- so it
    # stays out of the profile either way.
    portal_return_url = os.environ.get("PORTAL_CONNECT_RETURN_URL")
    return_url = portal_return_url or LOCAL_RETURN_URL

    region = boto3.Session().region_name
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    cred_provider_arn = resolve_provider_arn(client, profile)

    # Resolved from the profile's toolSchema, relative to scripts/. For the
    # github profile that is ../github/github.json -- the single copy of the
    # schema, also linked from github/README.md and github/portal.md, which tell
    # the reader to paste this exact file into the console. Do not fork it into
    # scripts/.
    schema_path = tool_schema_path(profile)
    with open(schema_path) as f:
        tool_schema = f.read()

    target_name = schema_target_name(profile)

    existing_id = find_target(client, gateway_id, target_name)
    if existing_id:
        print(
            f"--- {profile['displayName']} schema target exists: "
            f"{existing_id} -- reusing ---"
        )
        save_env(SCHEMA_TARGET_ID=existing_id)
        print("  Saved to .env")
        return

    print(
        f"--- Creating {profile['displayName']} target "
        "(Method 2: schema upfront) ---"
    )
    print("  No browser authorization needed during creation.")
    if portal_return_url:
        print(f"  Return URL: {return_url} (Consent Portal)")
    else:
        print(f"  Return URL: {return_url} (local callback_server.py)")
    print()

    # Extra authorization parameters the provider requires (Atlassian needs
    # aud + resource here or its consent page greys out Accept). GitHub has
    # none, so this stays absent and the call is unchanged for it.
    oauth_provider = {
        "providerArn": cred_provider_arn,
        "grantType": "AUTHORIZATION_CODE",
        "defaultReturnUrl": return_url,
        "scopes": profile["scopes"],
    }
    extra_params = custom_parameters(profile)
    if extra_params:
        oauth_provider["customParameters"] = extra_params

    target_response = client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name,
        description=(
            f"{profile['displayName']} MCP Server with authorization code flow "
            "- schema upfront"
        ),
        targetConfiguration={
            "mcp": {
                "mcpServer": {
                    "endpoint": profile["endpoint"],
                    "mcpToolSchema": {"inlinePayload": tool_schema},
                }
            }
        },
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "OAUTH",
                "credentialProvider": {"oauthCredentialProvider": oauth_provider},
            }
        ],
    )

    target_id = target_response["targetId"]
    print(f"  Target ID: {target_id}")
    print(f"  Status: {target_response['status']}")
    print(
        f"  Users will be prompted to authorize {profile['displayName']} "
        "on first tool invocation."
    )

    save_env(SCHEMA_TARGET_ID=target_id)
    print("  Saved to .env")


if __name__ == "__main__":
    main()
