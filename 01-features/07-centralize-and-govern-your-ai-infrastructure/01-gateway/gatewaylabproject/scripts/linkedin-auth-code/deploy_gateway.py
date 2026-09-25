"""Create AgentCore Gateway for the GitHub auth code flow tutorial.

Creates a gateway with supportedVersions: ["2025-11-25"] and
searchType: "SEMANTIC" (required for URL elicitation).

Requires COGNITO_STACK_NAME in environment. Reads CRED_PROVIDER_ARN from .env.

Usage:
    uv run python scripts/linkedin-auth-code/deploy_gateway.py
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

GATEWAY_NAME = "linkedin-auth-code-gateway"


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
    role_arn = admin.create_gateway_role(GATEWAY_NAME, oauth_targets=True)

    print("\n--- Creating AgentCore Gateway ---")
    gw_resp = control.create_gateway(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "allowedClients": [gw_client_id],
                "discoveryUrl": discovery_url,
            }
        },
        protocolConfiguration={
            "mcp": {"supportedVersions": ["2025-11-25"], "searchType": "SEMANTIC"}
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

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars["GATEWAY_ID"] = gateway_id
    env_vars["GATEWAY_URL"] = gateway_url
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    print("\n  Saved to .env")


if __name__ == "__main__":
    main()
