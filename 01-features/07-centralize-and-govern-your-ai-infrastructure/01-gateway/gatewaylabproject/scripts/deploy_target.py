"""Create a gateway target with outbound OAuth via boto3.

Creates an OAuth2 credential provider (if needed) and a gateway target
pointing to the specified MCP server endpoint.

Requires GATEWAY_ID, MCP_SERVER_URL, COGNITO_STACK_NAME in environment.

Usage:
    uv run python scripts/deploy_target.py \
      --name streaming-mcp-server-target \
      --gateway-env-file scripts/streaming/.env

    uv run python scripts/deploy_target.py \
      --name session-mcp-server-target \
      --gateway-env-file scripts/sessions/.env
"""

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gateway_admin import GatewayBoto3Client


def load_env(env_file):
    if os.path.exists(env_file):
        with open(env_file) as f:
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


def main():
    parser = argparse.ArgumentParser(
        description="Create a gateway target with outbound OAuth"
    )
    parser.add_argument("--name", required=True, help="Target name")
    parser.add_argument(
        "--gateway-env-file",
        required=True,
        help="Path to .env file containing GATEWAY_ID",
    )
    args = parser.parse_args()

    load_env(args.gateway_env_file)

    gateway_id = get_required_env("GATEWAY_ID")
    mcp_server_url = get_required_env("MCP_SERVER_URL")
    cognito_stack = get_required_env("COGNITO_STACK_NAME")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client
    cfn = boto3.client("cloudformation", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack)["Stacks"][0]["Outputs"]
    }
    discovery_url = outputs["DiscoveryUrl"]
    mcp_client_id = outputs["MCPClientId"]
    user_pool_id = outputs["UserPoolId"]
    mcp_client_secret = cognito.describe_user_pool_client(
        UserPoolId=user_pool_id, ClientId=mcp_client_id
    )["UserPoolClient"]["ClientSecret"]

    cred_name = f"{args.name}-oauth"
    print(f"--- Creating OAuth2 credential provider '{cred_name}' ---")
    try:
        cred_resp = admin.create_credential_provider(
            name=cred_name,
            discovery_url=discovery_url,
            client_id=mcp_client_id,
            client_secret=mcp_client_secret,
        )
        cred_arn = cred_resp["credentialProviderArn"]
    except admin.client.exceptions.ConflictException:
        print(f"  Credential provider already exists: {cred_name}")
        account_id = admin.account_id
        cred_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default/oauth2credentialprovider/{cred_name}"

    print(f"  Credential ARN: {cred_arn}")

    print(f"\n--- Creating gateway target '{args.name}' ---")
    target_resp = admin.create_target(
        gateway_id=gateway_id,
        name=args.name,
        endpoint=mcp_server_url,
        credential_provider_arn=cred_arn,
        scopes=["api/mcp"],
    )
    target_id = target_resp["targetId"]
    print(f"  Target ID: {target_id}")

    print("\n  Waiting for target to become READY...")
    while True:
        time.sleep(10)
        tgt = control.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )
        status = tgt["status"]
        print(f"    Status: {status}")
        if status in ["READY", "FAILED", "CREATE_FAILED"]:
            break

    env_vars: dict[str, str] = {}
    if os.path.exists(args.gateway_env_file):
        with open(args.gateway_env_file) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars["TARGET_ID"] = target_id
    env_vars["CRED_PROVIDER_ARN"] = cred_arn
    with open(args.gateway_env_file, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    print(f"\n  Saved TARGET_ID and CRED_PROVIDER_ARN to {args.gateway_env_file}")


if __name__ == "__main__":
    main()
