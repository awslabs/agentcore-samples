"""Attach a Databricks Apps A2A agent to the gateway as an HTTP passthrough target.

Creates an http.passthrough target (protocolType A2A) pointing at the Databricks
App URL, using the Databricks service-principal OAuth credential provider for
outbound auth. The gateway mints a Databricks token via client_credentials and
attaches it as Authorization: Bearer when forwarding A2A requests to the App.

A2A protocol targets get a default schema, so no schema is provided here.

Requires GATEWAY_ID and CREDENTIAL_PROVIDER_ARN in environment or .env.

Usage:
    uv run python scripts/databricks-a2a-target/deploy_target.py \
      --endpoint "https://<app-name>-<id>.cloud.databricksapps.com"
"""

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

TARGET_NAME = "databricks-a2a"


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


def save_env(updates):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars.update(updates)
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Create an http.passthrough A2A target for a Databricks App"
    )
    parser.add_argument(
        "--endpoint", required=True, help="Databricks App URL (the A2A agent base URL)"
    )
    parser.add_argument(
        "--scopes",
        default="all-apis",
        help="Comma-separated Databricks OAuth scopes (default: all-apis)",
    )
    args = parser.parse_args()

    load_env()
    gateway_id = get_required_env("GATEWAY_ID")
    credential_provider_arn = get_required_env("CREDENTIAL_PROVIDER_ARN")
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    print(f"--- Creating http.passthrough A2A target '{TARGET_NAME}' ---")
    target = control.create_gateway_target(
        name=TARGET_NAME,
        gatewayIdentifier=gateway_id,
        targetConfiguration={
            "http": {
                "passthrough": {
                    "endpoint": args.endpoint,
                    "protocolType": "A2A",
                }
            }
        },
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": credential_provider_arn,
                        "scopes": scopes,
                        "grantType": "CLIENT_CREDENTIALS",
                    }
                },
            }
        ],
    )
    target_id = target["targetId"]
    print(f"  Target ID: {target_id}")

    print("\n  Waiting for target to become READY...")
    while True:
        time.sleep(10)
        t = control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        status = t["status"]
        print(f"    Status: {status}")
        if status in ["READY", "FAILED", "CREATE_FAILED"]:
            break

    save_env({"TARGET_ID": target_id, "TARGET_NAME": TARGET_NAME})
    print("\n  Saved TARGET_ID and TARGET_NAME to .env")


if __name__ == "__main__":
    main()
