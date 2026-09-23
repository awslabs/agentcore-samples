"""Deploy OBO token exchange credential provider, gateway, and target.

Creates:
1. Custom OAuth2 credential provider with OBO token exchange config
2. AgentCore Gateway with Entra ID OIDC inbound auth (CUSTOM_JWT)
3. Gateway target with OpenAPI schema for Microsoft Graph + OBO outbound auth

Requires MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET, MICROSOFT_TENANT_ID
in environment.

Usage:
    uv run python scripts/obo-token-exchange/deploy.py
"""

import json
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it before running this script.")
        sys.exit(1)
    return val


def wait_for_gateway(client, gateway_id):
    end_statuses = [
        "READY",
        "FAILED",
        "CREATE_FAILED",
        "UPDATE_FAILED",
        "DELETE_FAILED",
    ]
    status = "CREATING"
    print("Waiting for Gateway to become READY...")
    while status not in end_statuses:
        time.sleep(10)
        gw = client.get_gateway(gatewayIdentifier=gateway_id)
        status = gw["status"]
        print(f"  Gateway status: {status}")
    if status != "READY":
        print(f"ERROR: Gateway ended in status: {status}")
        sys.exit(1)
    print("Gateway is READY")


def wait_for_target(client, gateway_id, target_id):
    end_statuses = [
        "READY",
        "FAILED",
        "UPDATE_UNSUCCESSFUL",
        "SYNCHRONIZE_UNSUCCESSFUL",
    ]
    status = "CREATING"
    print("Waiting for Target to become READY...")
    while status not in end_statuses:
        time.sleep(10)
        tgt = client.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )
        status = tgt["status"]
        print(f"  Target status: {status}")
    if status != "READY":
        print(f"ERROR: Target ended in status: {status}")
        sys.exit(1)
    print("Target is READY")


def main():
    tenant_id = get_required_env("MICROSOFT_TENANT_ID")
    client_id = get_required_env("MICROSOFT_CLIENT_ID")
    client_secret = get_required_env("MICROSOFT_CLIENT_SECRET")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client
    identity = boto3.client("bedrock-agentcore-control", region_name=region)

    # --- Step 1: Create Custom OAuth2 credential provider with OBO ---
    print("=" * 60)
    print("Step 1: Create OBO Credential Provider")
    print("=" * 60)

    provider_resp = identity.create_oauth2_credential_provider(
        name="microsoft-obo-provider",
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput={
            "customOauth2ProviderConfig": {
                "oauthDiscovery": {
                    "discoveryUrl": f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
                },
                "clientId": client_id,
                "clientSecret": client_secret,
                "clientAuthenticationMethod": "CLIENT_SECRET_POST",
                "onBehalfOfTokenExchangeConfig": {
                    "grantType": "JWT_AUTHORIZATION_GRANT"
                },
            }
        },
    )
    provider_arn = provider_resp["credentialProviderArn"]
    print("  Credential provider created: microsoft-obo-provider")
    print(f"  ARN: {provider_arn}")

    # --- Step 2: Create Gateway with Entra ID OIDC inbound auth ---
    print("\n" + "=" * 60)
    print("Step 2: Create AgentCore Gateway")
    print("=" * 60)

    # Entra ID v1.0 OIDC discovery URL for inbound auth
    # Entra ID issues v1.0 access tokens by default (issuer: sts.windows.net).
    # The discovery URL MUST match the token version.
    discovery_url = f"https://login.microsoftonline.com/{tenant_id}/.well-known/openid-configuration"

    role_arn = admin.create_gateway_role("microsoft-obo-gateway", oauth_targets=True)

    gw_resp = control.create_gateway(
        name="microsoft-obo-gateway",
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedAudience": [f"api://{client_id}"],
            }
        },
        protocolConfiguration={"mcp": {"supportedVersions": ["2025-11-25"]}},
        description="AgentCore Gateway with Entra ID OBO token exchange",
        exceptionLevel="DEBUG",
    )
    gateway_id = gw_resp["gatewayId"]
    gateway_url = gw_resp["gatewayUrl"]
    print(f"  Gateway ID: {gateway_id}")
    print(f"  Gateway URL: {gateway_url}")
    print(f"  Inbound auth: Entra ID OIDC (tenant: {tenant_id})")
    print(f"  Allowed audience: api://{client_id}")

    wait_for_gateway(control, gateway_id)

    # --- Step 3: Create Gateway Target with OpenAPI schema + OBO outbound auth ---
    print("\n" + "=" * 60)
    print("Step 3: Create Microsoft Graph Target")
    print("=" * 60)

    openapi_spec = json.dumps(
        {
            "openapi": "3.0.0",
            "info": {"title": "Microsoft Graph Calendar API", "version": "1.0"},
            "servers": [{"url": "https://graph.microsoft.com/v1.0"}],
            "paths": {
                "/me/calendarview": {
                    "get": {
                        "operationId": "listCalendarEvents",
                        "summary": "List calendar events for the current user within a date range",
                        "parameters": [
                            {
                                "name": "startDateTime",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Start date/time in ISO 8601 format (e.g. 2025-01-01T00:00:00Z)",
                            },
                            {
                                "name": "endDateTime",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "End date/time in ISO 8601 format (e.g. 2025-01-02T00:00:00Z)",
                            },
                        ],
                        "responses": {"200": {"description": "Calendar events"}},
                    }
                },
                "/me/messages": {
                    "get": {
                        "operationId": "listUserMails",
                        "summary": "List recent email messages for the current user",
                        "parameters": [
                            {
                                "name": "top",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "integer"},
                                "description": "Number of messages to return",
                            },
                            {
                                "name": "select",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "string"},
                                "description": "Comma-separated list of fields to return",
                            },
                        ],
                        "responses": {"200": {"description": "Email messages"}},
                    }
                },
                "/me": {
                    "get": {
                        "operationId": "getMyProfile",
                        "summary": "Get the current user profile information",
                        "responses": {"200": {"description": "User profile"}},
                    }
                },
            },
        }
    )

    target_resp = control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name="microsoft-graph-obo",
        description="Microsoft Graph API with OBO token exchange",
        targetConfiguration={"mcp": {"openApiSchema": {"inlinePayload": openapi_spec}}},
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": provider_arn,
                        "scopes": ["https://graph.microsoft.com/.default"],
                        "grantType": "TOKEN_EXCHANGE",
                        "customParameters": {"requested_token_use": "on_behalf_of"},
                    }
                },
            }
        ],
    )
    target_id = target_resp["targetId"]
    print(f"  Target ID: {target_id}")
    print(f"  Target name: {target_resp['name']}")

    wait_for_target(control, gateway_id, target_id)

    # --- Save state for other scripts ---
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars["MICROSOFT_TENANT_ID"] = tenant_id
    env_vars["MICROSOFT_CLIENT_ID"] = client_id
    env_vars["OBO_GATEWAY_ID"] = gateway_id
    env_vars["OBO_GATEWAY_URL"] = gateway_url
    env_vars["OBO_TARGET_ID"] = target_id
    env_vars["OBO_PROVIDER_ARN"] = provider_arn
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    print("\n  Saved to .env")

    print("\n" + "=" * 60)
    print("Deployment complete")
    print("=" * 60)
    print(f"\n  Gateway MCP URL: {gateway_url}")


if __name__ == "__main__":
    main()
