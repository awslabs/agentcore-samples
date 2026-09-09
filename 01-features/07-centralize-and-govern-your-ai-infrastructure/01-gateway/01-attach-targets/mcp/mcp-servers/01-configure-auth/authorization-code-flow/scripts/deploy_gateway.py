"""Create the AgentCore Gateway for the auth code flow tutorial.

Creates a gateway with searchType: "SEMANTIC", response streaming enabled,
sessions enabled, and all three MCP protocol versions advertised:

    2025-11-25  required for URL-mode elicitation (the authorization code flow)
    2025-06-18  streamable HTTP
    2025-03-26  older clients

Advertising all three is deliberate: the newest version is what enables the flow
this tutorial demonstrates, but a client that only speaks an older one still
connects instead of failing at negotiation. That configuration comes from
mcp_config.mcp_protocol_configuration(), shared with deploy_gateway_entra.py so
the two gateways cannot drift.

This script does Cognito inbound auth (github/README.md). For Microsoft Entra ID
inbound auth, use deploy_gateway_entra.py --entra instead.

The gateway name is derived from the profile named by the required flag, so
cleanup_gateway.py deletes the same gateway and IAM role this script creates.

Requires COGNITO_STACK_NAME in environment. Reads CRED_PROVIDER_ARN from .env.

Usage:
    uv run python scripts/deploy_gateway.py --github
"""

import time

import boto3
from gateway_admin import GatewayBoto3Client
from mcp_config import (
    gateway_name,
    get_required_env,
    load_env,
    mcp_protocol_configuration,
    save_env,
    select_profile,
)


def find_gateway(control, name):
    """Return the gatewayId of an existing gateway with this name, or None.

    CreateGateway is not idempotent -- a second run raises ConflictException. A
    paged name scan lets a re-run reuse the gateway instead of failing.
    """
    paginator = control.get_paginator("list_gateways")
    for page in paginator.paginate():
        for gw in page.get("items", []):
            if gw.get("name") == name:
                return gw["gatewayId"]
    return None


def main():
    profile = select_profile(__doc__)
    load_env()
    gw_name = gateway_name(profile)

    cognito_stack = get_required_env("COGNITO_STACK_NAME")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client
    cfn = boto3.client("cloudformation", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)  # noqa: F841

    outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack)["Stacks"][0]["Outputs"]
    }
    discovery_url = outputs["DiscoveryUrl"]
    gw_client_id = outputs["GatewayClientId"]

    print("--- Creating gateway IAM role ---")
    role_arn = admin.create_gateway_role(gw_name, oauth_targets=True)

    existing_id = find_gateway(control, gw_name)
    if existing_id:
        gw = control.get_gateway(gatewayIdentifier=existing_id)
        gateway_id = gw["gatewayId"]
        gw_url = gw["gatewayUrl"]
        print(
            f"\n--- Gateway {gw_name} exists: {gateway_id} ({gw['status']}) -- reusing ---"
        )
    else:
        print(f"\n--- Creating AgentCore Gateway: {gw_name} ---")
        gw_resp = control.create_gateway(
            name=gw_name,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={
                "customJWTAuthorizer": {
                    "allowedClients": [gw_client_id],
                    "discoveryUrl": discovery_url,
                }
            },
            protocolConfiguration=mcp_protocol_configuration(),
            exceptionLevel="DEBUG",
        )
        gateway_id = gw_resp["gatewayId"]
        gw_url = gw_resp["gatewayUrl"]
    # Store the bare host: CreateGateway returns gatewayUrl with /mcp appended,
    # and GatewayMCPClient re-appends it when missing. Matches deploy_gateway_entra.py.
    gateway_url = gw_url.rstrip("/").removesuffix("/mcp")
    print(f"  Gateway ID:  {gateway_id}")
    print(f"  Gateway URL: {gateway_url}")

    print("\n  Waiting for gateway to become READY...")
    while True:
        time.sleep(10)
        gw = control.get_gateway(gatewayIdentifier=gateway_id)
        status = gw["status"]
        print(f"    Status: {status}")
        if status in ["READY", "FAILED", "CREATE_FAILED"]:
            break

    save_env(GATEWAY_ID=gateway_id, GATEWAY_URL=gateway_url)
    print("\n  Saved to .env")


if __name__ == "__main__":
    main()
