"""Create gateway with dual interceptors and MCP server target for FGAC tutorial.

Assumes CloudFormation stacks (interceptors + cognito scopes) and the
MCP server are already deployed. Creates the gateway with both REQUEST
and RESPONSE interceptors, then registers the MCP server as a target.

Requires COGNITO_STACK_NAME, MCP_SERVER_URL, REQUEST_INTERCEPTOR_ARN,
RESPONSE_INTERCEPTOR_ARN, FGAC_CLIENT_ID in environment or .env.

Usage:
    uv run python scripts/fine-grain-access-control/deploy.py
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

GATEWAY_NAME = "fgac-gateway"


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
    request_interceptor_arn = get_required_env("REQUEST_INTERCEPTOR_ARN")
    response_interceptor_arn = get_required_env("RESPONSE_INTERCEPTOR_ARN")
    fgac_client_id = get_required_env("FGAC_CLIENT_ID")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client
    cfn = boto3.client("cloudformation", region_name=region)

    cognito_outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack)["Stacks"][0]["Outputs"]
    }
    discovery_url = cognito_outputs["DiscoveryUrl"]
    mcp_client_id = cognito_outputs["MCPClientId"]

    # --- Create gateway with dual interceptors ---
    print("=" * 60)
    print("Step 1: Create Gateway with REQUEST + RESPONSE Interceptors")
    print("=" * 60)

    role_arn = admin.create_gateway_role(
        GATEWAY_NAME, oauth_targets=True, lambda_targets=True
    )

    gw_resp = control.create_gateway(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        protocolType="MCP",
        protocolConfiguration={
            "mcp": {
                "supportedVersions": ["2025-11-25"],
                "searchType": "SEMANTIC",
            }
        },
        interceptorConfigurations=[
            {
                "interceptor": {"lambda": {"arn": request_interceptor_arn}},
                "interceptionPoints": ["REQUEST"],
                "inputConfiguration": {"passRequestHeaders": True},
            },
            {
                "interceptor": {"lambda": {"arn": response_interceptor_arn}},
                "interceptionPoints": ["RESPONSE"],
                "inputConfiguration": {"passRequestHeaders": True},
            },
        ],
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": [fgac_client_id],
            }
        },
        exceptionLevel="DEBUG",
    )

    gateway_id = gw_resp["gatewayId"]
    gateway_url = gw_resp["gatewayUrl"]
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

    # --- Create credential provider for outbound auth ---
    print("\n" + "=" * 60)
    print("Step 2: Create OAuth2 Credential Provider")
    print("=" * 60)

    cognito = boto3.client("cognito-idp", region_name=region)
    mcp_client_secret = cognito.describe_user_pool_client(
        UserPoolId=cognito_outputs["UserPoolId"], ClientId=mcp_client_id
    )["UserPoolClient"]["ClientSecret"]

    cred_name = "fgac-mcp-server-oauth"
    try:
        cred_resp = admin.create_credential_provider(
            name=cred_name,
            discovery_url=discovery_url,
            client_id=mcp_client_id,
            client_secret=mcp_client_secret,
        )
        cred_arn = cred_resp["credentialProviderArn"]
    except control.exceptions.ConflictException:
        print(f"  Credential provider already exists: {cred_name}")
        account_id = admin.account_id
        cred_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default/oauth2credentialprovider/{cred_name}"

    print(f"  Credential ARN: {cred_arn}")

    # --- Create gateway target ---
    print("\n" + "=" * 60)
    print("Step 3: Create Gateway Target")
    print("=" * 60)

    target_resp = admin.create_target(
        gateway_id=gateway_id,
        name="fgac-mcp-target",
        endpoint=mcp_server_url,
        credential_provider_arn=cred_arn,
        scopes=["api/mcp"],
    )
    target_id = target_resp["targetId"]
    print(f"  Target ID: {target_id}")

    print("  Waiting for target to become READY...")
    while True:
        time.sleep(10)
        tgt = control.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )
        status = tgt["status"]
        print(f"    Status: {status}")
        if status in ["READY", "FAILED", "UPDATE_UNSUCCESSFUL"]:
            break

    # --- Save state ---
    save_env(
        {
            "GATEWAY_ID": gateway_id,
            "GATEWAY_URL": gateway_url,
            "TARGET_ID": target_id,
            "CRED_PROVIDER_ARN": cred_arn,
        }
    )

    print("\n" + "=" * 60)
    print("Deployment complete")
    print("=" * 60)
    print(f"\n  Gateway URL: {gateway_url}")
    print(f"  Target: fgac-mcp-target ({target_id})")
    print("\n  Run: uv run python scripts/fine-grain-access-control/invoke.py")


if __name__ == "__main__":
    main()
