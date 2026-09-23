"""Create gateway with header propagation interceptor and targets.

Creates:
- Gateway with REQUEST interceptor
- Lambda target with metadataConfiguration (allowlisted headers/query params)
- MCP server target (DEFAULT mode) with metadataConfiguration

Requires COGNITO_STACK_NAME, MCP_SERVER_URL, INTERCEPTOR_ARN, TOOL_ARN in env.

Usage:
    uv run python scripts/header-query-propagation/custom-header-query/deploy.py
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from gateway_admin import GatewayBoto3Client

GATEWAY_NAME = "header-query-gateway"
TOOL_DEFINITION = {
    "name": "echo",
    "description": "Echoes back input with propagated headers and query parameters",
    "inputSchema": {
        "type": "object",
        "properties": {"message": {"type": "string", "description": "Message to echo"}},
        "required": ["message"],
    },
}


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
        print(f"ERROR: {key} not set. Export it or add to .env")
        sys.exit(1)
    return val


def save_env(env_vars: dict[str, str]):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    existing: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    existing[key] = value
    existing.update(env_vars)
    with open(env_path, "w") as f:
        for key, value in existing.items():
            f.write(f"{key}={value}\n")
    print("  Saved state to .env")


def main():
    load_env()

    cognito_stack = get_required_env("COGNITO_STACK_NAME")
    mcp_server_url = get_required_env("MCP_SERVER_URL")
    interceptor_arn = get_required_env("INTERCEPTOR_ARN")
    tool_arn = get_required_env("TOOL_ARN")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client
    cfn = boto3.client("cloudformation", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    cognito_outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack)["Stacks"][0]["Outputs"]
    }
    discovery_url = cognito_outputs["DiscoveryUrl"]
    gw_client_id = cognito_outputs["GatewayClientId"]
    mcp_client_id = cognito_outputs["MCPClientId"]

    # --- Create Gateway with interceptor ---
    print("=" * 60)
    print("Step 1: Create Gateway with REQUEST Interceptor")
    print("=" * 60)

    role_arn = admin.create_gateway_role(
        GATEWAY_NAME, oauth_targets=True, lambda_targets=True
    )

    gw_resp = control.create_gateway(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        protocolType="MCP",
        protocolConfiguration={"mcp": {"supportedVersions": ["2025-11-25"]}},
        interceptorConfigurations=[
            {
                "interceptor": {"lambda": {"arn": interceptor_arn}},
                "interceptionPoints": ["REQUEST"],
                "inputConfiguration": {"passRequestHeaders": True},
            }
        ],
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": [gw_client_id],
            }
        },
        exceptionLevel="DEBUG",
    )
    gateway_id = gw_resp["gatewayId"]
    gateway_url = gw_resp["gatewayUrl"]
    print(f"  Gateway ID:  {gateway_id}")
    print(f"  Gateway URL: {gateway_url}")

    print("  Waiting for gateway to become READY...")
    while True:
        time.sleep(10)
        gw = control.get_gateway(gatewayIdentifier=gateway_id)
        if gw["status"] in ["READY", "FAILED", "CREATE_FAILED"]:
            print(f"    Status: {gw['status']}")
            break

    # --- Create Lambda target with metadataConfiguration ---
    print("\n" + "=" * 60)
    print("Step 2: Create Lambda Target with Header Allowlist")
    print("=" * 60)

    lambda_target_resp = control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name="header-echo-lambda-target",
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": tool_arn,
                    "toolSchema": {"inlinePayload": [TOOL_DEFINITION]},
                }
            }
        },
        metadataConfiguration={
            "allowedRequestHeaders": ["x-correlation-id", "x-tenant-id"],
            "allowedResponseHeaders": ["x-rate-limit-remaining"],
            "allowedQueryParameters": ["version", "environment"],
        },
        credentialProviderConfigurations=[
            {"credentialProviderType": "GATEWAY_IAM_ROLE"}
        ],
    )
    lambda_target_id = lambda_target_resp["targetId"]
    print(f"  Lambda Target ID: {lambda_target_id}")

    # --- Create MCP server target (DEFAULT mode) with metadataConfiguration ---
    print("\n" + "=" * 60)
    print("Step 3: Create MCP Server Target (DEFAULT) with Header Allowlist")
    print("=" * 60)

    mcp_client_secret = cognito.describe_user_pool_client(
        UserPoolId=cognito_outputs["UserPoolId"], ClientId=mcp_client_id
    )["UserPoolClient"]["ClientSecret"]

    cred_name = "header-query-mcp-oauth"
    try:
        cred_resp = admin.create_credential_provider(
            name=cred_name,
            discovery_url=discovery_url,
            client_id=mcp_client_id,
            client_secret=mcp_client_secret,
        )
        cred_arn = cred_resp["credentialProviderArn"]
    except control.exceptions.ConflictException:
        account_id = admin.account_id
        cred_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default/oauth2credentialprovider/{cred_name}"
        print(f"  Credential provider already exists: {cred_name}")

    mcp_target_resp = admin.create_target(
        gateway_id=gateway_id,
        name="header-echo-mcp-target",
        endpoint=mcp_server_url,
        credential_provider_arn=cred_arn,
        scopes=["api/mcp"],
        metadata_config={
            "allowedRequestHeaders": ["x-correlation-id", "x-tenant-id"],
            "allowedResponseHeaders": ["x-rate-limit-remaining"],
            "allowedQueryParameters": ["version", "environment"],
        },
    )
    mcp_target_id = mcp_target_resp["targetId"]
    print(f"  MCP Target ID: {mcp_target_id}")

    print("  Waiting for targets to become READY...")
    time.sleep(20)

    # --- Save state ---
    save_env(
        {
            "GATEWAY_ID": gateway_id,
            "GATEWAY_URL": gateway_url,
            "LAMBDA_TARGET_ID": lambda_target_id,
            "MCP_TARGET_ID": mcp_target_id,
            "CRED_PROVIDER_ARN": cred_arn,
        }
    )

    print("\n" + "=" * 60)
    print("Deployment complete")
    print("=" * 60)
    print(f"\n  Gateway URL: {gateway_url}")
    print(
        "\n  Run: uv run python scripts/header-query-propagation/custom-header-query/invoke.py"
    )


if __name__ == "__main__":
    main()
